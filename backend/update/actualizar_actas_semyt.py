#!/usr/bin/env python3
"""
FUNCIONAL
actualizar_actas_semyt.py
---------------------------
Corre SOLO la parte de "actualizar estado" del flujo de SEMyT (la parte 2
de app/pasos/procesar_actas_semyt.py), sin la parte 1 (crear actas
nuevas del día). Útil para correr manualmente cuando querés refrescar
estados sin disparar también la búsqueda de altas del día.

Reutiliza las funciones ya existentes en procesar_actas_semyt.py
(_registros_pendientes, _leer_acta, MAPA_ESTADO_SEMYT, etc.) en vez de
duplicar la lógica -- si el día de mañana cambia el selector o el mapeo
de estados allá, este script lo hereda automáticamente.

Por defecto corre en DRY-RUN (no escribe en la DB). Para grabar de
verdad, pasale --commit.

USO:
    cd backend
    python update/actualizar_actas_semyt.py                     # dry-run
    python update/actualizar_actas_semyt.py --commit
    python update/actualizar_actas_semyt.py --commit --limit 50  # probar con pocas
    python update/actualizar_actas_semyt.py --commit --delay 3   # más lento, más seguro
"""
import argparse
import asyncio
import sys
from pathlib import Path

from app.models import models
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from typing import Optional
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.services.estados import aplicar_cambios_estado
from app.pasos.navegador import PaginaConSesion
from app.reglas.reglas_semyt import (
    ESTADOS_IGNORADOS_SEMYT, MAPA_ESTADO_SEMYT, ESTADO_PAGADA_EN_JUZGADO,
    pagada_en_juzgado_con_datos,
)

from app.pasos.procesar_actas_semyt import (
    ARCHIVO_SESION,
    URL_SEMYT,
    _leer_acta,
    _registros_pendientes,
)

def _numero_acta(registro) -> int:
    """Convierte registro.acta a int para poder ordenar numéricamente
    (si el campo es string, ordenar sin esto daría orden alfabético:
    "10" antes que "9")."""
    try:
        return int(registro.acta)
    except (TypeError, ValueError):
        # Si alguna acta no es puramente numérica, la mandamos al final
        # en vez de romper el sort.
        return sys.maxsize


async def actualizar_actas_semyt(
    db: Session, commit: bool, limite: Optional[int], delay: float = 2.0
):
    registros_pendientes = _registros_pendientes(db)

    registros_pendientes = [
        r for r in registros_pendientes
        if r.estado_semyt == models.EstadoSemyt.vencida
    ]
    registros_pendientes.sort(key=_numero_acta)

    if limite:
        registros_pendientes = registros_pendientes[:limite]

    actualizados, sin_cambios, ignorados, no_encontrados, errores = 0, 0, 0, 0, 0

    async with PaginaConSesion(ARCHIVO_SESION, URL_SEMYT) as page:
        primera_iteracion = True
        for registro in registros_pendientes:
            if not primera_iteracion and delay > 0:
                await asyncio.sleep(delay)
            primera_iteracion = False

            try:
                datos_acta = await _leer_acta(page, registro.acta)
            except Exception as exc:  
                print(f"[SEMyT] Error leyendo acta {registro.acta}: {exc}")
                errores += 1
                continue

            if datos_acta is None:
                print(f"[SEMyT] Acta {registro.acta} no encontrada en la grilla")
                no_encontrados += 1
                continue

            estado_texto = datos_acta["estado"]

            if estado_texto in ESTADOS_IGNORADOS_SEMYT:
                ignorados += 1
                continue

            if estado_texto == ESTADO_PAGADA_EN_JUZGADO and pagada_en_juzgado_con_datos(
                datos_acta["vencimiento"], datos_acta["importe"]
            ):
                ignorados += 1
                continue

            nuevo_estado = MAPA_ESTADO_SEMYT.get(estado_texto)
            if nuevo_estado is None:
                print(f"[SEMyT] Estado desconocido '{estado_texto}' en acta {registro.acta}")
                errores += 1
                continue

            if registro.estado_semyt == nuevo_estado:
                sin_cambios += 1
                continue

            print(f"{'[COMMIT]' if commit else '[DRY-RUN]'} acta {registro.acta}: "
                  f"{registro.estado_semyt} -> {nuevo_estado.value}")

            if commit:
                aplicar_cambios_estado(db, registro, {"estado_semyt": nuevo_estado})
                db.commit()

            actualizados += 1

    if not commit:
        db.rollback()

    return {
        "actualizados": actualizados,
        "sin_cambios": sin_cambios,
        "ignorados_por_estado": ignorados,
        "no_encontrados_en_semyt": no_encontrados,
        "errores": errores,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Actualiza estado_semyt de actas pendientes, sin crear actas nuevas."
    )
    parser.add_argument("--commit", action="store_true", help="Graba en la DB (sin esto, dry-run)")
    parser.add_argument("--limit", type=int, default=None, help="Probar con sólo N actas")
    parser.add_argument(
        "--delay", type=float, default=0.0,
        help="Segundos de espera entre acta y acta antes de buscar la "
             "siguiente (default: 2.0). Subilo si seguís viendo estados "
             "cruzados/equivocados; --delay 0 desactiva la espera."
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        resumen = asyncio.run(
            actualizar_actas_semyt(
                db, commit=args.commit, limite=args.limit, delay=args.delay
            )
        )
        modo = "COMMIT" if args.commit else "DRY-RUN (no se grabó nada)"
        print(f"\nModo: {modo}")
        for clave, valor in resumen.items():
            print(f"  {clave}: {valor}")
    finally:
        db.close()


if __name__ == "__main__":
    main()