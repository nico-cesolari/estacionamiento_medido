#!/usr/bin/env python3
"""
FUNCIONAL
llenar_actas_sigemi.py
------------------------------
CARGA INICIAL: lee el archivo crudo de SIGEMI (mismo formato/roturas que
antes) y, SÓLO para las actas que YA existen en la base pero TODAVÍA NO
tienen causa y/o estado_sigemi cargados, completa:

  - causa                  <- NUMERO (columna de causa del registro SIGEMI)
  - estado_sigemi          <- reglas de negocio (Juicio / Plan de Pago /
                                Pago Voluntario / Sin Resolución / Juzgado
                                de Faltas), ver sigemi_comun.py
  - motivo_archivo_sigemi  <- sólo si se puede determinar con certeza
                                (pago). Las archivadas sin poder determinar
                                el motivo (ARCHIVO o JUZGADO DE FALTAS sin
                                Pago Voluntario) quedan sin motivo A
                                PROPÓSITO, para elegirlas a mano.

Actas que YA tienen causa Y estado_sigemi cargados de una pasada anterior
NO se tocan acá -- para eso está actualizar_estado_sigemi.py, que sólo
actualiza el estado sin volver a pisar la causa.

Actas que no existen en la base: se ignoran (no se crean).

------------------------------------------------------------------------
PASO FINAL: corrección de fecha_cobro_sigemi
------------------------------------------------------------------------
Al terminar de cargar las actas, esta misma corrida corre además (en la
MISMA transacción) la corrección de fecha_cobro_sigemi contra el archivo
de pagos real -- ver app/reglas/fecha_cobro_sigemi.py y su constante
PATH_PAGOS_SIGEMI (siempre el mismo archivo, no hace falta pasarlo a
mano). Así, cualquier acta que se acaba de cargar como pagada/archivada
por pago ya sale con la fecha de cobro REAL en la misma pasada, en vez de
quedar con la fecha en que se corrió este script hasta que alguien corra
la corrección aparte.

Si en algún caso puntual hace falta usar OTRO archivo de pagos (no el de
siempre), correr update/actualizar_fecha_cobro_sigemi.py aparte con ese
archivo -- este script siempre usa la ruta constante.

USO:
    cd backend
    python alta/llenar_actas_sigemi.py sistemas/sigemi/archivos/total_em_sigemi.txt       # dry-run
    python alta/llenar_actas_sigemi.py sistemas/sigemi/archivos/total_em_sigemi.txt --commit   # graba
"""
import argparse
import sys
from pathlib import Path

from app.models import models
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.database import SessionLocal

from app.services.sistemas.sigemi.reglas.reglas_sigemi import (
    leer_registros_crudos,
    extraer_numero_causa,
    extraer_acta_numero,
    calcular_estado,
    resolver_estado,
)
from app.reglas.fecha_cobro_sigemi import PATH_PAGOS_SIGEMI, corregir_fechas
from app.services.estados import aplicar_cambios_estado

def _ya_tiene_causa_y_estado(registro) -> bool:
    causa_cargada = bool(registro.causa and str(registro.causa).strip())
    return causa_cargada and registro.estado_sigemi is not None


def cargar_actas(db, path_entrada, commit: bool):
    registros_crudos = leer_registros_crudos(path_entrada)

    cargadas = ya_cargadas = no_encontradas = sin_acta_numero = a_revisar = sin_determinar = 0
    resueltas_a_revisar_motivo = archivadas_sin_resolucion = 0

    for raw in registros_crudos:
        acta_numero = extraer_acta_numero(raw)
        if not acta_numero:
            print(f"[SIGEMI-CARGA] ⚠️ no se pudo extraer número de acta del registro: {raw}")
            sin_acta_numero += 1
            continue

        print(f"[SIGEMI-CARGA] ── acta={acta_numero}")

        registro = db.query(models.Registro).filter(models.Registro.acta == acta_numero).first()
        if registro is None:
            print(f"[SIGEMI-CARGA]   ❌ no encontrada en la base")
            no_encontradas += 1
            continue

        print(f"[SIGEMI-CARGA]   ✅ encontrada en DB (id={registro.id}) -- "
              f"causa actual={registro.causa!r}, estado_sigemi actual={registro.estado_sigemi}")

        # Ya pasó por acá antes (tiene causa y estado): no es trabajo de
        # este script, lo deja para actualizar_estado_sigemi.py.
        if _ya_tiene_causa_y_estado(registro):
            print(f"[SIGEMI-CARGA]   (ya tiene causa y estado cargados -- se deja para "
                  f"actualizar_estado_sigemi.py, no se toca)")
            ya_cargadas += 1
            continue

        causa_numero = extraer_numero_causa(raw)
        estado_final = calcular_estado(raw)
        resuelto = resolver_estado(estado_final, models)

        print(f"[SIGEMI-CARGA]   archivo -> causa={causa_numero!r}, estado calculado={estado_final!r}")

        if resuelto is None:
            print(f"[SIGEMI-CARGA] Acta {acta_numero}: estado '{estado_final}' no se pudo determinar; revisar a mano.")
            if causa_numero:
                registro.causa = causa_numero
                cargadas += 1
            sin_determinar += 1
            continue

        nuevo_estado, nuevo_motivo = resuelto

        print(f"[SIGEMI-CARGA]   ✅ se carga: causa={causa_numero!r}, estado_sigemi={nuevo_estado.value}"
              f"{f', motivo_archivo_sigemi={nuevo_motivo.value}' if nuevo_motivo else ''}")

        if causa_numero:
            registro.causa = causa_numero

        # motivo_archivo_sigemi aplica tanto a "Archivada" como a
        # "Resuelta sin Archivar" (ver reglas_sigemi.py, nota RESUELTA
        # SIN ARCHIVAR) -- para cualquier otro estado no se graba.
        cambios_estado = {"estado_sigemi": nuevo_estado}
        if nuevo_estado in (models.EstadoSigemi.archivada, models.EstadoSigemi.resuelta_sin_archivo) and nuevo_motivo is not None:
            cambios_estado["motivo_archivo_sigemi"] = nuevo_motivo
        aplicar_cambios_estado(db, registro, cambios_estado)

        if estado_final == "ARCHIVADA - REVISAR MOTIVO":
            a_revisar += 1
        elif estado_final == "ARCHIVADA SIN RESOLUCION":
            archivadas_sin_resolucion += 1
        elif nuevo_estado == models.EstadoSigemi.resuelta_sin_archivo and nuevo_motivo is None:
            resueltas_a_revisar_motivo += 1

        cargadas += 1

    resumen = {
        "cargadas": cargadas,
        "ya_tenian_causa_y_estado": ya_cargadas,
        "no_encontradas_en_db": no_encontradas,
        "sin_acta_numero_en_archivo": sin_acta_numero,
        "archivadas_a_revisar_motivo": a_revisar,
        "archivadas_sin_resolucion": archivadas_sin_resolucion,
        "resueltas_sin_archivar_a_revisar_motivo": resueltas_a_revisar_motivo,
        "estado_no_determinado": sin_determinar,
    }

    # Paso final, misma transacción: corrige fecha_cobro_sigemi contra el
    # archivo de pagos real (ver docstring del módulo, arriba). No hace
    # commit/rollback acá -- eso lo maneja main(), una sola vez, al final
    # de toda la corrida.
    print(f"\n[SIGEMI-CARGA] === paso final: corrigiendo fecha_cobro_sigemi contra {PATH_PAGOS_SIGEMI} ===")
    resumen_fecha_cobro = corregir_fechas(db, models, PATH_PAGOS_SIGEMI, commit=commit)
    resumen.update(resumen_fecha_cobro)

    return resumen


def main():
    parser = argparse.ArgumentParser(
        description="Carga inicial de causa y estado_sigemi para actas que todavía no los tienen."
    )
    parser.add_argument("entrada", help="Archivo de entrada (txt, separado por '|')")
    parser.add_argument("--commit", action="store_true", help="Graba los cambios en la base (sin esto, dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        resumen = cargar_actas(db, args.entrada, commit=args.commit)

        if args.commit:
            db.commit()
        else:
            db.rollback()

        modo = "COMMIT" if args.commit else "DRY-RUN (no se grabó nada)"
        print(f"\nModo: {modo}")
        for clave, valor in resumen.items():
            print(f"  {clave}: {valor}")
        if resumen["archivadas_a_revisar_motivo"] > 0:
            print(
                "\n⚠️  Hay actas archivadas (ARCHIVO o JUZGADO DE FALTAS) sin poder determinar "
                "el motivo con certeza (Desestimación/Amonestación/Sobreseimiento/Suspensión). "
                "Quedaron con motivo_archivo_sigemi vacío para elegirlas a mano."
            )
        if resumen["resueltas_sin_archivar_a_revisar_motivo"] > 0:
            print(
                "\n⚠️  Hay actas 'Resuelta sin Archivar' con un código de resolución "
                "todavía no confirmado en CODIGOS_MOTIVO_RESOLUCION_SIN_ARCHIVO "
                "(reglas_sigemi.py). Quedaron con motivo_archivo_sigemi vacío para "
                "elegirlas a mano; una vez identificado el motivo real, sumar el "
                "código al diccionario."
            )
        if resumen["estado_no_determinado"] > 0:
            print(
                "\n⚠️  Hay actas con un estado que no matcheó ninguna regla conocida "
                "(revisar la consola arriba para el detalle de cada una)."
            )
        if resumen["fecha_cobro_posibles_inconsistencias_de_estado"] > 0:
            print(
                "\n⚠️  Hay actas con pago registrado en el archivo de pagos pero cuyo estado_sigemi "
                "en la base no refleja que estén pagadas. Revisar el detalle arriba."
            )
        if resumen["fecha_cobro_sin_impacto_pagada_sin_archivar_revisar"] > 0:
            print(
                "\n⚠️  Hay actas en estado 'Pago Voluntario' (no archivadas) sin fecha real de cobro "
                "(pagadas en Procuración/Municipalidad, no impactaron). Revisar el detalle arriba."
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()