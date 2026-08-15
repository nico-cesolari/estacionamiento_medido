"""
FUNCIONAL
VARIANTE "REVERSO" de llenar_actas_sigi.py: misma lógica de negocio
exacta, pero recorre las páginas de la grilla en orden inverso (de la
última a la primera) delegando en web_sigi.recorrer_grilla(direccion=
"reversa"). Pensado para correrse EN PARALELO con llenar_actas_sigi.py
(uno arranca del principio, el otro del final) contra la misma DB: cada
match hace commit inmediato y saca el registro de "pendientes", así que
dos corridas simultáneas no deberían pisarse -- en el peor caso, ambas
leen el mismo registro como pendiente antes de que la otra lo guarde y
terminan escribiéndolo dos veces (incidente menor: `pendientes` se arma
una sola vez por corrida a partir del estado de la DB en ese momento, no
se refresca fila a fila).

Uso standalone:
    cd backend
    python alta/llenar_actas_sigi_reverso.py
    python alta/llenar_actas_sigi_reverso.py --commit
    caffeinate -i python alta/llenar_actas_sigi_reverso.py --commit
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

# Reutiliza el 100% de la lógica de fila de llenar_actas_sigi.py -- lo
# único distinto entre "adelante" y "reversa" es el orden de páginas, que
# ya vive en web_sigi.recorrer_grilla. Antes esto era ~700 líneas
# copiadas con una sola diferencia real (el sentido del recorrido).
from alta.llenar_actas_sigi import _procesar_una_fila

URL_SIGI = "https://juzgado.villamaria.gob.ar/juzgado"
ARCHIVO_SESION = "sesion_sigi.json"


async def ejecutar_alta(db: Session, page, commit: bool = True) -> dict:
    modo = "COMMIT (graba de verdad)" if commit else "DRY-RUN (no toca la base)"
    web_sigi.log("INICIO", f"Modo (REVERSO): {modo}")

    registros_totales = list(reglas_sigi.registros_sin_expediente(db))
    registros_con_patente = [r for r in registros_totales if r.patente]
    sin_patente_total = len(registros_totales) - len(registros_con_patente)
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
        page, procesar_fila, direccion="reversa",
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