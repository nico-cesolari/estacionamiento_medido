from backend.configs.config import _variable_de_entorno_obligatoria
# --- Sitio 1: SEMyT (login) ---
SEMYT_LOGIN = _variable_de_entorno_obligatoria("SEMYT_LOGIN")

# --- Sitio 2: Municipalidad de Villa María (Carga de Actas - Descarga de pagos) ---
SIGI_LOGIN = _variable_de_entorno_obligatoria("SIGI_LOGIN")
SIGI_PROCESO = _variable_de_entorno_obligatoria("SIGI_PROCESO")
