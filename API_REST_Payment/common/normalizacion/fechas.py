# common/normalizacion/fechas.py
# -----------------------------------------------------------------------------
# La lógica real vive en sistemas/comun/fechas.py (paquete compartido
# "sistemas-estacionamiento", usado también por backend/ del otro
# proyecto). Este archivo re-exporta nomás, para no tener que salir a
# cambiar cada `from common.normalizacion.fechas import ...` que ya existe
# en el proyecto (comparador.py, excel_service.py, utils/fechas.py).
# -----------------------------------------------------------------------------
from app.services.sistemas.comun.fechas import (
    FORMATO_VISUAL,
    FORMATO_ALMACENAMIENTO,
    FORMATO_EXCEL_FECHA_HORA,
    texto_a_fecha,
    fecha_a_texto,
    es_formato_valido,
    sumar_un_dia,
    fecha_y_hora_actual,
    parsear_fecha_hora_completa,
    parsear_fecha,
    normalizar_fecha_comparacion,
)

__all__ = [
    "FORMATO_VISUAL",
    "FORMATO_ALMACENAMIENTO",
    "FORMATO_EXCEL_FECHA_HORA",
    "texto_a_fecha",
    "fecha_a_texto",
    "es_formato_valido",
    "sumar_un_dia",
    "fecha_y_hora_actual",
    "parsear_fecha_hora_completa",
    "parsear_fecha",
    "normalizar_fecha_comparacion",
]