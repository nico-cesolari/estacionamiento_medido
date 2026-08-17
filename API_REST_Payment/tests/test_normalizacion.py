"""
Tests de las funciones de normalización en backend/orquestador/comparador.py.

Estas son la base de todo el cruce: si fallan silenciosamente, todo lo que
viene después (matching de patente+fecha, filtrado, etc.) queda mal sin que
se note hasta mucho más adelante. Por eso se testean por separado.
"""
import pandas as pd

from backend.orquestador import comparador


class TestLimpiarNumeroActa:
    def test_deja_solo_digitos(self):
        assert comparador.limpiar_numero_acta("N° 00123") == "00123"

    def test_texto_sin_digitos_da_vacio(self):
        assert comparador.limpiar_numero_acta("s/n") == ""

    def test_nan_da_vacio(self):
        assert comparador.limpiar_numero_acta(float("nan")) == ""

    def test_numero_entero_se_castea_bien(self):
        # Puede llegar como int/float desde pandas si la columna no es dtype=str
        assert comparador.limpiar_numero_acta(123) == "123"


class TestLimpiarPatente:
    def test_mayusculas_sin_espacios_ni_guiones(self):
        assert comparador.limpiar_patente("ab-123 cd") == "AB123CD"

    def test_nan_da_vacio(self):
        assert comparador.limpiar_patente(float("nan")) == ""

    def test_caracteres_raros_se_eliminan(self):
        assert comparador.limpiar_patente("ab.123-cd!") == "AB123CD"


class TestParsearFechaHoraCompleta:
    def test_formato_excel_con_coma(self):
        resultado = comparador.parsear_fecha_hora_completa("27/06/2024, 08:40")
        assert resultado == pd.Timestamp("2024-06-27 08:40:00")

    def test_formato_iso_con_espacio(self):
        resultado = comparador.parsear_fecha_hora_completa("2024-06-27 08:40:00")
        assert resultado == pd.Timestamp("2024-06-27 08:40:00")

    def test_solo_fecha_sin_hora(self):
        resultado = comparador.parsear_fecha_hora_completa("27/06/2024")
        assert resultado == pd.Timestamp("2024-06-27 00:00:00")

    def test_valor_no_parseable_da_nat(self):
        assert pd.isna(comparador.parsear_fecha_hora_completa("no es una fecha"))

    def test_nan_da_nat(self):
        assert pd.isna(comparador.parsear_fecha_hora_completa(float("nan")))

    def test_dayfirst_no_confunde_dia_con_mes(self):
        # 03/04/2024 con dayfirst=True es 3 de abril, no 4 de marzo
        resultado = comparador.parsear_fecha_hora_completa("03/04/2024")
        assert resultado.day == 3
        assert resultado.month == 4


class TestNormalizarFechaComparacion:
    def test_ignora_la_hora(self):
        a = comparador.normalizar_fecha_comparacion("27/06/2024, 08:40")
        b = comparador.normalizar_fecha_comparacion("27/06/2024, 23:59")
        assert a == b == "27/06/2024"

    def test_formatos_distintos_de_origen_dan_igual(self):
        # Mismo día, dos formas distintas de venir desde Excel vs pagos.txt
        desde_excel = comparador.normalizar_fecha_comparacion("2024-06-27 08:40:00")
        desde_pagos = comparador.normalizar_fecha_comparacion("27/06/2024")
        assert desde_excel == desde_pagos == "27/06/2024"

    def test_no_parseable_da_vacio(self):
        assert comparador.normalizar_fecha_comparacion("xxx") == ""
