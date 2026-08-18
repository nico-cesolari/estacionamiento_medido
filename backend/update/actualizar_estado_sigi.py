#!/usr/bin/env python3
"""
update/actualizar_estado_sigi.py
------------------------------
Actualiza estado_sigi de las actas que YA tienen expediente asignado y
cuyo estado todavía puede cambiar (se excluyen las Archivado -- estado
terminal -- y las No Cargada -- sin info real -- y, como caso extremo,
cualquiera con estado_sigi nulo; ver
reglas_sigi.registros_con_expediente_pendientes).

A DIFERENCIA de la versión anterior (que recorría TODA la grilla de SIGI
página por página comparando expedientes), esto busca cada expediente
UNO POR UNO usando la misma estrategia robusta que
alta/llenar_actas_sigi.py::buscar() -- filtro + Enter, verificación de
que el filtro realmente se aplicó (por el contador de resultados),
recorrido de todas las páginas del resultado filtrado, y sólo acepta una
fila con match EXACTO de expediente. El recorrido rápido de grilla podía
leer una fila todavía no repintada y pescar coincidencias erróneas -- acá
se prioriza que ande bien por sobre que sea rápido, con una pausa real
entre búsqueda y búsqueda (--delay).

No hace alta de expedientes nuevos (eso lo sigue haciendo
alta/llenar_actas_sigi.py, que ya busca individualmente por número de
expediente ascendente).

USO:
    cd backend
    python update/actualizar_estado_sigi.py                  # dry-run
    python update/actualizar_estado_sigi.py --commit
    python update/actualizar_estado_sigi.py --commit --limit 20   # probar con pocos
    python update/actualizar_estado_sigi.py --commit --delay 3    # más lento, más seguro
    caffeinate -i python update/actualizar_estado_sigi.py --commit
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.sistemas.sigi.reglas import reglas_sigi
from app.services.sistemas.comun.sesion import PaginaConSesion
from app.paths import CARPETA_SESIONES_API_REST_PAYMENT
from app.database import SessionLocal

from app.services.sistemas.sigi.web import web_sigi
from app.services.sistemas.sigi.web.web_sigi import log

from alta.llenar_actas_sigi import URL_SIGI, ARCHIVO_SESION, buscar as buscar_expediente


async def _procesar_expediente_pendiente(
    db: Session,
    page,
    registro,
    idx_expediente: int,
    idx_estado: Optional[int],
    commit: bool,
    primera_busqueda_global: bool,
) -> str:
    """
    Busca el expediente de `registro` individualmente (misma estrategia
    que llenar_actas_sigi.buscar: reintentos + verificación de match
    exacto). Devuelve 'actualizado' | 'sin_cambios' | 'no_encontrado' | 'error'.

    NOTA: igual que en la versión anterior (camino 1), acá NO se abre el
    detalle -- el estado se lee directo de la columna de la grilla, así
    que si el expediente pasó a 'Archivado' en esta misma corrida, el
    motivo de archivo va a quedar sin cargar (necesita el detalle para
    leerlo). Mismo comportamiento de siempre, no es una regresión nueva.
    """
    try:
        fila = await buscar_expediente(
            page, registro.expediente, idx_expediente,
            primera_busqueda_global=primera_busqueda_global,
        )
    except Exception as exc:  # noqa: BLE001
        log("EXPEDIENTE", f"❌ Error buscando expediente {registro.expediente} "
                           f"(acta {registro.acta}): {exc}")
        return "error"

    if fila is None:
        log("EXPEDIENTE", f"↩ expediente {registro.expediente} (acta {registro.acta}): "
                           f"no apareció en SIGI -- se desvincula (vuelve a 'No Cargada' "
                           f"para que llenar_actas_sigi.py lo re-busque más adelante).")
        if commit:
            if reglas_sigi.desvincular_expediente_no_encontrado(db, registro):
                db.commit()
        else:
            log("EXPEDIENTE", "  (dry-run) NO se graba la desvinculación")
        return "no_encontrado"

    valores = await web_sigi.leer_celdas_con_reintento(fila, {"estado": idx_estado})
    estado_fila = valores.get("estado")

    cambios = reglas_sigi.armar_cambios_estado(estado_fila)
    if cambios is None:
        log("EXPEDIENTE", f"  ⚠️ expediente {registro.expediente}: estado '{estado_fila}' "
                           f"no se pudo mapear")
        return "error"

    if not reglas_sigi.hay_cambio_real(registro, cambios):
        return "sin_cambios"

    if commit:
        reglas_sigi.aplicar_actualizacion(db, registro, cambios)
        db.commit()
        log("EXPEDIENTE", f"  ✅ expediente {registro.expediente} (acta {registro.acta}): "
                           f"estado actualizado a {cambios.get('estado_sigi')}")
    else:
        log("EXPEDIENTE", f"  (dry-run) expediente {registro.expediente} (acta {registro.acta}): "
                           f"pasaría a {cambios.get('estado_sigi')}, NO se graba")
    return "actualizado"


async def ejecutar_actualizacion_estado(
    db: Session,
    page,
    commit: bool = True,
    delay: float = 2.0,
    limite: Optional[int] = None,
) -> dict:
    modo = "COMMIT (graba de verdad)" if commit else "DRY-RUN (no toca la base)"
    log("INICIO", f"Modo: {modo}")

    registros = reglas_sigi.registros_con_expediente_pendientes(db)
    log("INICIO", f"{len(registros)} expediente(s) con estado activo (ni Archivado, ni "
                  f"No Cargada, ni sin estado) -- se re-consultan UNO POR UNO, pausado "
                  f"(--delay {delay}s), para no repetir el bug de coincidencias erróneas "
                  f"del recorrido rápido de grilla.")

    if limite:
        registros = registros[:limite]
        log("LIMITE", f"Recortado a {len(registros)} por --limit.")

    await web_sigi.filtrar_por_tipo_acta_estacionamiento(page)
    idx_expediente, idx_estado = await web_sigi.indices_expediente_estado(page)
    if idx_expediente is None:
        raise RuntimeError(
            "No se encontró la columna 'Expediente' en la grilla de SIGI -- revisar "
            "TEXTO_HEADER_EXPEDIENTE / SELECTOR_HEADERS_TABLA en web_sigi.py."
        )

    contadores = {"actualizados": 0, "sin_cambios": 0, "no_encontrados": 0, "errores": 0}
    _clave_por_resultado = {
        "actualizado": "actualizados",
        "sin_cambios": "sin_cambios",
        "no_encontrado": "no_encontrados",
        "error": "errores",
    }

    primera_busqueda = True
    total = len(registros)
    for i, registro in enumerate(registros, start=1):
        if not primera_busqueda and delay > 0:
            await asyncio.sleep(delay)
        es_primera = primera_busqueda
        primera_busqueda = False

        resultado = await _procesar_expediente_pendiente(
            db, page, registro, idx_expediente, idx_estado, commit,
            primera_busqueda_global=es_primera,
        )
        contadores[_clave_por_resultado[resultado]] += 1

        if i % 25 == 0 or i == total:
            log("PROGRESO", f"{i}/{total} procesados: {contadores}")

    log("RESUMEN", str(contadores))
    return contadores


async def _main(commit: bool, delay: float, limite: Optional[int]):
    db = SessionLocal()
    try:
        async with PaginaConSesion(
            ARCHIVO_SESION, URL_SIGI,
            carpeta_sesiones=CARPETA_SESIONES_API_REST_PAYMENT,
        ) as page:
            resumen = await ejecutar_actualizacion_estado(
                db, page, commit=commit, delay=delay, limite=limite
            )
        log("FIN", str(resumen))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Actualiza estado_sigi de expedientes ya cargados, buscando cada uno "
                    "individualmente (pausado, para evitar coincidencias erróneas)."
    )
    parser.add_argument("--commit", action="store_true",
                         help="Graba en la DB de verdad. Sin este flag, corre en dry-run.")
    parser.add_argument(
        "--delay", type=float, default=2.0,
        help="Segundos de espera entre expediente y expediente antes de la siguiente "
             "búsqueda (default: 2.0). A propósito no es instantáneo: leer la grilla "
             "demasiado rápido es lo que generaba coincidencias erróneas. --delay 0 "
             "desactiva la espera (no recomendado)."
    )
    parser.add_argument("--limit", type=int, default=None,
                         help="Probar con sólo N expedientes en esta corrida.")
    args = parser.parse_args()
    asyncio.run(_main(commit=args.commit, delay=args.delay, limite=args.limit))