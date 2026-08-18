#!/usr/bin/env python3
"""
llenar_actas_sigi_descendente.py
------------------------------------
Misma estrategia que llenar_actas_sigi.py (buscar por número de expediente,
saltando sin buscar los que ya están en la base), pero recorriendo los
números en sentido DESCENDENTE: arranca en el expediente más reciente de
la grilla (o en --desde, si se pasa) y baja hasta --hasta (o hasta 1, si
no se pasa -- comportamiento histórico).

Toda la mecánica de búsqueda (buscar(), _procesar_expediente_encontrado(),
_armar_expediente(), _descomponer_expediente()) se reutiliza tal cual
desde llenar_actas_sigi.py -- acá sólo cambia el sentido del range().

Uso:
    cd backend
    python alta/llenar_actas_sigi_descendente.py                       # dry-run
    python alta/llenar_actas_sigi_descendente.py --commit
    python alta/llenar_actas_sigi_descendente.py --commit --hasta 173000
    python alta/llenar_actas_sigi_descendente.py --commit --desde 176500 --limit 5
    python alta/llenar_actas_sigi_descendente.py --commit --desde 144123 --hasta 23389
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.database import SessionLocal
from app.services.sistemas.comun.sesion import PaginaConSesion
from app.paths import CARPETA_SESIONES_API_REST_PAYMENT
from app.services.sistemas.sigi.reglas import reglas_sigi
from app.services.sistemas.sigi.web import web_sigi

from alta.llenar_actas_sigi import (
    URL_SIGI,
    ARCHIVO_SESION,
    _armar_expediente,
    _descomponer_expediente,
    _leer_primer_expediente_de_la_grilla,
    _procesar_expediente_encontrado,
    buscar,
)


async def ejecutar_descendente(
    db: Session,
    page,
    commit: bool,
    hasta: Optional[int],
    desde: Optional[int],
    limite: Optional[int],
    delay: float = 1.5,
) -> dict:
    modo = "COMMIT (graba de verdad)" if commit else "DRY-RUN (no toca la base)"
    web_sigi.log("INICIO", f"Modo: {modo} -- recorrido DESCENDENTE")

    await web_sigi.filtrar_por_tipo_acta_estacionamiento(page)
    # Igual que en la versión ascendente: no hace falta tocar el paginado,
    # sólo la primera fila (la más reciente) para saber el expediente máximo.

    idx_expediente, idx_estado = await web_sigi.indices_expediente_estado(page)
    if idx_expediente is None:
        raise RuntimeError("No se encontró la columna 'Expediente' en la grilla.")

    primer_expediente = await _leer_primer_expediente_de_la_grilla(page, idx_expediente)
    descompuesto = _descomponer_expediente(primer_expediente or "")
    if not descompuesto:
        raise RuntimeError(
            f"No se pudo interpretar el primer expediente de la grilla ({primer_expediente!r}); "
            "revisar el formato esperado (EXP-AAAA-NNNNNN) o el índice de columna."
        )
    anio, numero_maximo_grilla = descompuesto

    # --desde permite arrancar más abajo que el máximo real de la grilla
    # (por ejemplo, para retomar una corrida cortada). Si no se pasa, se
    # usa el máximo detectado.
    numero_desde = desde if desde is not None else numero_maximo_grilla
    if numero_desde > numero_maximo_grilla:
        web_sigi.log(
            "INICIO",
            f"⚠️ --desde {numero_desde} es mayor al máximo real de la grilla "
            f"({numero_maximo_grilla}) -- se ajusta a {numero_maximo_grilla}.",
        )
        numero_desde = numero_maximo_grilla

    # --hasta es opcional: si no se pasa, se mantiene el comportamiento
    # histórico (bajar hasta 1, o sea recorrer todo lo que queda).
    numero_hasta = hasta if hasta is not None else 1

    web_sigi.log(
        "INICIO",
        f"Expediente más reciente: EXP-{anio}-{numero_maximo_grilla} -> "
        f"recorriendo DESCENDENTE desde {numero_desde} hasta {numero_hasta}",
    )

    expedientes_conocidos = set(reglas_sigi.todos_los_expedientes_cargados(db).keys())
    actas_conocidas_todas = reglas_sigi.todas_las_actas_conocidas(db)
    actas_conocidas = {acta: regs[0] for acta, regs in actas_conocidas_todas.items() if regs}
    web_sigi.log(
        "INICIO",
        f"{len(expedientes_conocidos)} expediente(s) ya en la base "
        f"(se saltean sin buscar), {len(actas_conocidas)} acta(s) distinta(s) conocidas",
    )

    contadores = {"ya_en_db": 0, "altas": 0, "clones": 0, "no_encontrados": 0, "sin_acta": 0, "errores": 0}
    procesados = 0
    primera_busqueda = True

    for numero in range(numero_desde, numero_hasta - 1, -1):
        if limite is not None and procesados >= limite:
            web_sigi.log("LIMITE", f"Se alcanzó --limit {limite}.")
            break

        expediente_completo = _armar_expediente(anio, numero)
        expediente_norm = reglas_sigi.normalizar_expediente(expediente_completo)

        if expediente_norm in expedientes_conocidos:
            contadores["ya_en_db"] += 1
            procesados += 1
            continue

        if not primera_busqueda and delay > 0:
            await page.wait_for_timeout(int(delay * 1000))
        es_primera_busqueda_global = primera_busqueda
        primera_busqueda = False

        try:
            fila = await buscar(
                page, expediente_completo, idx_expediente,
                primera_busqueda_global=es_primera_busqueda_global,
            )
        except Exception as exc:  # noqa: BLE001
            web_sigi.log("EXPEDIENTE", f"❌ Error buscando {expediente_completo}: {exc}")
            contadores["errores"] += 1
            procesados += 1
            continue

        if fila is None:
            web_sigi.log(
                "EXPEDIENTE",
                f"↩ {expediente_completo}: sin resultados (no existe, o no es "
                f"de tipo Estacionamiento Medido)",
            )
            contadores["no_encontrados"] += 1
            procesados += 1
            continue

        resultado = await _procesar_expediente_encontrado(
            db, page, fila, expediente_completo, idx_estado, actas_conocidas, commit
        )
        if resultado == "alta":
            contadores["altas"] += 1
            expedientes_conocidos.add(expediente_norm)
        elif resultado == "clon":
            contadores["clones"] += 1
            expedientes_conocidos.add(expediente_norm)
        else:
            contadores["sin_acta"] += 1

        procesados += 1
        if procesados % 50 == 0:
            web_sigi.log("PROGRESO", f"{procesados} expediente(s) procesados: {contadores}")

    web_sigi.log("RESUMEN", str(contadores))
    return contadores


async def _main(commit: bool, hasta: Optional[int], desde: Optional[int], limite: Optional[int], delay: float):
    db = SessionLocal()
    try:
        async with PaginaConSesion(
            ARCHIVO_SESION, URL_SIGI, carpeta_sesiones=CARPETA_SESIONES_API_REST_PAYMENT
        ) as page:
            resumen = await ejecutar_descendente(
                db, page, commit=commit, hasta=hasta, desde=desde, limite=limite, delay=delay
            )
        web_sigi.log("FIN", str(resumen))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Carga actas de SIGI buscando por número de expediente, de MAYOR a MENOR, "
                    "salteando sin buscar los que ya están en la base."
    )
    parser.add_argument("--commit", action="store_true", help="Graba en la DB de verdad. Sin este flag, dry-run.")
    parser.add_argument(
        "--desde", type=int, default=None,
        help="Número de expediente donde arrancar el descenso. Si no se pasa, arranca en el "
             "más reciente de la grilla (comportamiento actual).",
    )
    parser.add_argument(
        "--hasta", type=int, default=None,
        help="Número de expediente donde para el descenso, inclusive. Si no se pasa, baja "
             "hasta 1 (default histórico).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Procesa como máximo N expedientes en esta corrida.")
    parser.add_argument(
        "--delay", type=float, default=1.5,
        help="Segundos de espera entre expediente y expediente antes de la siguiente búsqueda "
             "(default: 1.5). --delay 0 la desactiva."
    )
    args = parser.parse_args()
    asyncio.run(_main(commit=args.commit, hasta=args.hasta, desde=args.desde, limite=args.limit, delay=args.delay))