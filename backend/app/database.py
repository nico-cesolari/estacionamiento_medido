"""
Conexión a la base de datos.

Por defecto usa SQLite en un archivo local (estacionamiento.db), suficiente
para el volumen de esta tabla (miles de registros). Si en algún momento
querés Postgres, sólo cambiás DATABASE_URL en el .env, por ejemplo:

    DATABASE_URL=postgresql://usuario:pass@localhost:5432/estacionamiento

y agregás psycopg2-binary a requirements.txt. El resto del código no cambia.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./estacionamiento.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
