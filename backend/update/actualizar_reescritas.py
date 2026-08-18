# scripts/backfill_relaciones.py
from app.database import SessionLocal
from app.services.duplicados import calcular_actas_reescritas, calcular_actas_duplicadas
"""
python update/actualizar_reescritas.py
"""
def main():
    db = SessionLocal()
    try:
        print("Recalculando reescritas...")
        print(calcular_actas_reescritas(db))
        print("Recalculando duplicadas...")
        print(calcular_actas_duplicadas(db))
    finally:
        db.close()

if __name__ == "__main__":
    main()