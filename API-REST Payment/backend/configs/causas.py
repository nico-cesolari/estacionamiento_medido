import pandas as pd

COL_NUM_ACTA = "ACTA_NUMERO"
COL_NUM_CAUSA = "CAUSA_NUMERO"
COL_ANIO_CAUSA = "CAUSA_AÑO"

COLUMNAS_CAUSAS = [COL_NUM_ACTA, COL_NUM_CAUSA, COL_ANIO_CAUSA]

FECHA_CAMBIO_SISTEMA = pd.to_datetime("25/11/2025",dayfirst=True)
# Las actas se numeran por año, así que un mismo ACTA_NUMERO puede
# repetirse en años distintos. Por eso el cruce contra causas siempre se
# hace por (ACTA_NUMERO, CAUSA_AÑO), nunca solo por ACTA_NUMERO.

# Límite superior del rango de "multas vencidas viejas": todo lo anterior
# al cambio de sistema, un día antes del cambio (el día del cambio en
# adelante ya es sistema nuevo y no necesita buscarse en SIGEMI).
FECHA_HASTA_MULTAS_VENCIDAS_VIEJAS = FECHA_CAMBIO_SISTEMA - pd.Timedelta(days=1)
