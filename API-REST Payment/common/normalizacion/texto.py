# common/normalizacion/texto.py
# -----------------------------------------------------------------------------
# Normalización de texto compartida entre orquestador/comparador.py y
# cualquier otro consumidor (antes vivía SOLO en comparador.py).
# -----------------------------------------------------------------------------
import re

import pandas as pd


def limpiar_numero_acta(valor) -> str:
    """Normaliza actas: solo dígitos."""
    if pd.isna(valor):
        return ""
    return re.sub(r"\D", "", str(valor))


def limpiar_patente(valor) -> str:
    """Normaliza patentes: mayúsculas, sin espacios ni guiones."""
    if pd.isna(valor):
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(valor).upper())