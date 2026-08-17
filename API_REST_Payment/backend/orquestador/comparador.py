import os
import re
import pandas as pd
from collections import defaultdict
from backend.configs import excel, causas
from backend.configs import pagos as pagos_config
from backend.configs.pagos import COLUMNAS_PAGOS
from backend.utils.utils import Utilidades
from common.normalizacion.texto import limpiar_numero_acta, limpiar_patente
from common.normalizacion.fechas import (
    parsear_fecha_hora_completa,
    parsear_fecha,
    normalizar_fecha_comparacion,
)
import time
# ================== NORMALIZACIÓN DE VALORES ==================
# limpiar_numero_acta, limpiar_patente, parsear_fecha_hora_completa,
# parsear_fecha y normalizar_fecha_comparacion ahora viven en
# common/normalizacion/ (importadas arriba). Se mantienen los mismos
# nombres acá para que el resto de este archivo, y los tests, no cambien.

def limpiar_columnas(df):
    """Limpia nombres de columnas."""
    df.columns = [c.strip() for c in df.columns]
    return df

# ================== CARGA DE ARCHIVOS ==================

def cargar_excel(path, hoja=0):
    """Carga el excel de multas."""
    df = pd.read_excel(path, sheet_name=hoja, dtype=str)
    limpiar_columnas(df)
    return df

def cargar_pagos(path):
    """Carga el txt de pagos."""
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(path, sep="|", dtype=str, encoding=enc, quoting=3)
            encoding_usado = enc
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError("No se pudo leer pagos.txt con ninguna codificación probada")
    limpiar_columnas(df)
    return df

def cargar_causas_sigemi(path):
    """Carga un archivo de causas SIGEMI (tanto el maestro total_causas_sigemi.txt
    como el simplificado causas.txt: tienen el mismo formato)."""
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(path, sep="|", dtype=str, encoding=enc, quoting=3)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"No se pudo leer {path} con ninguna codificación probada")
    limpiar_columnas(df)
    return df

# ================== PASO 1: LLENAR ACTA_NUMERO ==================
def indexar_multas_por_patente_y_fecha(multas):
    """
    Crea índice de multas por (patente, fecha normalizada), donde cada
    entrada es una LISTA de multas ordenada por HORA ascendente (y por
    ACTA_NUMERO ascendente como desempate).

    Vectorizado: antes llamaba pd.to_datetime() UNA fila a la vez (2 veces
    por fila, ~288.000 llamadas en total entre SIGEMI+SIGI) — ahí se
    iban los ~30s del PASO 1. Ahora se parsean TODAS las fechas de la
    columna en una sola llamada vectorizada, y se arma el índice iterando
    ya sobre datos parseados (mucho más liviano).
    """
    grupos = defaultdict(list)

    patentes = multas[excel.COL_PADRON].map(limpiar_patente)
    fechas_raw = multas[excel.COL_FECHA_LABRADA]
    actas = multas[excel.COL_NUM_ACTA].map(limpiar_numero_acta)

    # Parseo vectorizado UNA sola vez (reemplaza las 2 llamadas por fila
    # que hacían parsear_fecha_hora_completa + normalizar_fecha_comparacion).
    texto = (
        fechas_raw.astype(str).str.strip()
        .str.replace(",", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True)
    )
    horas_completas = pd.to_datetime(texto, dayfirst=True, errors="coerce")
    fechas_normalizadas = horas_completas.dt.strftime("%d/%m/%Y")

    for idx, patente, fecha_raw_valor, hora, fecha_normalizada, acta in zip(
        multas.index, patentes, fechas_raw, horas_completas, fechas_normalizadas, actas
    ):
        if not patente or pd.isna(fecha_raw_valor) or pd.isna(hora):
            continue

        grupos[(patente, fecha_normalizada)].append({
            "idx": idx,
            "hora": hora,
            "acta": acta,
        })

    def _clave_orden(m):
        hora = m["hora"] if pd.notna(m["hora"]) else pd.Timestamp.max
        try:
            acta_num = int(m["acta"]) if m["acta"] else float("inf")
        except ValueError:
            acta_num = float("inf")
        return (hora, acta_num)

    for clave in grupos:
        grupos[clave].sort(key=_clave_orden)

    return grupos

def _construir_actas_ya_usadas(pagos):
    """
    Recorre los pagos que YA tienen ACTA_NUMERO cargado de origen y arma un
    diccionario (patente, fecha_normalizada) -> set de actas ya usadas.

    Vectorizado: antes recorría las 14.865 filas completas con iterrows()
    para quedarse solo con ~1.700 que tienen acta. Ahora se arma la máscara
    primero y solo se itera sobre ESAS filas (mucho menos trabajo).
    """
    acta_col = pagos[pagos_config.COL_NUM_ACTA]
    tiene_acta = acta_col.notna() & (acta_col.astype(str).str.strip() != "")

    if not tiene_acta.any():
        return defaultdict(set)

    patente = pagos.loc[tiene_acta, pagos_config.COL_PADRON].map(limpiar_patente)
    fecha_raw = pagos.loc[tiene_acta, pagos_config.COL_FECHA_LABRADA]
    fecha_normalizada = fecha_raw.map(normalizar_fecha_comparacion)
    acta_limpia = acta_col.loc[tiene_acta].map(limpiar_numero_acta)

    valido = (patente != "") & (fecha_normalizada != "")

    usadas = defaultdict(set)
    for pat, fecha, acta in zip(patente[valido], fecha_normalizada[valido], acta_limpia[valido]):
        usadas[(pat, fecha)].add(acta)

    return usadas
def llenar_actas_vacias(pagos, indice_multas):
    """
    PASO 1: Llena números de acta vacíos en pagos.
    (misma lógica de siempre — ver docstring original — solo cambia CÓMO
    se arman los grupos: vectorizado en vez de iterrows() sobre las
    14.865 filas completas)
    """
    print("\n" + "="*80)
    print("PASO 1: LLENANDO ACTA_NUMERO (búsqueda por patente + fecha, con desempate por hora)")
    print("="*80)

    pagos_llenados_exitosamente = 0
    pagos_no_encontrados = 0
    pagos_sin_resolver_por_desbalance = 0

    actas_ya_usadas = _construir_actas_ya_usadas(pagos)

    acta_col = pagos[pagos_config.COL_NUM_ACTA]
    tiene_acta_original = acta_col.notna() & (acta_col.astype(str).str.strip() != "")
    pagos_con_acta_original = int(tiene_acta_original.sum())

    idx_sin_acta = pagos.index[~tiene_acta_original]
    pagos_sin_acta_buscada = len(idx_sin_acta)

    patente_sin_acta = pagos.loc[idx_sin_acta, pagos_config.COL_PADRON].map(limpiar_patente)
    fecha_raw_sin_acta = pagos.loc[idx_sin_acta, pagos_config.COL_FECHA_LABRADA]
    fecha_normalizada_sin_acta = fecha_raw_sin_acta.map(normalizar_fecha_comparacion)

    valido = (patente_sin_acta != "") & (fecha_normalizada_sin_acta != "")
    pagos_no_encontrados += int((~valido).sum())

    grupos_pagos_sin_acta = defaultdict(list)
    for idx, pat, fecha in zip(idx_sin_acta[valido], patente_sin_acta[valido], fecha_normalizada_sin_acta[valido]):
        grupos_pagos_sin_acta[(pat, fecha)].append(idx)

    # --- de acá en adelante, TODO IGUAL que antes (sin cambios) ---
    for clave, indices_pagos in grupos_pagos_sin_acta.items():

        multas_candidatas = indice_multas.get(clave, [])
        actas_reservadas = actas_ya_usadas.get(clave, set())

        multas_disponibles = [
            m for m in multas_candidatas if m["acta"] not in actas_reservadas
        ]

        if not multas_disponibles:
            pagos_no_encontrados += len(indices_pagos)
            continue

        indices_pagos_ordenados = sorted(indices_pagos)
        n_emparejar = min(len(indices_pagos_ordenados), len(multas_disponibles))

        for i in range(n_emparejar):
            idx_pago = indices_pagos_ordenados[i]
            multa = multas_disponibles[i]
            pagos.loc[idx_pago, pagos_config.COL_NUM_ACTA] = multa["acta"]
            pagos_llenados_exitosamente += 1

        if len(indices_pagos_ordenados) > n_emparejar:
            pagos_sin_resolver_por_desbalance += len(indices_pagos_ordenados) - n_emparejar

    print(f"\n📊 ESTADÍSTICAS DE LLENADO DE ACTAS:")
    print(f"  • Pagos con acta ORIGINAL: {pagos_con_acta_original}")
    print(f"  • Pagos SIN acta (buscados): {pagos_sin_acta_buscada}")
    print(f"  • Pagos LLENADOS exitosamente: {pagos_llenados_exitosamente}")
    print(f"  • Pagos NO encontrados en multas: {pagos_no_encontrados}")
    print(f"  • Pagos SIN RESOLVER por desbalance de cantidades: {pagos_sin_resolver_por_desbalance}")

    return pagos

# ================== PASO 2: FILTRAR DESACTUALIZADOS ==================

def actas_vigentes(multas):
    """Set de ACTA_NUMERO (normalizados) presentes en un Excel de vencidas."""
    if multas is None or len(multas) == 0:
        return set()
    return {a for a in multas[excel.COL_NUM_ACTA].map(limpiar_numero_acta) if a}

def _filtrar_vigentes_de_un_sistema(mascara_es_este_sistema, actas_normalizadas, actas_vigentes, etiqueta_excel):
    """
    Dado un subconjunto de pagos (los que pertenecen a UN sistema, nuevo o
    viejo, indicados por `mascara_es_este_sistema`) y el set de actas
    vigentes de SU Excel correspondiente, devuelve qué pagos se mantienen
    y cuántos se descartaron por ya no figurar vencidos (SEMyT ya los
    actualizó).

    `actas_normalizadas` se recibe ya calculado (una sola vez, afuera) en
    vez de recalcularse acá, para no repetir el mismo .map() sobre toda
    la columna una vez por cada sistema.
    """
    sigue_vigente = actas_normalizadas.isin(actas_vigentes)

    mascara_mantener = mascara_es_este_sistema & sigue_vigente
    descartados = int((mascara_es_este_sistema & ~sigue_vigente).sum())

    if descartados > 0:
        print(
            f"  ⚠ {descartados} pago(s) descartados: su acta ya no figura "
            f"como vencida en {etiqueta_excel} (SEMyT ya los actualizó)."
        )

    return mascara_mantener, descartados


def filtrar_por_vigencia_segun_sistema(pagos, actas_vigentes_sigi, actas_vigentes_sigemi):
    """
    PASO 2-bis: se queda solo con los pagos cuya acta sigue vigente,
    usando el Excel correspondiente según a qué sistema pertenece cada uno:

      - Sistema NUEVO (fecha >= FECHA_CAMBIO_SISTEMA): contra MULTAS_SIGI_CRUCE.
      - Sistema VIEJO (fecha <  FECHA_CAMBIO_SISTEMA): contra MULTAS_SIGEMI_CRUCE.
    """
    fecha_labrada = pagos[pagos_config.COL_FECHA_LABRADA].map(parsear_fecha)
    es_sistema_nuevo = fecha_labrada >= causas.FECHA_CAMBIO_SISTEMA

    actas_normalizadas = pagos[pagos_config.COL_NUM_ACTA].map(limpiar_numero_acta)

    mascara_mantener_nuevo, descartados_nuevo = _filtrar_vigentes_de_un_sistema(
        es_sistema_nuevo, actas_normalizadas, actas_vigentes_sigi, "MULTAS_SIGI_CRUCE"
    )
    mascara_mantener_viejo, descartados_viejo = _filtrar_vigentes_de_un_sistema(
        ~es_sistema_nuevo, actas_normalizadas, actas_vigentes_sigemi, "el Excel de SIGEMI"
    )

    mascara_mantener = mascara_mantener_nuevo | mascara_mantener_viejo
    pagos_filtrados = pagos[mascara_mantener].copy()
    descartados = descartados_nuevo + descartados_viejo

    return pagos_filtrados, descartados

def filtrar_pagos_completos(pagos):
    """
    PASO 2: Filtra y mantiene SOLO los registros que tienen ACTA_NUMERO completo.
    """
    print("\n" + "="*80)
    print("PASO 2: FILTRANDO PAGOS CON ACTA_NUMERO COMPLETO")
    print("="*80)

    pagos_totales = len(pagos)

    pagos_filtrados = pagos[
        (pagos[pagos_config.COL_NUM_ACTA].notna()) &
        (pagos[pagos_config.COL_NUM_ACTA].astype(str).str.strip() != "")
    ].copy()

    pagos_filtrados_count = len(pagos_filtrados)
    pagos_eliminados = pagos_totales - pagos_filtrados_count

    print(f"\n📊 ESTADÍSTICAS DE FILTRADO:")
    print(f"  • Pagos TOTALES al inicio: {pagos_totales}")
    print(f"  • Pagos CON acta (MANTIENEN): {pagos_filtrados_count}")
    print(f"  • Pagos RECHAZADOS (sin coincidencias): {pagos_eliminados}")

    if pagos_eliminados > 0:
        print(f"\n⚠️  Se eliminaron {pagos_eliminados} registros sin número de acta")
        print(f"     (recordá: esto incluye tanto pagos ya actualizados/pagados")
        print(f"      como pagos que quedaron sin resolver por desbalance en el Paso 1)")

    estadisticas = {
        'totales': pagos_totales,
        'filtrados': pagos_filtrados_count,
        'rechazados': pagos_eliminados
    }

    return pagos_filtrados, estadisticas

# ============== PASO 2.5: ARMAR causas.txt SIMPLIFICADO ==============

def _anio_de_acta_vieja(fecha_labrada_raw):
    """Año a partir de la fecha labrada de una fila del Excel de multas
    vencidas viejas (ese Excel no trae columna de año, solo fecha)."""
    fecha = parsear_fecha_hora_completa(fecha_labrada_raw)
    if pd.isna(fecha):
        return None
    return str(fecha.year)

def indexar_causas_por_acta_y_anio(df_causas):
    """Crea índice rápido: (ACTA_NUMERO, CAUSA_AÑO) -> CAUSA_NUMERO."""
    actas = df_causas[causas.COL_NUM_ACTA].map(limpiar_numero_acta)
    anios = df_causas[causas.COL_ANIO_CAUSA].fillna("").astype(str).str.strip()
    numeros = df_causas[causas.COL_NUM_CAUSA]

    indice = {}
    for acta, anio, numero in zip(actas, anios, numeros):
        if acta and anio:
            indice[(acta, anio)] = numero
    return indice
def generar_causas_simplificado(multas, df_total_causas, archivo_salida):
    """
    PASO 2.5: arma el causas.txt "simplificado" (descargas/causas/causas.txt)
    a partir del maestro completo (total_causas_sigemi.txt), quedándose
    SOLO con las causas cuya (ACTA_NUMERO, AÑO) aparece entre las multas
    vencidas viejas ya cargadas en memoria.

    Vectorizado: antes parseaba 104.421 fechas UNA fila a la vez con
    pd.to_datetime (ver _anio_de_acta_vieja) — ahí se iban los ~10.3s.
    Ahora se parsea toda la columna en una sola llamada.
    """
    print("\n" + "="*80)
    print("PASO 2.5: ARMANDO causas.txt SIMPLIFICADO (cruce contra total_causas_sigemi.txt)")
    print("="*80)

    actas_multas = multas[excel.COL_NUM_ACTA].map(limpiar_numero_acta)

    # Parseo vectorizado UNA sola vez, en vez de _anio_de_acta_vieja fila a fila.
    fechas_multas = pd.to_datetime(
        multas[excel.COL_FECHA_LABRADA].astype(str).str.strip().str.replace(",", " ", regex=False),
        dayfirst=True, errors="coerce",
    )
    anios_multas = fechas_multas.dt.year.astype("Int64").astype(str)
    # Filas sin fecha válida: year da <NA> -> str(<NA>) = "<NA>", que no
    # matchea nada real en el índice de causas (mismo efecto práctico que
    # el None de antes, que directamente se filtraba abajo con "if acta and anio").
    anios_multas = anios_multas.where(fechas_multas.notna(), None)

    actas_vencidas_viejas = {
        (acta, anio) for acta, anio in zip(actas_multas, anios_multas) if acta and anio
    }

    df_total_causas = df_total_causas.copy()
    df_total_causas["_acta_limpia"] = df_total_causas[causas.COL_NUM_ACTA].map(limpiar_numero_acta)
    df_total_causas["_anio_limpio"] = (
        df_total_causas[causas.COL_ANIO_CAUSA].fillna("").astype(str).str.strip()
    )

    claves = pd.Series(
        list(zip(df_total_causas["_acta_limpia"], df_total_causas["_anio_limpio"])),
        index=df_total_causas.index,
    )
    mascara = claves.isin(actas_vencidas_viejas)
    causas_simplificadas = df_total_causas[mascara].drop(columns=["_acta_limpia", "_anio_limpio"])

    os.makedirs(os.path.dirname(archivo_salida), exist_ok=True)

    causas_simplificadas[causas.COLUMNAS_CAUSAS].to_csv(
        archivo_salida, sep="|", index=False, na_rep="", encoding="utf-8", lineterminator="\n"
    )

    print(f"  • Actas vencidas viejas (multas): {len(actas_vencidas_viejas)}")
    print(f"  • Causas totales en el maestro SIGEMI: {len(df_total_causas)}")
    print(f"  • Causas que coinciden (simplificado): {len(causas_simplificadas)}")
    print(f"  ✅ Archivo escrito: {Utilidades.ruta_para_log(archivo_salida)}")

    return causas_simplificadas

# ================== PASO 3: LLENAR CAUSA_NUMERO ==================

def llenar_causas(pagos, indice_causas):
    """
    PASO 3: Llena CAUSA_NUMERO (y CAUSA_AÑO si falta).
    """
    print("\n" + "="*80)
    print("PASO 3: LLENANDO CAUSA_NUMERO (según fecha de cambio de sistema, cruzando por acta + año)")
    print("="*80)

    causas_encontradas_sigemi = 0
    causas_no_encontradas_sigemi = 0
    causas_asignadas_automaticamente = 0
    causas_ya_existentes = 0

    for idx, row in pagos.iterrows():
        causa_actual = row[pagos_config.COL_EXP]
        acta = limpiar_numero_acta(row[pagos_config.COL_NUM_ACTA])
        fecha_raw = row[pagos_config.COL_FECHA_LABRADA]
        anio_actual = row[pagos_config.COL_ANIO_EXP]

        if causa_actual and not pd.isna(causa_actual) and str(causa_actual).strip():
            causas_ya_existentes += 1
            continue

        fecha = parsear_fecha(fecha_raw)

        if pd.isna(fecha):
            continue

        if anio_actual and not pd.isna(anio_actual) and str(anio_actual).strip():
            anio = str(anio_actual).strip()
        else:
            anio = str(fecha.year)

        if fecha < causas.FECHA_CAMBIO_SISTEMA:
            causa = indice_causas.get((acta, anio))
            if causa:
                pagos.loc[idx, pagos_config.COL_EXP] = causa
                pagos.loc[idx, pagos_config.COL_ANIO_EXP] = anio
                causas_encontradas_sigemi += 1
            else:
                causas_no_encontradas_sigemi += 1
        else:
            pagos.loc[idx, pagos_config.COL_EXP] = acta
            pagos.loc[idx, pagos_config.COL_ANIO_EXP] = anio
            causas_asignadas_automaticamente += 1

    print(f"\n📊 ESTADÍSTICAS DE LLENADO DE CAUSAS:")
    print(f"  • Pagos con causa ORIGINAL: {causas_ya_existentes}")
    print(f"  • Causas encontradas en SIGEMI (actas viejas): {causas_encontradas_sigemi}")
    print(f"  • Causas NO encontradas en SIGEMI: {causas_no_encontradas_sigemi}")
    print(f"  • Causas asignadas automáticamente (actas nuevas): {causas_asignadas_automaticamente}")

    return pagos

# ================== PASO 4: ORDENAR Y ESCRIBIR ==================

def ordenar_salida(pagos):
    """Ordena los pagos por FECHA_LABRADA y luego por ACTA_NUMERO."""
    print("\n" + "="*80)
    print("PASO 4: ORDENANDO POR FECHA Y ACTA_NUMERO")
    print("="*80)

    pagos[pagos_config.COL_FECHA_LABRADA] = pd.to_datetime(
        pagos[pagos_config.COL_FECHA_LABRADA],
        format='%d/%m/%Y',
        errors='coerce'
    )

    pagos['_acta_num'] = pd.to_numeric(
        pagos[pagos_config.COL_NUM_ACTA],
        errors='coerce'
    )

    pagos_sorted = pagos.sort_values(
        by=[pagos_config.COL_FECHA_LABRADA, '_acta_num'],
        na_position='last'
    )

    pagos_sorted = pagos_sorted.drop(columns=['_acta_num'])

    pagos_sorted[pagos_config.COL_FECHA_LABRADA] = pagos_sorted[pagos_config.COL_FECHA_LABRADA].dt.strftime('%d/%m/%Y')

    pagos_sorted = pagos_sorted.reset_index(drop=True)

    print(f"\n✓ Pagos ordenados correctamente")
    if len(pagos_sorted) > 0:
        print(f"  • Primera entrada: Acta={pagos_sorted[pagos_config.COL_NUM_ACTA].iloc[0]}, Fecha={pagos_sorted[pagos_config.COL_FECHA_LABRADA].iloc[0]}")
        print(f"  • Última entrada: Acta={pagos_sorted[pagos_config.COL_NUM_ACTA].iloc[-1]}, Fecha={pagos_sorted[pagos_config.COL_FECHA_LABRADA].iloc[-1]}")

    return pagos_sorted

def escribir_salida(path, pagos):
    """Escribe el archivo final de pagos."""
    print(f"\n📝 Escribiendo archivo de salida...")
    pagos = pagos[COLUMNAS_PAGOS].copy()
    pagos_salida = pagos.to_csv(sep="|",index=False,na_rep="",lineterminator="\n").rstrip("\n")

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(pagos_salida)

    print(f"  ✅ Archivo escrito: {Utilidades.ruta_para_log(path)}")
    print(f"  ✅ Total registros: {len(pagos_salida)}")

# ================== MAIN ==================

def comparar(
    archivo_excel_sigemi,
    archivo_excel_sigi,
    archivo_pagos,
    archivo_total_causas_sigemi,
    archivo_causas_simplificado,
    archivo_salida,
):
    """
    archivo_excel_sigemi: Excel de "multas vencidas viejas", o None.
    archivo_excel_sigi: Excel de "multas vencidas nuevas", o None.
    archivo_total_causas_sigemi: maestro con TODAS las causas de SIGEMI.
    archivo_causas_simplificado: donde se escribe el causas.txt reducido.
    """
    print("\n" + "="*80)
    print("INICIANDO PROCESO DE GENERACIÓN DE PAGOS DESACTUALIZADOS")
    print("="*80)

    print("\n📂 Cargando archivos...")
    pagos = cargar_pagos(archivo_pagos)
    print(f"  ✓ Pagos: {len(pagos)} registros")

    multas_sigemi = cargar_excel(archivo_excel_sigemi, excel.HOJA_EXCEL) if archivo_excel_sigemi else None
    if multas_sigemi is not None:
        print(f"  ✓ Excel SIGEMI (multas vencidas viejas): {len(multas_sigemi)} registros")
    else:
        print("  ℹ Excel SIGEMI (multas vencidas viejas): no había ninguna en este rango")

    multas_sigi = cargar_excel(archivo_excel_sigi, excel.HOJA_EXCEL) if archivo_excel_sigi else None
    if multas_sigi is not None:
        print(f"  ✓ Excel SIGI (multas vencidas nuevas): {len(multas_sigi)} registros")
    else:
        print("  ℹ Excel SIGI (multas vencidas nuevas): no había ninguna en este rango")
    t1 = time.perf_counter()
    if multas_sigemi is not None:
        indice_sigemi = indexar_multas_por_patente_y_fecha(multas_sigemi)
        pagos = llenar_actas_vacias(pagos, indice_sigemi)
    if multas_sigi is not None:
        indice_sigi = indexar_multas_por_patente_y_fecha(multas_sigi)
        pagos = llenar_actas_vacias(pagos, indice_sigi)
    print(f"⏱ PASO 1 (llenar actas): {time.perf_counter() - t1:.1f}s")
    t2 = time.perf_counter()
    pagos, stats_filtrado = filtrar_pagos_completos(pagos)
    
    print("\n" + "="*80)
    print("PASO 2-bis: CONFIRMANDO VIGENCIA DE PAGOS (sistema NUEVO contra MULTAS_SIGI_CRUCE, sistema VIEJO contra SIGEMI)")
    print("="*80)
    pagos, descartados_por_actualizados = filtrar_por_vigencia_segun_sistema(
        pagos,
        actas_vigentes(multas_sigi),
        actas_vigentes(multas_sigemi),
    )
    print(f"  • Pagos descartados en total por ya estar actualizados/pagados: {descartados_por_actualizados}")
    print(f"⏱ PASO 2 (filtrar): {time.perf_counter() - t2:.1f}s")
    t3 = time.perf_counter()
    print("\n📂 Cargando maestro de causas SIGEMI...")
    total_causas = cargar_causas_sigemi(archivo_total_causas_sigemi)
    print(f"  ✓ Causas totales (maestro): {len(total_causas)} registros")

    multas_para_causas = multas_sigemi if multas_sigemi is not None else pd.DataFrame(columns=[excel.COL_NUM_ACTA, excel.COL_FECHA_LABRADA])
    causas_simplificadas = generar_causas_simplificado(multas_para_causas, total_causas, archivo_causas_simplificado)
    indice_causas = indexar_causas_por_acta_y_anio(causas_simplificadas)
    print(f"⏱ PASO 2.5 (causas simplificado): {time.perf_counter() - t3:.1f}s")
    if len(pagos) == 0:
        print("\n⚠️  NO HAY PAGOS CON ACTA_NUMERO PARA PROCESAR")
        print("Creando archivo vacío...")
        with open(archivo_salida, "w", encoding="utf-8") as f:
            f.write("|".join(COLUMNAS_PAGOS))
        return archivo_salida

    t4 = time.perf_counter()
    pagos = llenar_causas(pagos, indice_causas)
    print(f"⏱ PASO 3 (llenar causas): {time.perf_counter() - t4:.1f}s")

    t5 = time.perf_counter()
    pagos = ordenar_salida(pagos)
    escribir_salida(archivo_salida, pagos)
    print(f"⏱ PASO 4 (ordenar y escribir): {time.perf_counter() - t5:.1f}s")

    print("\n" + "="*80)
    print("✅ PROCESO COMPLETADO EXITOSAMENTE")
    print("="*80)

    print(f"\n📊 RESUMEN FINAL DEL PROCESAMIENTO:")
    print(f"\n  ENTRADA:")
    print(f"    • Pagos originales: {stats_filtrado['totales']}")
    print(f"    • Multas SIGEMI cargadas: {len(multas_sigemi) if multas_sigemi is not None else 0}")
    print(f"    • Multas SIGI cargadas: {len(multas_sigi) if multas_sigi is not None else 0}")
    print(f"    • Causas simplificadas (usadas para el cruce): {len(indice_causas)}")

    print(f"\n  PROCESO:")
    print(f"    • Pagos rechazados sigemi: {stats_filtrado['rechazados']}")
    print(f"    • Pagos rechazados sigi: {descartados_por_actualizados}")
    print(f"\n  SALIDA:")
    print(f"    • Pagos desactualizados procesados: {len(pagos)}")
    print(f"    • Archivo final: {Utilidades.ruta_para_log(archivo_salida)}")
    print(f"    • Ordenados por fecha: SÍ ✓")
    print(f"    • Todos con ACTA_NUMERO: SÍ ✓")
    print(f"    • Todos con CAUSA_NUMERO: SÍ ✓")

    return archivo_salida