#!/usr/bin/env python3
"""
procesar_archivo_sigemi.py
-------------------
Utilidad de diagnóstico: exporta a CSV el estado calculado por
reglas_sigemi.py para cada acta de un archivo crudo de SIGEMI, sin tocar
la base de datos. Útil para revisar en Excel/planilla qué haría
llenar_actas_sigemi.py / actualizar_estado_sigemi.py ANTES de correrlos
con --commit.

Toda la lógica de negocio (parseo, reglas de estado) vive en
app/services/sistemas/sigemi/reglas/reglas_sigemi.py -- este script sólo
la invoca y vuelca el resultado a CSV. NO reimplementar reglas acá: una
copia vieja de esta lógica llevó a que este mismo script diera resultados
distintos (y con bugs ya corregidos del lado bueno) que
llenar_actas_sigemi.py -- ver historial si hace falta el detalle.

USO:
    python procesar_archivo_sigemi.py entrada.txt salida.csv --modo inicial
    python procesar_archivo_sigemi.py entrada.txt salida.csv --modo actualizacion
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.sistemas.sigemi.reglas.reglas_sigemi import (
    leer_registros_crudos,
    extraer_numero_causa,
    extraer_acta_numero,
    calcular_estado,
    resolver_estado,
    mapa_estado_final,
)
from app.models import models


def procesar_archivo(path_entrada, path_salida, modo):
    registros = leer_registros_crudos(path_entrada)

    filas_salida = []
    for raw in registros:
        numero = extraer_acta_numero(raw)
        if not numero:
            continue
        causa = extraer_numero_causa(raw)
        estado_final = calcular_estado(raw)
        resuelto = resolver_estado(estado_final, models)
        revisar = resuelto is None or (resuelto[0] is not None and resuelto[1] is None and estado_final != "PAGADA")

        filas_salida.append({
            "NUMERO": numero,
            "CAUSA": causa or "",
            "ESTADO_FINAL": estado_final,
            "ESTADO_SIGEMI": resuelto[0].value if resuelto and resuelto[0] else "",
            "MOTIVO": resuelto[1].value if resuelto and resuelto[1] else "",
            "REVISAR_MANUAL": "SI" if revisar else "",
            "RAW": raw,
        })

    if modo == "inicial":
        columnas = ["NUMERO", "CAUSA", "ESTADO_FINAL", "ESTADO_SIGEMI", "MOTIVO", "REVISAR_MANUAL", "RAW"]
    else:
        columnas = ["NUMERO", "ESTADO_FINAL", "ESTADO_SIGEMI"]

    with open(path_salida, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columnas, delimiter=";")
        writer.writeheader()
        for fila in filas_salida:
            writer.writerow({k: fila[k] for k in columnas})

    total = len(filas_salida)
    a_revisar = sum(1 for f in filas_salida if f["REVISAR_MANUAL"] == "SI")
    print(f"Procesados {total} registros.")
    print(f"  -> {a_revisar} marcados para REVISAR_MANUAL.")
    print(f"Archivo generado: {path_salida}")


def main():
    parser = argparse.ArgumentParser(description="Diagnóstico: exporta a CSV el estado calculado por reglas_sigemi.py.")
    parser.add_argument("entrada", help="Archivo de entrada (txt, separado por '|')")
    parser.add_argument("salida", help="Archivo de salida (csv, separado por ';')")
    parser.add_argument("--modo", choices=["inicial", "actualizacion"], default="inicial")
    args = parser.parse_args()
    procesar_archivo(args.entrada, args.salida, args.modo)


if __name__ == "__main__":
    main()