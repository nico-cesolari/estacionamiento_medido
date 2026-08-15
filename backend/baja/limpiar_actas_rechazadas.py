#!/usr/bin/env python3
"""
HAY QUE VERLO Y PROBARLO Y ACTUALIZARLO BIEN
limpiar_actas_rechazadas.py
----------------------------
Elimina de la base las actas que:
  - estado_semyt =/ Vencida
  - Y NO tienen expediente, NI causa, NI estado_sigemi, NI estado_sigi.

Es decir: eliminar actas que ya se resolvieron por pago o desestimos si no estan cargadas en SEMyT ni en SIGI

USO:
    cd backend
    python baja/limpiar_actas_rechazadas.py            # dry-run
    python baja/limpiar_actas_rechazadas.py --commit    # borra de verdad
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.database import SessionLocal
from app.models import models

def _vacio(valor) -> bool:
    return valor is None or (isinstance(valor, str) and valor.strip() == "")


def _candidatos(db):
    """Actas Rechazadas en SEMyT sin ningún dato de SIGEMI/SIGI."""
    return (
        db.query(models.Registro)
        .filter(
            models.Registro.estado_semyt == models.EstadoSemyt.rechazada,
            models.Registro.estado_sigemi.is_(None),
            models.Registro.estado_sigi.is_(None),
        )
        .all()
    )

def limpiar(db, commit: bool):
    candidatos = [
        r for r in _candidatos(db)
        if _vacio(r.expediente) and _vacio(r.causa)
    ]

    eliminadas, fotos_borradas, fotos_no_encontradas = 0, 0, 0

    for registro in candidatos:

        print(f"{'[COMMIT]' if commit else '[DRY-RUN]'} acta {registro.acta} "
              f"(patente={registro.patente}, foto={registro.foto_url or '-'})")

        if commit:
            db.delete(registro) 
            eliminadas += 1
        else:
            eliminadas += 1

    if commit:
        db.commit()
    else:
        db.rollback()

    return {
        "actas_encontradas": len(candidatos),
        "actas_eliminadas" if commit else "actas_a_eliminar": eliminadas,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Elimina actas SEMyT Rechazada sin expediente/causa/SIGEMI/SIGI, y su foto."
    )
    parser.add_argument("--commit", action="store_true", help="Borra de verdad (sin esto, dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        resumen = limpiar(db, commit=args.commit)
        modo = "COMMIT (se borró de verdad)" if args.commit else "DRY-RUN (no se borró nada)"
        print(f"\nModo: {modo}")
        for clave, valor in resumen.items():
            print(f"  {clave}: {valor}")
    finally:
        db.close()


if __name__ == "__main__":
    main()