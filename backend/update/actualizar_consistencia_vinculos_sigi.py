#!/usr/bin/env python3
"""
actualizar_consistencia_vinculos_sigi.py
------------------------------------------
Recalcula VinculoSigi.consistente para TODOS los vínculos existentes,
usando la lógica real (sigi_vinculos.recalcular_consistencia_vinculo:
categorías SEMyT/SIGEMI + este expediente SIGI puntual). Hace falta
correrlo después de:
  - la migración inicial (Parte 1: los vínculos migrados desde las
    columnas viejas de Registro quedaron con consistente=NULL a
    propósito, ver el INSERT de la migración).
  - cualquier corrección manual en la base (UPDATE directo por SQL)
    que haya tocado estado_semyt/estado_sigemi/estado_sigi sin pasar
    por aplicar_cambios_estado / actualizar_vinculo.

También aplica la regla "sin 2+ expedientes no-nulos, no se marca
duplicada/reescrita": antes de recalcular consistencia, resetea a
'directo' cualquier vínculo cuyo registro haya quedado con un solo
vínculo activo. Es la misma regla que el SQL de pgAdmin4, hecha acá
también para que este script sea idempotente y autosuficiente si se
corre solo, sin depender de haber corrido el SQL antes.

USO:
    cd backend
    python update/actualizar_consistencia_vinculos_sigi.py            # dry-run
    python update/actualizar_consistencia_vinculos_sigi.py --commit
"""
import argparse
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.database import SessionLocal
from app.models import models
from app.services.sigi_vinculos import recalcular_consistencia_vinculo


def actualizar(db, commit: bool) -> dict:
    vinculos = db.query(models.VinculoSigi).all()

    # Agrupar por registro para poder aplicar la regla "1 solo vínculo -> directo"
    por_registro = defaultdict(list)
    for v in vinculos:
        por_registro[v.registro_id].append(v)

    origen_corregido = 0
    consistencia_recalculada = 0
    sin_cambios = 0

    for registro_id, vinculos_del_registro in por_registro.items():
        if len(vinculos_del_registro) <= 1:
            for v in vinculos_del_registro:
                if v.origen != "directo":
                    print(f"[ORIGEN] vínculo id={v.id} (registro_id={registro_id}, "
                          f"expediente={v.expediente}): {v.origen} -> directo "
                          f"(sólo tiene 1 vínculo, no corresponde marcarlo)")
                    if commit:
                        v.origen = "directo"
                    origen_corregido += 1

        for v in vinculos_del_registro:
            antes = v.consistente
            registro = v.registro  # carga el Registro asociado (lazy, una query por vínculo -- ver nota abajo)
            recalcular_consistencia_vinculo(registro, v)
            if v.consistente != antes:
                print(f"[CONSISTENCIA] vínculo id={v.id} (acta={registro.acta}, "
                      f"expediente={v.expediente}): {antes} -> {v.consistente}")
                consistencia_recalculada += 1
            else:
                sin_cambios += 1

    if commit:
        db.commit()
    else:
        db.rollback()

    return {
        "vinculos_totales": len(vinculos),
        "origen_corregido": origen_corregido,
        "consistencia_recalculada": consistencia_recalculada,
        "sin_cambios": sin_cambios,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Recalcula consistente por vínculo SIGI y corrige origen cuando quedó un solo vínculo."
    )
    parser.add_argument("--commit", action="store_true", help="Graba los cambios (sin esto, dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        resumen = actualizar(db, commit=args.commit)
        modo = "COMMIT" if args.commit else "DRY-RUN (no se grabó nada)"
        print(f"\nModo: {modo}")
        for clave, valor in resumen.items():
            print(f"  {clave}: {valor}")
    finally:
        db.close()


if __name__ == "__main__":
    main()