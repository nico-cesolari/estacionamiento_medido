# Ubicación: backend/app/main.py  (REEMPLAZA al archivo actual)
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .models import models
from .database import engine
from .routers import registros, procesamiento

# Crea las tablas si no existen (para producción real preferí Alembic,
# pero para este tamaño de proyecto esto alcanza).
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Estacionamiento Medido - Municipalidad de Villa María")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en producción: restringir al dominio real
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(registros.router)
app.include_router(procesamiento.router)

# Sirve el frontend -- va DESPUÉS, porque es un catch-all
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")