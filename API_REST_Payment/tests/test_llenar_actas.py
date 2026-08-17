"""
Tests del PASO 1 de comparador.py: llenar_actas_vacias.

Cubre el caso de negocio más delicado del proyecto: cuando varios pagos de
la misma patente+fecha llegan sin acta, hay que emparejarlos con las multas
disponibles respetando el orden (multa más antigua -> pago más antiguo), sin
pisar actas que otro pago de esa misma clave ya trajo de origen, y dejando
sin resolver (para revisión manual) lo que no cierra 1 a 1.
"""
from backend.configs import pagos as pagos_config
from backend.orquestador import comparador


def _indexar(multas):
    return comparador.indexar_multas_por_patente_y_fecha(multas)


class TestIndexarMultas:
    def test_agrupa_por_patente_y_fecha_ordenado_por_hora(self, hacer_multas):
        multas = hacer_multas([
            {"Numero": "20", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 10:00"},
            {"Numero": "10", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 08:00"},
        ])
        indice = _indexar(multas)
        grupo = indice[("AB123CD", "27/06/2024")]
        assert [m["acta"] for m in grupo] == ["10", "20"]

    def test_multa_sin_patente_o_fecha_se_ignora(self, hacer_multas):
        multas = hacer_multas([
            {"Numero": "10", "Dominio": "", "Fecha Hora": "27/06/2024, 08:00"},
            {"Numero": "20", "Dominio": "AB123CD", "Fecha Hora": ""},
        ])
        indice = _indexar(multas)
        assert indice == {}


class TestLlenarActasVacias:
    def test_un_pago_una_multa_se_completa(self, hacer_pagos, hacer_multas):
        pagos = hacer_pagos([
            {"PADRON": "AB123CD", "LABRADA_FECHA": "27/06/2024", "ACTA_NUMERO": ""},
        ])
        multas = hacer_multas([
            {"Numero": "555", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 08:00"},
        ])
        indice = _indexar(multas)
        resultado = comparador.llenar_actas_vacias(pagos, indice)
        assert resultado.loc[0, pagos_config.COL_NUM_ACTA] == "555"

    def test_dos_pagos_dos_multas_se_emparejan_por_orden_y_hora(self, hacer_pagos, hacer_multas):
        # El pago que aparece primero en el archivo (proxy de más antiguo)
        # debe quedarse con la multa de hora más chica.
        pagos = hacer_pagos([
            {"PADRON": "AB123CD", "LABRADA_FECHA": "27/06/2024", "ACTA_NUMERO": ""},
            {"PADRON": "AB123CD", "LABRADA_FECHA": "27/06/2024", "ACTA_NUMERO": ""},
        ])
        multas = hacer_multas([
            {"Numero": "999", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 18:00"},
            {"Numero": "111", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 07:00"},
        ])
        indice = _indexar(multas)
        resultado = comparador.llenar_actas_vacias(pagos, indice)
        assert resultado.loc[0, pagos_config.COL_NUM_ACTA] == "111"
        assert resultado.loc[1, pagos_config.COL_NUM_ACTA] == "999"

    def test_no_pisa_acta_ya_reservada_por_otro_pago_de_la_misma_clave(self, hacer_pagos, hacer_multas):
        # Un pago de esa patente+fecha YA vino con acta propia (111): esa
        # acta no puede volver a asignarse al otro pago sin acta de la
        # misma clave, aunque sea la de hora más chica.
        pagos = hacer_pagos([
            {"PADRON": "AB123CD", "LABRADA_FECHA": "27/06/2024", "ACTA_NUMERO": "111"},
            {"PADRON": "AB123CD", "LABRADA_FECHA": "27/06/2024", "ACTA_NUMERO": ""},
        ])
        multas = hacer_multas([
            {"Numero": "111", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 07:00"},
            {"Numero": "999", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 18:00"},
        ])
        indice = _indexar(multas)
        resultado = comparador.llenar_actas_vacias(pagos, indice)
        assert resultado.loc[1, pagos_config.COL_NUM_ACTA] == "999"

    def test_sin_multas_disponibles_queda_vacio(self, hacer_pagos, hacer_multas):
        pagos = hacer_pagos([
            {"PADRON": "ZZ999ZZ", "LABRADA_FECHA": "27/06/2024", "ACTA_NUMERO": ""},
        ])
        multas = hacer_multas([
            {"Numero": "555", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 08:00"},
        ])
        indice = _indexar(multas)
        resultado = comparador.llenar_actas_vacias(pagos, indice)
        assert resultado.loc[0, pagos_config.COL_NUM_ACTA] == ""

    def test_desbalance_empareja_lo_que_puede_y_deja_el_resto_sin_resolver(self, hacer_pagos, hacer_multas):
        # 2 pagos sin acta, 1 sola multa disponible: se empareja 1 y el
        # otro pago queda sin acta (no se inventa nada).
        pagos = hacer_pagos([
            {"PADRON": "AB123CD", "LABRADA_FECHA": "27/06/2024", "ACTA_NUMERO": ""},
            {"PADRON": "AB123CD", "LABRADA_FECHA": "27/06/2024", "ACTA_NUMERO": ""},
        ])
        multas = hacer_multas([
            {"Numero": "555", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 08:00"},
        ])
        indice = _indexar(multas)
        resultado = comparador.llenar_actas_vacias(pagos, indice)
        actas_resultantes = list(resultado[pagos_config.COL_NUM_ACTA])
        assert actas_resultantes.count("555") == 1
        assert actas_resultantes.count("") == 1

    def test_pago_con_acta_original_no_se_toca(self, hacer_pagos, hacer_multas):
        pagos = hacer_pagos([
            {"PADRON": "AB123CD", "LABRADA_FECHA": "27/06/2024", "ACTA_NUMERO": "777"},
        ])
        multas = hacer_multas([
            {"Numero": "555", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024, 08:00"},
        ])
        indice = _indexar(multas)
        resultado = comparador.llenar_actas_vacias(pagos, indice)
        assert resultado.loc[0, pagos_config.COL_NUM_ACTA] == "777"
