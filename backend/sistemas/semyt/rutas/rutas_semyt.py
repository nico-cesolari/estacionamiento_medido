import sys
from pathlib import Path
CARPETA_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CARPETA_BACKEND))

# --- Rutas y constantes generales ---
RAIZ_PROYECTO = CARPETA_BACKEND.parent  # un nivel arriba de backend/, no dos veces .parent.parent
ARCHIVO_SESION = RAIZ_PROYECTO / "API-REST Payment" / "datos" / "sesiones" / "sesion_semyt.json"
URL_SEMYT = "https://ciudad.villamaria.gob.ar/#/actas"