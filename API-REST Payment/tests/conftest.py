import pandas as pd
import pytest

from backend.configs import excel, causas
from backend.configs import pagos as pagos_config


def df_pagos(filas):
    """Arma un DataFrame de pagos con TODAS las columnas esperadas,
    completando con "" lo que cada test no necesite. `filas` es una lista
    de dicts con solo las columnas que le importan al test."""
    columnas = pagos_config.COLUMNAS_PAGOS
    data = []
    for fila in filas:
        completa = {c: fila.get(c, "") for c in columnas}
        data.append(completa)
    return pd.DataFrame(data, columns=columnas)


def df_multas(filas):
    """Arma un DataFrame de multas (Excel SIGEMI/SIGI) con las 3
    columnas relevantes: Numero, Dominio, Fecha Hora."""
    columnas = [excel.COL_NUM_ACTA, excel.COL_PADRON, excel.COL_FECHA_LABRADA]
    return pd.DataFrame(filas, columns=columnas)


def df_causas(filas):
    columnas = causas.COLUMNAS_CAUSAS
    return pd.DataFrame(filas, columns=columnas)


@pytest.fixture
def hacer_pagos():
    return df_pagos


@pytest.fixture
def hacer_multas():
    return df_multas


@pytest.fixture
def hacer_causas():
    return df_causas
