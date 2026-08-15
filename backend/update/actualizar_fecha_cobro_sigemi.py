#!/usr/bin/env python3
"""
FUNCIONAL
actualizar_fecha_cobro_sigemi.py
------------------------------
CORRECCIÓN PUNTUAL (no es parte del flujo normal de carga/actualización
DE ACTAS -- para eso, ver la integración en llenar_actas_sigemi.py y
actualizar_estado_sigemi.py, que corren esto mismo como paso final de
cada pasada usando siempre app.reglas.fecha_cobro_sigemi.PATH_PAGOS_SIGEMI):

lee app/datos/maestro/total_pagos_em_sigemi.txt -- un archivo DISTINTO al
que usan llenar_actas_sigemi.py / actualizar_estado_sigemi.py -- que trae,
por cada pago real, el número de acta y su fecha de cobro real. Con eso
corrige `fecha_cobro_sigemi` en la base, que hasta ahora se completaba
sola con la fecha en que se CORRIÓ el script de carga (ver
crud.aplicar_cambios_estado), no con la fecha real del pago -- por eso
estaba mal para todo lo que se cargó de una sola vez bastante después del
pago real.

Formato del archivo (separado por '|', un pago por línea):
    LOTE_TIPO|LOTE_NUMERO|RECIBO|FECHA_COBRO|IMPORTE|JUZGADO|CAUSA_ANIO|
    CAUSA_NUMERO|OFICINA_CODIGO|ACTA_NUMERO|LABRADA_FECHA|PERSONA_DOC|
    INFRACTOR|PADRON

Sólo importan ACTA_NUMERO (índice 9) y FECHA_COBRO (índice 3) -- el resto
de las columnas se ignora. AJUSTAR los índices en
app/reglas/fecha_cobro_sigemi.py si el orden real de columnas resulta
distinto al de este docstring (ahora viven ahí, junto con el resto de la
lógica reutilizable -- ver ese módulo).

Qué hace, por cada línea del archivo de pagos, y qué hace con los pagos
que no impactaron (pagados en Procuración/Municipalidad): sin cambios,
ver el docstring de app/reglas/fecha_cobro_sigemi.py -- toda esa lógica
se movió ahí tal cual estaba, este script ahora es sólo el CLI.

Por defecto corre en DRY-RUN (no graba). Para que grabe de verdad:
    cd backend
    python update/actualizar_fecha_cobro_sigemi.py --commit

Por defecto usa app.reglas.fecha_cobro_sigemi.PATH_PAGOS_SIGEMI (la misma
ruta que se usa siempre a mano). Para usar otro archivo puntualmente:
    python update/actualizar_fecha_cobro_sigemi.py otro_archivo.txt --commit
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.database import SessionLocal  # AJUSTAR si el factory de sesión tiene otro nombre/ubicación
from app import models
from app.reglas.fecha_cobro_sigemi import PATH_PAGOS_SIGEMI, corregir_fechas


def main():
    parser = argparse.ArgumentParser(
        description="Corrige fecha_cobro_sigemi usando la fecha real de pago desde el archivo de pagos SIGEMI."
    )
    parser.add_argument(
        "entrada",
        nargs="?",
        default=PATH_PAGOS_SIGEMI,
        help=f"Archivo de pagos (txt, separado por '|'). Default: {PATH_PAGOS_SIGEMI}",
    )
    parser.add_argument("--commit", action="store_true", help="Graba los cambios en la base (sin esto, dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        resumen = corregir_fechas(db, models, args.entrada, commit=args.commit)

        if args.commit:
            db.commit()
        else:
            db.rollback()

        modo = "COMMIT" if args.commit else "DRY-RUN (no se grabó nada)"
        print(f"\nModo: {modo}")
        for clave, valor in resumen.items():
            print(f"  {clave}: {valor}")
        if resumen["fecha_cobro_posibles_inconsistencias_de_estado"] > 0:
            print(
                "\n⚠️  Hay actas con pago registrado en el archivo de pagos pero cuyo estado_sigemi "
                "en la base no refleja que estén pagadas. Revisar el detalle arriba."
            )
        if resumen["fecha_cobro_sin_impacto_pagada_sin_archivar_revisar"] > 0:
            print(
                "\n⚠️  Hay actas en estado 'Pago Voluntario' (no archivadas) sin fecha real de cobro "
                "(pagadas en Procuración/Municipalidad, no impactaron). Se les vació fecha_cobro_sigemi "
                "pero no se les puso motivo_archivo_sigemi porque no están archivadas. Revisar el "
                "detalle arriba y decidir si corresponde archivarlas."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()