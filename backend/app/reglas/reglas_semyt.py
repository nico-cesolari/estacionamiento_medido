# app/reglas/reglas_semyt.py
# -----------------------------------------------------------------------------
# SHIM: la lógica real vive separada en
#   app/services/sistemas/semyt/reglas/estados.py  (mapeo de estados, parseo)
#   app/services/sistemas/semyt/reglas/foto.py      (lectura de foto de fila)
# Este archivo sólo re-exporta, para no tener que tocar cada import
# existente (procesar_actas_semyt.py, cargar_actas_semyt.py,
# actualizar_actas_semyt.py, solucionar_foto_url*.py) de una sola vez.
# NO agregar lógica nueva acá -- va en los módulos separados de arriba.
# -----------------------------------------------------------------------------
from app.models import models

from app.services.sistemas.semyt.reglas.estados import (
    COLUMNAS_TABLA,
    INDICE_COLUMNA_ESTADO,
    SELECTOR_FILAS_RESULTADO,
    ESTADOS_IGNORADOS_SEMYT,
    ESTADO_PAGADA_EN_JUZGADO,
    mapa_estado_semyt,
    pagada_en_juzgado_con_datos,
    normalizar_estado,
    parsear_fecha_hora,
)
from app.services.sistemas.semyt.reglas.foto import obtener_url_foto_de_fila

MAPA_ESTADO_SEMYT = mapa_estado_semyt(models.EstadoSemyt)

__all__ = [
    "COLUMNAS_TABLA",
    "INDICE_COLUMNA_ESTADO",
    "SELECTOR_FILAS_RESULTADO",
    "ESTADOS_IGNORADOS_SEMYT",
    "ESTADO_PAGADA_EN_JUZGADO",
    "MAPA_ESTADO_SEMYT",
    "pagada_en_juzgado_con_datos",
    "normalizar_estado",
    "parsear_fecha_hora",
    "obtener_url_foto_de_fila",
]