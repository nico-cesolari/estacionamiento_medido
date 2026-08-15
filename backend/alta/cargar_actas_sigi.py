"""
POR PROBAR PERO TODO INDICA QUE FUNCIONA, EJECUTAR LUEGO DE CARGAR TODAS LAS DE SEMYT
Alta de actas nuevas desde SIGI:
recorre TODA la tabla de SIGI (filtrada por Tipo de acta = Estacionamiento
Medido, 50 filas por página), fila por fila, y compara el Nº de acta de
cada fila contra TODAS las actas que ya existen en la base local (tengan
o no expediente cargado, estén archivadas o no -- da igual, alcanza con
que la acta ya esté para saltear la fila).

Si el acta YA está en la base -> se saltea. Si NO está -> se da de alta
un registro nuevo con expediente (de la grilla), acta/patente/dirección/
fecha (del detalle) y estado_sigi según la columna ESTADO de la grilla.
estado_semyt queda siempre en 'Eliminada' (ver
reglas_sigi.crear_registro_nuevo_por_acta). Cada alta se comitea al
instante.

A diferencia de llenar_actas_sigi.py, acá NO hay "pendientes" que agotar:
se recorre la grilla completa siempre (no hay forma de saber de antemano
cuántas actas nuevas puede haber).

La mecánica de Playwright (paginación, apertura de detalle, reintentos)
vive en sistemas/sigi/web/web_sigi.py -- ver ese módulo para el detalle.

Uso standalone:
    cd backend
    python alta/cargar_actas_sigi.py
    python alta/cargar_actas_sigi.py --commit
    caffeinate -i python alta/cargar_actas_sigi.py --commit
También se puede importar `ejecutar_alta(db, page)` desde otro script
(ver backend/app/pasos/procesar_actas_sigi.py) para correrlo dentro de
una sesión de navegador ya abierta.
"""
import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.database import SessionLocal
from app.pasos.navegador import PaginaConSesion
from app.services.sistemas.sigi.reglas import reglas_sigi
from app.services.sistemas.sigi.web import web_sigi

URL_SIGI = "https://juzgado.villamaria.gob.ar/juzgado"
ARCHIVO_SESION = "sesion_sigi.json"


async def _procesar_una_fila(
    db: Session,
    page,
    pagina_actual: int,
    fila_idx: int,
    idx_expediente,
    idx_estado,
    actas_conocidas: set,
    commit: bool,
) -> int:
    filas = page.locator(web_sigi.SELECTOR_FILAS_RESULTADO)
    fila = filas.nth(fila_idx)
    await fila.scroll_into_view_if_needed()

    valores = await web_sigi.leer_celdas_con_reintento(
        fila, {"expediente": idx_expediente, "estado": idx_estado}
    )
    expediente_fila, estado_fila = valores["expediente"], valores["estado"]

    if expediente_fila is None and estado_fila is None:
        raise TimeoutError("fila sin datos aún")

    web_sigi.log("FILA", f"Página {pagina_actual}, fila {fila_idx + 1}: "
                          f"expediente={expediente_fila}, estado={estado_fila} -> abriendo detalle...")

    if not await web_sigi.abrir_detalle_de_fila(page, fila):
        return 0

    numero_acta = await web_sigi.leer_numero_acta(page)
    acta_norm = reglas_sigi.normalizar_acta(numero_acta) if numero_acta else None
    alta_nueva = 0

    if not numero_acta:
        web_sigi.log("FILA", "  ❌ no se pudo leer el Nº de acta en el detalle -- salteo esta fila")

    elif acta_norm in actas_conocidas:
        web_sigi.log("FILA", f"  ↩ acta {numero_acta} ya existe en la base -- salteo")

    else:
        # Los datos extra (patente/dirección/fecha) se leen del mismo
        # texto plano que ya trajo leer_numero_acta -- reusamos las
        # reglas de sigi para extraerlos sin volver a golpear la página.
        texto_detalle = await page.locator("body").inner_text()
        patente_leida = reglas_sigi.extraer_patente_de_texto(texto_detalle)
        direccion_leida = reglas_sigi.extraer_direccion_de_texto(texto_detalle)
        fecha_hora_leida = reglas_sigi.extraer_fecha_hora_de_texto(texto_detalle)

        if not patente_leida:
            web_sigi.log("FILA", f"  ⚠️ acta {numero_acta} es nueva pero no se pudo leer la patente "
                                  f"(campo obligatorio) -- salteo esta fila, no se da de alta.")
        else:
            motivo_texto = await web_sigi.leer_primer_texto(page.locator(web_sigi.SELECTOR_MOTIVO_EN_DETALLE))
            if commit:
                reglas_sigi.crear_registro_nuevo_por_acta(
                    db,
                    expediente=expediente_fila,
                    acta=numero_acta,
                    patente=patente_leida,
                    direccion=direccion_leida,
                    fecha_hora=fecha_hora_leida,
                    estado_texto=estado_fila,
                    motivo_texto=motivo_texto,
                )
                db.commit()
                web_sigi.log("FILA", f"  ✅ ALTA acta {numero_acta} (expediente {expediente_fila}, "
                                      f"patente {patente_leida}) -- guardada en DB.")
            else:
                web_sigi.log("FILA", f"  (dry-run) ALTA acta {numero_acta} (expediente {expediente_fila}, "
                                      f"patente {patente_leida}, dirección {direccion_leida}, "
                                      f"fecha {fecha_hora_leida}, estado {estado_fila}) -- NO se graba.")
            actas_conocidas.add(acta_norm)
            alta_nueva = 1

    await web_sigi.cerrar_detalle(page)
    return alta_nueva


async def ejecutar_alta(db: Session, page, commit: bool = True) -> dict:
    modo = "COMMIT (graba de verdad)" if commit else "DRY-RUN (no toca la base)"
    web_sigi.log("INICIO", f"Modo: {modo}")

    actas_conocidas = set(reglas_sigi.todas_las_actas_conocidas(db).keys())
    web_sigi.log("INICIO", f"{len(actas_conocidas)} acta(s) ya conocida(s) en la base "
                            f"(se saltean si aparecen en la grilla)")

    await web_sigi.preparar_grilla(page)
    idx_expediente, idx_estado = await web_sigi.indices_expediente_estado(page)

    async def procesar_fila(pagina_actual: int, fila_idx: int) -> int:
        async def intento_unico():
            return await _procesar_una_fila(
                db, page, pagina_actual, fila_idx, idx_expediente, idx_estado,
                actas_conocidas, commit,
            )
        return await web_sigi.con_reintento_de_fila(page, pagina_actual, fila_idx, intento_unico)

    # Sin `debe_continuar`: se recorre la grilla completa siempre (no hay
    # "pendientes" que agotar, cualquier fila puede resultar en un alta).
    altas = await web_sigi.recorrer_grilla(page, procesar_fila, direccion="adelante")

    resumen = {"altas_nuevas": altas}
    web_sigi.log("RESUMEN", str(resumen))
    return resumen


async def _main(commit: bool):
    db = SessionLocal()
    try:
        async with PaginaConSesion(ARCHIVO_SESION, URL_SIGI) as page:
            resumen = await ejecutar_alta(db, page, commit=commit)
        web_sigi.log("FIN", str(resumen))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                         help="Graba en la DB de verdad. Sin este flag, corre en dry-run.")
    args = parser.parse_args()
    asyncio.run(_main(commit=args.commit))