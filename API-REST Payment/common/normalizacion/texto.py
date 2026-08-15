# common/normalizacion/texto.py
# -----------------------------------------------------------------------------
# La lógica real vive en sistemas/comun/texto.py (paquete compartido
# "sistemas-estacionamiento", usado también por backend/ del otro
# proyecto). Este archivo re-exporta nomás, para no tener que salir a
# cambiar cada `from common.normalizacion.texto import ...` que ya existe
# en el proyecto (comparador.py, etc.).
# -----------------------------------------------------------------------------
from app.services.sistemas.comun.texto import (
    limpiar_numero_acta,
    limpiar_patente,
)

__all__ = [
    "limpiar_numero_acta",
    "limpiar_patente",
]