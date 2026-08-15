"""
FUNCIONAL
Alta de expediente / estado en SIGI:
recorre TODA la tabla de SIGI (filtrada por Tipo de acta = Estacionamiento
Medido, 50 filas por página), fila por fila, y compara el Nº de acta de
cada fila contra el conjunto de actas pendientes en la base local. Apenas
encuentra una coincidencia, persiste el cambio en la DB al instante
(commit inmediato) y saca ese registro de la lista de pendientes.

Toda la mecánica de Playwright (paginación, apertura de detalle, lectura
de fila, reintentos) vive en sistemas/sigi/web/web_sigi.py -- este script
sólo aporta QUÉ hacer con cada fila. Antes esa mecánica estaba copiada acá
mismo (y en llenar_actas_sigi_reverso.py y cargar_actas_sigi.py); ahora
un fix ahí adentro se ve en los 4 scripts a la vez.

Si el script se corta a mitad de camino, lo ya encontrado hasta ese punto
queda guardado (commit inmediato por match) -- se puede volver a correr y
arranca de nuevo desde los que sigan sin expediente.

Uso standalone:
    cd backend
    python alta/llenar_actas_sigi.py
    python alta/llenar_actas_sigi.py --commit
    caffeinate -i python alta/llenar_actas_sigi.py --commit
También se puede importar `ejecutar_alta(db, page)` desde otro script
(ver backend/app/pasos/procesar_actas_sigi.py) para correrlo dentro de
una sesión de navegador ya abierta, junto con la actualización.
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
    pendientes: dict,
    encontrados_por_acta: dict,
    commit: bool,
    expedientes_conocidos: set,
) -> int:
    """Un único intento de procesar la fila `fila_idx`. Se re-ubica todo
    DESDE CERO acá adentro -- lo que permite que web_sigi.con_reintento_de_fila
    lo reintente sin arrastrar locators viejos."""
    filas = page.locator(web_sigi.SELECTOR_FILAS_RESULTADO)
    fila = filas.nth(fila_idx)
    await fila.scroll_into_view_if_needed()

    valores = await web_sigi.leer_celdas_con_reintento(
        fila, {"expediente": idx_expediente, "estado": idx_estado}
    )
    expediente_fila, estado_fila = valores["expediente"], valores["estado"]

    if expediente_fila is None and estado_fila is None:
        # Fila todavía sin poblar (placeholder de carga tras 'Volver').
        # Lo tratamos como "timeout" para que el motor de reintentos lo
        # reintente en vez de contarlo como una fila sin match.
        raise TimeoutError("fila sin datos aún")

    expediente_norm = reglas_sigi.normalizar_expediente(expediente_fila)
    if expediente_norm and expediente_norm in expedientes_conocidos:
        web_sigi.log("FILA", f"Página {pagina_actual}, fila {fila_idx + 1}: expediente "
                              f"{expediente_fila} ya está cargado -- salteo sin abrir detalle.")
        return 0

    web_sigi.log("FILA", f"Página {pagina_actual}, fila {fila_idx + 1}: "
                          f"expediente={expediente_fila}, estado={estado_fila} -> abriendo detalle...")

    if not await web_sigi.abrir_detalle_de_fila(page, fila):
        return 0

    numero_acta = await web_sigi.leer_numero_acta(page)
    acta_norm = reglas_sigi.normalizar_acta(numero_acta) if numero_acta else None
    nuevos_encontrados = 0

    if not numero_acta:
        web_sigi.log("FILA", "  ❌ no se pudo leer el Nº de acta en el detalle")

    elif acta_norm and acta_norm in pendientes:
        registro = pendientes.pop(acta_norm)
        motivo_texto = await web_sigi.leer_primer_texto(page.locator(web_sigi.SELECTOR_MOTIVO_EN_DETALLE))
        cambios = reglas_sigi.armar_cambios_estado(estado_fila, motivo_texto)

        if commit:
            registro.expediente = expediente_fila
            if cambios:
                reglas_sigi.aplicar_actualizacion(db, registro, cambios)
            else:
                db.add(registro)
            db.commit()
            web_sigi.log("FILA", f"  ✅ MATCH acta {numero_acta} -> patente {registro.patente} "
                                  f"(expediente {expediente_fila}). Faltan {len(pendientes)}.")
        else:
            web_sigi.log("FILA", f"  (dry-run) MATCH acta {numero_acta} -> patente {registro.patente} "
                                  f"(expediente {expediente_fila}), NO se graba. Faltan {len(pendientes)}.")
        nuevos_encontrados = 1
        encontrados_por_acta[acta_norm] = {"base": registro, "expedientes": {expediente_fila}}

    elif acta_norm and acta_norm in encontrados_por_acta:
        info_acta = encontrados_por_acta[acta_norm]
        ya_guardados = info_acta["expedientes"]
        if expediente_fila not in ya_guardados:
            motivo_texto = await web_sigi.leer_primer_texto(page.locator(web_sigi.SELECTOR_MOTIVO_EN_DETALLE))
            cambios = reglas_sigi.armar_cambios_estado(estado_fila, motivo_texto)
            if commit:
                nuevo_registro = reglas_sigi.clonar_registro(info_acta["base"])
                nuevo_registro.expediente = expediente_fila
                if cambios:
                    reglas_sigi.aplicar_actualizacion(db, nuevo_registro, cambios)
                else:
                    db.add(nuevo_registro)
                db.commit()
                web_sigi.log("FILA", f"  ✅ registro nuevo para acta {numero_acta} (expediente {expediente_fila})")
            else:
                web_sigi.log("FILA", f"  (dry-run) se guardaría un registro nuevo para acta {numero_acta}")
            ya_guardados.add(expediente_fila)
    else:
        web_sigi.log("FILA", f"  ↩ acta {numero_acta} no está entre las pendientes")

    await web_sigi.cerrar_detalle(page)
    return nuevos_encontrados


async def ejecutar_alta(db: Session, page, commit: bool = True) -> dict:
    """Núcleo del paso de alta. commit=False corre en dry-run: no toca la DB."""
    modo = "COMMIT (graba de verdad)" if commit else "DRY-RUN (no toca la base)"
    web_sigi.log("INICIO", f"Modo: {modo}")

    registros_con_patente = [r for r in reglas_sigi.registros_sin_expediente(db) if r.patente]
    sin_patente_total = len(reglas_sigi.registros_sin_expediente(db)) - len(registros_con_patente)
    pendientes = {reglas_sigi.normalizar_acta(r.acta): r for r in registros_con_patente}
    total_inicial = len(pendientes)
    web_sigi.log("INICIO", f"{total_inicial} acta(s) pendiente(s) con patente "
                            f"({sin_patente_total} sin patente, van directo a sin_coincidencia)")

    expedientes_conocidos = set(reglas_sigi.todos_los_expedientes_cargados(db).keys())
    web_sigi.log("INICIO", f"{len(expedientes_conocidos)} expediente(s) ya cargado(s) en la base")

    await web_sigi.preparar_grilla(page)

    idx_expediente, idx_estado = await web_sigi.indices_expediente_estado(page)
    encontrados_por_acta: dict = {}

    async def procesar_fila(pagina_actual: int, fila_idx: int) -> int:
        async def intento_unico():
            return await _procesar_una_fila(
                db, page, pagina_actual, fila_idx, idx_expediente, idx_estado,
                pendientes, encontrados_por_acta, commit, expedientes_conocidos,
            )
        return await web_sigi.con_reintento_de_fila(page, pagina_actual, fila_idx, intento_unico)

    await web_sigi.recorrer_grilla(
        page, procesar_fila, direccion="adelante",
        debe_continuar=lambda: bool(pendientes),
    )

    sin_coincidencia = sin_patente_total
    for registro in pendientes.values():
        if commit:
            web_sigi.log("SIN-COINCIDENCIA", f"acta {registro.acta} -> se marca 'no_cargada'")
            if reglas_sigi.marcar_sin_coincidencia(db, registro):
                db.commit()
        else:
            web_sigi.log("SIN-COINCIDENCIA", f"(dry-run) acta {registro.acta} -> se marcaría 'no_cargada'")
        sin_coincidencia += 1

    resumen = {
        "altas_expediente": total_inicial - len(pendientes),
        "actualizados": total_inicial - len(pendientes),
        "sin_cambios": 0,
        "sin_coincidencia": sin_coincidencia,
        "errores": 0,
    }
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