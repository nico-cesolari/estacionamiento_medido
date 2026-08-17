# Ubicación: backend/app/reglas/fecha_cobro_sigemi.py
"""
Lógica compartida de corrección de fecha_cobro_sigemi, usada tanto por
actualizar_fecha_cobro_sigemi.py (standalone, para correcciones puntuales
con un archivo distinto) como -- INTEGRADA -- por llenar_actas_sigemi.py y
actualizar_estado_sigemi.py, que la corren como un paso más al final de
cada pasada, usando siempre PATH_PAGOS_SIGEMI (no hace falta pasar el
archivo a mano).

Se separó de actualizar_fecha_cobro_sigemi.py (que ahora es sólo el CLI
delgado para uso standalone) siguiendo el mismo criterio que
reglas_sigemi.py: la lógica de negocio vive en app/reglas/, los scripts
ejecutables en alta/ y update/ sólo la invocan.

IMPORTANTE sobre transacciones: a diferencia del script standalone
original, `corregir_fechas()` acá NO hace commit/rollback -- eso queda a
cargo del caller, para que se pueda integrar dentro de la misma
transacción que ya está usando llenar_actas_sigemi.py / actualizar_
estado_sigemi.py (evita un commit/rollback intermedio que cortaría la
transacción de la carga de actas a mitad de camino).
"""
from collections import defaultdict
from datetime import datetime

# AJUSTAR esta ruta si el archivo real vive en otro lugar -- es la misma
# que se usa siempre a mano para correr actualizar_fecha_cobro_sigemi.py
# (ver docstring/USO de ese script). Relativa al directorio desde el que
# se corren los scripts (backend/).
PATH_PAGOS_SIGEMI = "app/services/sistemas/sigemi/archivos/total_pagos_em_sigemi.txt"

# AJUSTAR estos índices si el orden real de columnas del archivo es
# distinto al documentado en actualizar_fecha_cobro_sigemi.py.
INDICE_FECHA_COBRO = 3
INDICE_ACTA_NUMERO = 9
CANTIDAD_COLUMNAS_ESPERADA = 14

FORMATOS_FECHA = ("%d/%m/%Y", "%d/%m/%y")


def parse_fecha_cobro(texto: str):
    """'28/10/2024' o '28/10/24' -> datetime. None si no matchea ningún formato conocido."""
    texto = (texto or "").strip()
    if not texto:
        return None
    for fmt in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, fmt)
        except ValueError:
            continue
    return None


def leer_pagos(path_entrada):
    """
    Lee el archivo línea por línea (un pago por línea, sin roturas como en
    el archivo de SIGEMI que usa leer_registros_crudos). Devuelve una lista
    de tuplas (acta_numero, fecha_cobro_texto, linea_completa_para_log).
    Salta automáticamente una eventual fila de encabezado (si la primer
    columna es literalmente "LOTE_TIPO").
    """
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

    pagos = []
    for linea in contenido.splitlines():
        if not linea.strip():
            continue
        partes = linea.split("|")
        if partes[0].strip().upper() == "LOTE_TIPO":
            continue  # encabezado
        if len(partes) < CANTIDAD_COLUMNAS_ESPERADA:
            print(f"[FECHA-COBRO-SIGEMI] ⚠️ línea con menos columnas de las esperadas, se ignora: {linea}")
            continue
        acta_numero = partes[INDICE_ACTA_NUMERO].strip()
        fecha_texto = partes[INDICE_FECHA_COBRO].strip()
        if not acta_numero:
            print(f"[FECHA-COBRO-SIGEMI] ⚠️ línea sin número de acta, se ignora: {linea}")
            continue
        pagos.append((acta_numero, fecha_texto, linea))
    return pagos


def agrupar_por_acta(pagos):
    """
    acta -> fecha MÁS RECIENTE entre todas sus filas (parseadas). Si alguna
    fila de esa acta no se pudo parsear, se ignora esa fila puntual pero
    no las demás.
    """
    fechas_por_acta = defaultdict(list)
    for acta_numero, fecha_texto, linea in pagos:
        fecha = parse_fecha_cobro(fecha_texto)
        if fecha is None:
            print(f"[FECHA-COBRO-SIGEMI] ⚠️ acta {acta_numero}: no se pudo parsear la fecha "
                  f"{fecha_texto!r} (línea: {linea})")
            continue
        fechas_por_acta[acta_numero].append(fecha)

    resultado = {}
    for acta_numero, fechas in fechas_por_acta.items():
        if len(set(fechas)) > 1:
            print(f"[FECHA-COBRO-SIGEMI] ℹ️ acta {acta_numero}: aparece con {len(fechas)} fechas distintas "
                  f"en el archivo de pagos ({[f.strftime('%d/%m/%Y') for f in sorted(set(fechas))]}); "
                  f"se toma la más reciente")
        resultado[acta_numero] = max(fechas)
    return resultado


def marcar_pagos_sin_impacto(db, models, actas_con_pago_real: set, commit: bool):
    """
    Recorre la base buscando actas SIGEMI pagadas/archivados-por-pago que
    no están en `actas_con_pago_real` (el set de actas que sí aparecen en
    el archivo de pagos). Esas son pagos que no impactaron -- normalmente
    Procuración o Municipalidad -- y no tienen fecha real de cobro para
    cargar. Ver docstring de actualizar_fecha_cobro_sigemi.py para el
    detalle de qué se toca en cada caso.
    """
    query = db.query(models.Registro).filter(
        models.Registro.estado_sigemi.in_([models.EstadoSigemi.pagada, models.EstadoSigemi.archivado])
    )

    marcadas_archivados = marcadas_pagada_sin_archivar = ya_marcadas = 0

    for registro in query:
        es_pago = registro.estado_sigemi == models.EstadoSigemi.pagada or (
            registro.estado_sigemi == models.EstadoSigemi.archivado
            and registro.motivo_archivo_sigemi == models.MotivoArchivoSigemi.por_pago
        )
        if not es_pago:
            continue  # archivado por otro motivo (desestimación, etc.) -- no es un pago

        if registro.acta in actas_con_pago_real:
            continue  # el pago SÍ impactó -- ya se corrigió/corregirá la fecha en corregir_fechas()

        if registro.motivo_archivo_sigemi == models.MotivoArchivoSigemi.por_pago_procuracion:
            ya_marcadas += 1
            continue  # ya estaba marcada de una corrida anterior

        if registro.estado_sigemi == models.EstadoSigemi.archivado:
            print(f"[SIN-IMPACTO-SIGEMI] ── acta={registro.acta} -- archivado por pago pero no aparece "
                  f"en el archivo de pagos -> se marca motivo_archivo_sigemi=Pago en Procuración, "
                  f"fecha_cobro_sigemi -> None")
            if commit:
                registro.motivo_archivo_sigemi = models.MotivoArchivoSigemi.por_pago_procuracion
                registro.fecha_cobro_sigemi = None
            marcadas_archivados += 1
        else:
            print(f"[SIN-IMPACTO-SIGEMI] ⚠️ acta={registro.acta} -- estado 'Pago Voluntario' (no archivado) "
                  f"pero no aparece en el archivo de pagos -> se vacía fecha_cobro_sigemi, pero NO se toca "
                  f"motivo_archivo_sigemi (sólo aplica a archivados) -- revisar a mano si corresponde archivar")
            if commit:
                registro.fecha_cobro_sigemi = None
            marcadas_pagada_sin_archivar += 1

    return {
        "sin_impacto_marcadas_archivado_procuracion": marcadas_archivados,
        "sin_impacto_pagada_sin_archivar_revisar": marcadas_pagada_sin_archivar,
        "sin_impacto_ya_marcadas_antes": ya_marcadas,
    }


def corregir_fechas(db, models, path_entrada, commit: bool):
    """
    Hace todo el trabajo (corrección de fechas + marcado de pagos sin
    impacto) pero NO commitea ni hace rollback -- eso lo decide el
    caller, para poder integrarse en la transacción de otro script sin
    cortarla a mitad de camino. El caller de más afuera (el único que
    sabe si esto es todo el trabajo de la corrida o sólo un paso más) es
    quien hace db.commit()/db.rollback() al final.
    """
    pagos = leer_pagos(path_entrada)
    fecha_correcta_por_acta = agrupar_por_acta(pagos)

    corregidas = sin_cambios = no_encontradas = inconsistentes = 0

    for acta_numero, fecha_correcta in fecha_correcta_por_acta.items():
        print(f"[FECHA-COBRO-SIGEMI] ── acta={acta_numero} -- fecha de pago real: "
              f"{fecha_correcta.strftime('%d/%m/%Y')}")

        registro = db.query(models.Registro).filter(models.Registro.acta == acta_numero).first()
        if registro is None:
            print(f"[FECHA-COBRO-SIGEMI]   ❌ no encontrada en la base")
            no_encontradas += 1
            continue

        actual = registro.fecha_cobro_sigemi
        print(f"[FECHA-COBRO-SIGEMI]   en DB: fecha_cobro_sigemi={actual}, estado_sigemi={registro.estado_sigemi}"
              f"{f', motivo={registro.motivo_archivo_sigemi}' if registro.motivo_archivo_sigemi else ''}")

        # Sólo avisa -- no cambia estado/motivo, sólo la fecha (ver
        # docstring de actualizar_fecha_cobro_sigemi.py).
        es_pago = registro.estado_sigemi == models.EstadoSigemi.pagada or (
            registro.estado_sigemi == models.EstadoSigemi.archivado
            and registro.motivo_archivo_sigemi == models.MotivoArchivoSigemi.por_pago
        )
        if not es_pago:
            print(f"...conviene revisar el estado a mano")
            inconsistentes += 1
            continue

        if actual is not None and actual.date() == fecha_correcta.date():
            sin_cambios += 1
            continue

        if commit:
            registro.fecha_cobro_sigemi = fecha_correcta
            print(f"[FECHA-COBRO-SIGEMI]   ✅ corregida: {actual} -> {fecha_correcta.strftime('%d/%m/%Y')}")
        else:
            print(f"[FECHA-COBRO-SIGEMI]   (dry-run) se corregiría: {actual} -> "
                  f"{fecha_correcta.strftime('%d/%m/%Y')}, NO se graba")
        corregidas += 1

    resumen_sin_impacto = marcar_pagos_sin_impacto(
        db, models, actas_con_pago_real=set(fecha_correcta_por_acta.keys()), commit=commit
    )

    return {
        "fecha_cobro_corregidas": corregidas,
        "fecha_cobro_sin_cambios": sin_cambios,
        "fecha_cobro_no_encontradas_en_db": no_encontradas,
        "fecha_cobro_posibles_inconsistencias_de_estado": inconsistentes,
        **{f"fecha_cobro_{k}": v for k, v in resumen_sin_impacto.items()},
    }