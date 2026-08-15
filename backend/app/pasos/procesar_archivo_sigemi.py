#!/usr/bin/env python3
"""
procesar_archivo_sigemi.py
-------------------
Lee un archivo "tirado por el sistema" con columnas separadas por '|',
donde a veces el sistema falla y separa un registro en 2 filas (la
segunda fila arranca con ',').

Reconstruye cada registro, detecta el estado real de la causa según
reglas de negocio (Juicio/Procuracion, Pagada, Plan de pago, Archivo,
Pago Voluntario, Sin Resolucion/Vencida) y exporta un CSV con el
resultado, listo para carga inicial o actualizacion.

USO:
    python procesar_archivo_sigemi.py entrada.txt salida.csv --modo inicial
    python procesar_archivo_sigemi.py entrada.txt salida.csv --modo actualizacion

    --modo inicial        -> exporta NUMERO, ANIO, JUZGADO, ESTADO_FINAL,
                              DETALLE, REVISAR_MANUAL, RAW
    --modo actualizacion  -> exporta solo NUMERO, ESTADO_FINAL (para
                              matchear contra lo ya cargado)

El archivo de entrada puede tener o no la fila de encabezado (columnas);
si la primera linea no matchea el patron de registro, se ignora.
"""

import argparse
import csv
import re
import sys

# ------------------------------------------------------------------
# Patron que identifica el INICIO de un registro nuevo y valido:
# JUZGADO(numero) | ANIO(4 digitos) | NUMERO(numeros) | ...
# ------------------------------------------------------------------
INICIO_REGISTRO_RE = re.compile(r'^\s*\d+\|\d{4}\|\d+\|')

# Estado_actual|Estado_descripcion embebido en cualquier parte de la linea
ESTADO_RE = re.compile(r'\|(\w*)\|(PROCURACION|ARCHIVO|_SIN ESTADO_)\|')

JUICIO_RE = re.compile(r'Juicio\s+\S+\s+Saldo:\s*\$?\s*([\d.,]+)', re.IGNORECASE)
PLAN_PAGO_RE = re.compile(r'Plan de Pago\s+\S+\s+Saldo:\s*\$?\s*([\d.,]+)', re.IGNORECASE)
SALDO_ACTUALIZADO_RE = re.compile(r'Saldo Actualizado:\s*\$?\s*([\d.,]+)', re.IGNORECASE)
PAGO_VOLUNTARIO_RE = re.compile(r'Pago Voluntario', re.IGNORECASE)
SIN_RESOLUCION_RE = re.compile(r'Sin Resoluci[oó]n', re.IGNORECASE)

# Código corto (ESTADO_ACTUAL) -> motivo, para el caso "_SIN ESTADO_" que en
# realidad ya está resuelto sin haber pasado por archivo. Debe coincidir con
# CODIGOS_MOTIVO_RESOLUCION_SIN_ARCHIVO de app/reglas/reglas_sigemi.py.
# Sólo "SE" (Sobreseída) está confirmado contra datos reales.
CODIGOS_MOTIVO_RESOLUCION_SIN_ARCHIVO = {
    "SE": "Sobreseimiento",
    # "DS": "Desestimación",  # confirmar código real antes de habilitar
    # "AM": "Amonestación",   # confirmar código real antes de habilitar
    # "SU": "Suspensión",     # confirmar código real antes de habilitar
}


def parse_monto(texto):
    """Convierte '62.400,80' -> 62400.80  y  '0,00' -> 0.0"""
    if texto is None:
        return None
    limpio = texto.strip().replace('.', '').replace(',', '.')
    try:
        return float(limpio)
    except ValueError:
        return None


def leer_registros_crudos(path_entrada):
    """
    Lee el archivo linea por linea y agrupa las lineas que pertenecen
    al mismo registro (cuando el sistema lo parte en 2 filas, la
    segunda arranca con ',' y NO matchea el patron de inicio).
    Devuelve una lista de strings, cada uno = un registro "crudo"
    (posiblemente compuesto por 2 lineas fisicas unidas con un espacio).
    """
    # Probar distintas codificaciones porque el archivo suele venir con
    # caracteres tipo 'Tr\x{fffd}nsito' (problemas de encoding original)
    contenido = None
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            with open(path_entrada, "r", encoding=enc) as f:
                contenido = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if contenido is None:
        with open(path_entrada, "r", encoding="latin-1", errors="replace") as f:
            contenido = f.read()

    lineas = contenido.splitlines()

    registros = []
    actual = None

    for linea in lineas:
        if not linea.strip():
            continue
        if INICIO_REGISTRO_RE.match(linea):
            # Nueva fila valida -> cierra el registro anterior y abre uno nuevo
            if actual is not None:
                registros.append(actual)
            actual = linea
        else:
            # Es continuacion (segunda parte de una fila rota) o basura/encabezado
            if actual is not None:
                actual = actual + " " + linea
            # si actual es None (ej. la primera linea es el encabezado o basura), se ignora
    if actual is not None:
        registros.append(actual)

    return registros


def extraer_numero_anio_juzgado(raw):
    partes = raw.split("|")
    juzgado = partes[0].strip() if len(partes) > 0 else ""
    anio = partes[1].strip() if len(partes) > 1 else ""
    numero = partes[2].strip() if len(partes) > 2 else ""
    return juzgado, anio, numero


def calcular_estado(raw):
    """
    Aplica las reglas de negocio sobre el texto crudo del registro
    (ya reconstruido, sea 1 o 2 lineas unidas).
    Devuelve (estado_final, detalle, revisar_manual: bool)
    """
    m_estado = ESTADO_RE.search(raw)
    estado_desc = m_estado.group(2) if m_estado else None
    codigo_estado = m_estado.group(1) if m_estado else None

    m_juicio = JUICIO_RE.search(raw)
    m_plan = PLAN_PAGO_RE.search(raw)
    m_saldo_act = SALDO_ACTUALIZADO_RE.search(raw)
    tiene_pago_voluntario = bool(PAGO_VOLUNTARIO_RE.search(raw))
    tiene_sin_resolucion = bool(SIN_RESOLUCION_RE.search(raw))

    revisar = False
    detalle = ""

    # -----------------------------------------------------------
    # REGLA 1: si tiene "Juicio" -> SIEMPRE es PROCURACION (salvo
    # que el saldo sea 0, ahi puede estar PAGADA)
    # -----------------------------------------------------------
    if m_juicio:
        saldo_juicio = parse_monto(m_juicio.group(1))

        if estado_desc == "ARCHIVO":
            # Caso de error manual del empleado: tiene Juicio pero
            # quedo cargado como Archivado -> se avisa igual
            revisar = True
            detalle += "ATENCION: tiene Juicio pero el estado quedo como ARCHIVO (error de carga manual). "

        if saldo_juicio is not None and saldo_juicio == 0.0:
            # Saldo del juicio en 0 -> podria estar pagada, PERO
            # ojo con el Plan de Pago
            if m_plan:
                saldo_plan = parse_monto(m_plan.group(1))
                if saldo_plan is not None and saldo_plan != 0.0:
                    estado_final = "VENCIDA (Plan de Pago pendiente)"
                    detalle += f"Juicio saldo $0,00 pero Plan de Pago con saldo {m_plan.group(1)}."
                else:
                    estado_final = "PAGADA"
                    detalle += "Juicio saldo $0,00 y Plan de Pago tambien en $0,00."
            else:
                estado_final = "PAGADA"
                detalle += "Juicio con saldo $0,00, sin plan de pago pendiente."
        else:
            estado_final = "PROCURACION"
            detalle += f"Juicio con saldo pendiente ({m_juicio.group(1)})."
        return estado_final, detalle.strip(), revisar

    # -----------------------------------------------------------
    # REGLA 2: ESTADO_DESCRIPCION = ARCHIVO (sin Juicio)
    # -----------------------------------------------------------
    if estado_desc == "ARCHIVO":
        if tiene_pago_voluntario:
            estado_final = "PAGADA (Archivo - Pago Voluntario)"
            detalle = "Archivada por Pago Voluntario."
        else:
            estado_final = "ARCHIVADA - REVISAR MOTIVO"
            detalle = ("Archivada sin dato de Pago Voluntario: puede ser "
                       "Desestimacion, Amonestacion, Sobreseida, Suspendida, etc. "
                       "Revisar manualmente.")
            revisar = True
        return estado_final, detalle, revisar

    # -----------------------------------------------------------
    # REGLA 2.5: _SIN ESTADO_ con código corto conocido de resolución
    # (y sin el texto "Sin Resolución" en el registro) -> ya está
    # resuelta (ej. Sobreseída), NO vencida. Va antes de la REGLA 3
    # justamente para sacarle estos casos.
    # -----------------------------------------------------------
    if (
        estado_desc == "_SIN ESTADO_"
        and not tiene_sin_resolucion
        and codigo_estado in CODIGOS_MOTIVO_RESOLUCION_SIN_ARCHIVO
    ):
        motivo = CODIGOS_MOTIVO_RESOLUCION_SIN_ARCHIVO[codigo_estado]
        estado_final = f"RESUELTA SIN ARCHIVAR ({motivo})"
        detalle = f"Código '{codigo_estado}' con _SIN ESTADO_: ya resuelta por {motivo}, no archivada."
        return estado_final, detalle, revisar

    # -----------------------------------------------------------
    # REGLA 3: Sin Resolucion / _SIN ESTADO_ -> Vencida (con variantes)
    # -----------------------------------------------------------
    if tiene_sin_resolucion or estado_desc == "_SIN ESTADO_":
        if m_saldo_act:
            estado_final = "VENCIDA (con sentencia)"
            detalle = f"Sin Resolucion con Saldo Actualizado: {m_saldo_act.group(1)}."
        elif tiene_pago_voluntario:
            estado_final = "VENCIDA (Pago Voluntario no archivado)"
            detalle = "Tiene Pago Voluntario cargado pero el estado no es Archivo."
        else:
            estado_final = "VENCIDA"
            detalle = "Sin Resolucion / Sin Estado, sin saldo ni pago voluntario."
        return estado_final, detalle, revisar

    # -----------------------------------------------------------
    # REGLA 4: PROCURACION sin Juicio detectado (caso raro)
    # -----------------------------------------------------------
    if estado_desc == "PROCURACION":
        estado_final = "PROCURACION"
        detalle = "Estado Procuracion sin patron de Juicio reconocido (revisar formato)."
        revisar = True
        return estado_final, detalle, revisar

    # -----------------------------------------------------------
    # Caso no contemplado -> marcar para revisar a mano
    # -----------------------------------------------------------
    estado_final = f"DESCONOCIDO ({estado_desc or 'sin dato'})"
    detalle = "No matcheo ninguna regla conocida. Revisar manualmente."
    revisar = True
    return estado_final, detalle, revisar

def procesar_archivo(path_entrada, path_salida, modo):
    registros = leer_registros_crudos(path_entrada)

    filas_salida = []
    for raw in registros:
        juzgado, anio, numero = extraer_numero_anio_juzgado(raw)
        if not numero:
            continue
        estado_final, detalle, revisar = calcular_estado(raw)

        filas_salida.append({
            "NUMERO": numero,
            "ANIO": anio,
            "JUZGADO": juzgado,
            "ESTADO_FINAL": estado_final,
            "DETALLE": detalle,
            "REVISAR_MANUAL": "SI" if revisar else "",
            "RAW": raw,
        })

    if modo == "inicial":
        columnas = ["NUMERO", "ANIO", "JUZGADO", "ESTADO_FINAL", "DETALLE", "REVISAR_MANUAL", "RAW"]
    else:  # actualizacion
        columnas = ["NUMERO", "ESTADO_FINAL"]

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
    parser = argparse.ArgumentParser(description="Procesa el archivo de causas/actas y calcula el estado real.")
    parser.add_argument("entrada", help="Archivo de entrada (txt, separado por '|')")
    parser.add_argument("salida", help="Archivo de salida (csv, separado por ';')")
    parser.add_argument("--modo", choices=["inicial", "actualizacion"], default="inicial",
                         help="'inicial' = numero+estado+detalle completo; 'actualizacion' = solo numero+estado")
    args = parser.parse_args()

    procesar_archivo(args.entrada, args.salida, args.modo)

if __name__ == "__main__":
    main()