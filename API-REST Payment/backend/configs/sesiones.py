# configs/sesiones.py
# -----------------------------------------------------------------------------
# Punto único de las rutas de sesión guardada en disco (storage_state de
# Playwright). Nadie más debería armar estas rutas a mano.
# -----------------------------------------------------------------------------
import os

from backend.configs import config

RUTA_SESION_COMPARTIDA = os.path.join(config.CARPETA_SESIONES, "sesion_general.json")
RUTA_SESION_SEMYT = os.path.join(config.CARPETA_SESIONES, "sesion_semyt.json")
RUTA_SESION_SIGI = os.path.join(config.CARPETA_SESIONES, "sesion_sigi.json")

TODAS = (RUTA_SESION_COMPARTIDA, RUTA_SESION_SEMYT, RUTA_SESION_SIGI)


def ruta_contexto_inicial() -> str | None:
    """Con qué storage_state abrir un BrowserContext nuevo: prioridad
    compartida > ninguna (primera vez / recién limpiada)."""
    if os.path.exists(RUTA_SESION_COMPARTIDA):
        return RUTA_SESION_COMPARTIDA
    return None
