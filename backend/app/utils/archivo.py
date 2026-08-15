# utils/archivo_sigemi.py
import pandas as pd

def leer_archivo(ruta_archivo):
    return pd.read_csv(ruta_archivo,sep="|",dtype=str,encoding="utf-8")