# common/normalizacion/fechas.py
# -----------------------------------------------------------------------------
# Punto único de fechas: antes esto estaba repartido entre utils/fechas.py
# (funciones "puras" tipo texto_a_fecha/fecha_a_texto) y orquestador/
# comparador.py (parsear_fecha_hora_completa, más robusto porque soporta
# varios formatos de origen). excel_service.py además tenía su PROPIA
# constante de formato (FORMATO_FECHA_EXCEL) con el mismo valor que
# FORMATO_EXCEL_FECHA_HORA de acá abajo, solo que con otro nombre.
#
# Todo eso vive ahora en un solo lugar. Los módulos viejos pasan a ser
# shims que re-exportan desde acá (ver utils/fechas.py), así no hace
# falta tocar cada import existente de una sola vez.
# -----------------------------------------------------------------------------
import re
from datetime import datetime, timedelta

import pandas as pd

FORMATO_VISUAL = "%d/%m/%Y"
FORMATO_ALMACENAMIENTO = "%Y-%m-%d"
FORMATO_EXCEL_FECHA_HORA = "%d/%m/%Y, %H:%M"


def texto_a_fecha(texto: str, formato: str = FORMATO_VISUAL) -> datetime:
    """Convierte un texto a un objeto datetime según el formato indicado."""
    return datetime.strptime(texto.strip(), formato)


def fecha_a_texto(fecha: datetime, formato: str = FORMATO_VISUAL) -> str:
    """Convierte un objeto datetime a texto según el formato indicado."""
    return fecha.strftime(formato)


def es_formato_valido(texto: str, formato: str = FORMATO_VISUAL) -> bool:
    """Indica si un texto respeta el formato de fecha esperado."""
    try:
        texto_a_fecha(texto, formato)
        return True
    except ValueError:
        return False


def sumar_un_dia(fecha: datetime) -> datetime:
    """Devuelve la fecha siguiente, sin componente de hora."""
    return (fecha + timedelta(days=1)).normalize() if hasattr(fecha, "normalize") else fecha + timedelta(days=1)


def fecha_y_hora_actual() -> datetime:
    """Punto único de acceso al reloj del sistema."""
    return datetime.now()


def parsear_fecha_hora_completa(valor):
    """
    Parsea fecha (con hora si la tiene) a un Timestamp completo.
    Robusto ante distintos formatos de origen:
      - "27/06/2024, 08:40" (string tal cual viene de Excel exportado)
      - "2024-06-27 08:40:00" (si Excel entregó un datetime real y pandas
        lo convirtió a string con dtype=str)
      - "27/06/2024" (pagos, sin hora)
    Devuelve pd.NaT si no se puede parsear.
    """
    if pd.isna(valor):
        return pd.NaT
    texto = str(valor).strip().replace(",", " ")
    texto = re.sub(r"\s+", " ", texto)
    return pd.to_datetime(texto, dayfirst=True, errors="coerce")


# Alias por compatibilidad: comparador.py usaba este nombre más corto.
parsear_fecha = parsear_fecha_hora_completa


def normalizar_fecha_comparacion(valor) -> str:
    """
    Extrae SOLO la fecha (sin hora) en formato DD/MM/YYYY para comparación,
    sin importar el formato de origen (string con hora, datetime real, etc).
    """
    fecha = parsear_fecha_hora_completa(valor)
    if pd.isna(fecha):
        return ""
    return fecha.strftime("%d/%m/%Y")