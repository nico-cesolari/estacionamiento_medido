# sistemas/comun/texto.py
# -----------------------------------------------------------------------------
# Normalización de texto compartida entre los dos proyectos. Movido tal
# cual desde "API-REST Payment/common/normalizacion/texto.py" (antes solo
# lo usaba orquestador/comparador.py de ese proyecto).
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