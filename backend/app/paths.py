# Ubicación: backend/app/paths.py
from pathlib import Path

# backend/app/paths.py -> backend/app/
RAIZ_APP = Path(__file__).resolve().parent

# backend/app/paths.py -> raíz del repo (contiene "API-REST Payment" y "backend")
RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

CARPETA_SESIONES_API_REST_PAYMENT = RAIZ_PROYECTO / "API-REST Payment" / "datos" / "sesiones"