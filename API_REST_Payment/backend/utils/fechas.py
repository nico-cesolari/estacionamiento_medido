# utils/fechas.py
# -----------------------------------------------------------------------------
# La lógica real vive en common/normalizacion/fechas.py (compartida con
# comparador.py y excel_service.py). Este archivo re-exporta nomás, para
# no tener que salir a cambiar cada `from backend.utils import fechas as
# utilidades_fecha` que ya existe en el proyecto (actas_service.py,
# descargas_paralelas_paso.py, etc.).
# -----------------------------------------------------------------------------
from common.normalizacion.fechas import (
    FORMATO_VISUAL,
    FORMATO_ALMACENAMIENTO,
    FORMATO_EXCEL_FECHA_HORA,
    texto_a_fecha,
    fecha_a_texto,
    es_formato_valido,
    sumar_un_dia,
    fecha_y_hora_actual,
)