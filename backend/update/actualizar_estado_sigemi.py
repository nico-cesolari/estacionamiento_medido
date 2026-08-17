#!/usr/bin/env python3
"""
FUNCIONAL
actualizar_estado_sigemi.py
------------------------------
ACTUALIZACIÓN: lee el mismo archivo crudo de SIGEMI, pero sólo toca las
actas que YA tienen causa Y estado_sigemi cargados de una pasada anterior
de llenar_actas_sigemi.py. Actualiza estado_sigemi (y motivo_archivo_sigemi
cuando corresponde) SIN volver a pisar la causa (una vez cargada, se
asume estable).

Actas que TODAVÍA no tienen causa/estado (recién aparecen en SIGEMI, nunca
pasaron por la carga inicial) NO se tocan acá -- para eso está
llenar_actas_sigemi.py.

Actas que no existen en la base: se ignoran (no se crean).

Reutiliza TODO el parseo/reglas de negocio desde app/reglas/reglas_sigemi.py
(el mismo módulo que usa llenar_actas_sigemi.py) -- nada duplicado acá.

USO:
    cd backend
    python update/actualizar_estado_sigemi.py app/datos/maestro/total_em_sigemi.txt             # dry-run
    python update/actualizar_estado_sigemi.py app/datos/maestro/total_em_sigemi.txt --commit   # graba
"""
import argparse
import sys
from pathlib import Path

from app.models import models
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.database import SessionLocal
from app.services.estados import aplicar_cambios_estado

from app.services.sistemas.sigemi.reglas.reglas_sigemi import (
    leer_registros_crudos,
    extraer_acta_numero,
    calcular_estado,
    resolver_estado,
)


def _ya_tiene_causa_y_estado(registro) -> bool:
    causa_cargada = bool(registro.causa and str(registro.causa).strip())
    return causa_cargada and registro.estado_sigemi is not None


def actualizar_estados(db, path_entrada, commit: bool):
    registros_crudos = leer_registros_crudos(path_entrada)

    actualizadas = sin_cambios = no_encontradas = sin_acta_numero = 0
    todavia_sin_carga_inicial = a_revisar = sin_determinar = 0
    resueltas_a_revisar_motivo = 0

    for raw in registros_crudos:
        acta_numero = extraer_acta_numero(raw)
        if not acta_numero:
            print(f"[SIGEMI-ACTUALIZAR] ⚠️ no se pudo extraer número de acta del registro: {raw}")
            sin_acta_numero += 1
            continue

        print(f"[SIGEMI-ACTUALIZAR] ── acta={acta_numero}")

        registro = db.query(models.Registro).filter(models.Registro.acta == acta_numero).first()
        if registro is None:
            print(f"[SIGEMI-ACTUALIZAR]   ❌ no encontrada en la base")
            no_encontradas += 1
            continue

        print(f"[SIGEMI-ACTUALIZAR]   ✅ encontrada en DB (id={registro.id}) -- "
              f"causa={registro.causa!r}, estado_sigemi actual={registro.estado_sigemi}")

        # Todavía no pasó por la carga inicial: no es trabajo de este
        # script, lo deja para llenar_actas_sigemi.py.
        if not _ya_tiene_causa_y_estado(registro):
            print(f"[SIGEMI-ACTUALIZAR]   (todavía sin causa/estado inicial -- se deja para "
                  f"llenar_actas_sigemi.py, no se toca)")
            todavia_sin_carga_inicial += 1
            continue

        estado_final = calcular_estado(raw)
        resuelto = resolver_estado(estado_final, models)

        print(f"[SIGEMI-ACTUALIZAR]   archivo -> estado calculado={estado_final!r}")

        if resuelto is None:
            print(f"[SIGEMI-ACTUALIZAR]   ⚠️ estado '{estado_final}' no se pudo determinar; revisar a mano.")
            sin_determinar += 1
            continue

        nuevo_estado, nuevo_motivo = resuelto

        # motivo_archivo_sigemi aplica tanto a "Archivado" como a
        # "Resuelta sin Archivar" (ver reglas_sigemi.py, nota RESUELTA
        # SIN ARCHIVAR).
        estados_con_motivo = (models.EstadoSigemi.archivado, models.EstadoSigemi.resuelta_sin_archivo)
        motivo_actual = registro.motivo_archivo_sigemi if nuevo_estado in estados_con_motivo else None
        sin_cambio_real = (
            registro.estado_sigemi == nuevo_estado
            and (nuevo_motivo is None or motivo_actual == nuevo_motivo)
        )

        print(f"[SIGEMI-ACTUALIZAR]   comparación -> DB: {registro.estado_sigemi} vs. "
              f"archivo: {nuevo_estado.value}{f' (motivo: {nuevo_motivo.value})' if nuevo_motivo else ''}")

        if sin_cambio_real:
            print(f"[SIGEMI-ACTUALIZAR]   sin cambios")
            sin_cambios += 1
            continue

        print(f"[SIGEMI-ACTUALIZAR]   {'✅ COMMIT' if commit else '(dry-run)'} acta {acta_numero}: "
              f"{registro.estado_sigemi} -> {nuevo_estado.value}"
              f"{f' (motivo: {nuevo_motivo.value})' if nuevo_motivo else ''}")

        if commit:
            cambios_estado = {"estado_sigemi": nuevo_estado}
            if nuevo_estado in estados_con_motivo and nuevo_motivo is not None:
                cambios_estado["motivo_archivo_sigemi"] = nuevo_motivo
            aplicar_cambios_estado(db, registro, cambios_estado)

        if estado_final == "ARCHIVADO - REVISAR MOTIVO":
            a_revisar += 1
        elif nuevo_estado == models.EstadoSigemi.resuelta_sin_archivo and nuevo_motivo is None:
            resueltas_a_revisar_motivo += 1

        actualizadas += 1

    if commit:
        db.commit()
    else:
        db.rollback()

    return {
        "actualizadas": actualizadas,
        "sin_cambios": sin_cambios,
        "no_encontradas_en_db": no_encontradas,
        "sin_acta_numero_en_archivo": sin_acta_numero,
        "todavia_sin_carga_inicial": todavia_sin_carga_inicial,
        "archivados_a_revisar_motivo": a_revisar,
        "resueltas_sin_archivar_a_revisar_motivo": resueltas_a_revisar_motivo,
        "estado_no_determinado": sin_determinar,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Actualiza estado_sigemi (sin tocar causa) de actas que ya tienen carga inicial."
    )
    parser.add_argument("entrada", help="Archivo de entrada (txt, separado por '|')")
    parser.add_argument("--commit", action="store_true", help="Graba los cambios en la base (sin esto, dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        resumen = actualizar_estados(db, args.entrada, commit=args.commit)
        modo = "COMMIT" if args.commit else "DRY-RUN (no se grabó nada)"
        print(f"\nModo: {modo}")
        for clave, valor in resumen.items():
            print(f"  {clave}: {valor}")
        if resumen["archivados_a_revisar_motivo"] > 0:
            print(
                "\n⚠️  Hay actas archivadas (ARCHIVO o JUZGADO DE FALTAS) sin poder determinar "
                "el motivo con certeza. Quedaron con motivo_archivo_sigemi vacío para elegirlas a mano."
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
    finally:
        db.close()


if __name__ == "__main__":
    main()