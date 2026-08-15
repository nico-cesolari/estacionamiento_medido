"""
Tests de los PASOS 2 y 2-bis de comparador.py:
  - filtrar_pagos_completos: se queda solo con los pagos que tienen acta.
  - filtrar_sistema_nuevo_vigente: revisa vigencia de TODOS los pagos
    (sistema nuevo contra el Excel SIGI, sistema viejo contra el Excel
    SIGEMI) y descarta los que ya no figuran vencidos (SEMyT ya los
    actualizó/pagó por otra vía).
"""
import pandas as pd

from backend.configs import causas, excel
from backend.configs import pagos as pagos_config
from backend.orquestador import comparador


class TestFiltrarPagosCompletos:
    def test_mantiene_solo_los_que_tienen_acta(self, hacer_pagos):
        pagos = hacer_pagos([
            {"ACTA_NUMERO": "111"},
            {"ACTA_NUMERO": ""},
            {"ACTA_NUMERO": "222"},
        ])
        resultado, stats = comparador.filtrar_pagos_completos(pagos)
        assert list(resultado[pagos_config.COL_NUM_ACTA]) == ["111", "222"]
        assert stats == {"totales": 3, "filtrados": 2, "rechazados": 1}

    def test_acta_con_solo_espacios_cuenta_como_vacia(self, hacer_pagos):
        pagos = hacer_pagos([{"ACTA_NUMERO": "   "}])
        resultado, stats = comparador.filtrar_pagos_completos(pagos)
        assert len(resultado) == 0
        assert stats["rechazados"] == 1


class TestActasVigentes:
    def test_devuelve_set_normalizado(self, hacer_multas):
        multas = hacer_multas([
            {"Numero": "N° 111", "Dominio": "AB123CD", "Fecha Hora": "27/06/2024"},
        ])
        assert comparador.actas_vigentes(multas) == {"111"}

    def test_none_da_set_vacio(self):
        assert comparador.actas_vigentes(None) == set()

    def test_dataframe_vacio_da_set_vacio(self, hacer_multas):
        assert comparador.actas_vigentes(hacer_multas([])) == set()


class TestFiltrarPorVigenciaSegunSistema:
    def _fecha(self, texto):
        return texto

    def test_sistema_viejo_vigente_se_mantiene(self, hacer_pagos):
        # Fecha anterior al cambio de sistema: ahora depende del Excel SIGEMI.
        antes_del_cambio = (causas.FECHA_CAMBIO_SISTEMA - pd.Timedelta(days=30)).strftime("%d/%m/%Y")
        pagos = hacer_pagos([
            {"LABRADA_FECHA": antes_del_cambio, "ACTA_NUMERO": "111"},
        ])
        resultado, descartados = comparador.filtrar_por_vigencia_segun_sistema(
            pagos, actas_vigentes_sigi=set(), actas_vigentes_sigemi={"111"}
        )
        assert len(resultado) == 1
        assert descartados == 0

    def test_sistema_viejo_ya_actualizado_se_descarta(self, hacer_pagos):
        # Caso real reportado: acta labrada antes del cambio de sistema
        # que YA fue pagada en SEMyT y por eso ya no figura como vencida
        # en el Excel SIGEMI. Antes de este fix, este pago NUNCA se
        # descartaba y quedaba escrito en todos los archivos de pago.
        antes_del_cambio = (causas.FECHA_CAMBIO_SISTEMA - pd.Timedelta(days=30)).strftime("%d/%m/%Y")
        pagos = hacer_pagos([
            {"LABRADA_FECHA": antes_del_cambio, "ACTA_NUMERO": "351213"},
        ])
        resultado, descartados = comparador.filtrar_por_vigencia_segun_sistema(
            pagos, actas_vigentes_sigi=set(), actas_vigentes_sigemi={"999"}
        )
        assert len(resultado) == 0
        assert descartados == 1

    def test_sistema_nuevo_vigente_se_mantiene(self, hacer_pagos):
        despues_del_cambio = (causas.FECHA_CAMBIO_SISTEMA + pd.Timedelta(days=1)).strftime("%d/%m/%Y")
        pagos = hacer_pagos([
            {"LABRADA_FECHA": despues_del_cambio, "ACTA_NUMERO": "111"},
        ])
        resultado, descartados = comparador.filtrar_por_vigencia_segun_sistema(
            pagos, actas_vigentes_sigi={"111"}, actas_vigentes_sigemi=set()
        )
        assert len(resultado) == 1
        assert descartados == 0

    def test_sistema_nuevo_ya_actualizado_se_descarta(self, hacer_pagos):
        despues_del_cambio = (causas.FECHA_CAMBIO_SISTEMA + pd.Timedelta(days=1)).strftime("%d/%m/%Y")
        pagos = hacer_pagos([
            {"LABRADA_FECHA": despues_del_cambio, "ACTA_NUMERO": "111"},
        ])
        # El acta 111 ya NO está en el Excel SIGI -> SEMyT ya la actualizó.
        resultado, descartados = comparador.filtrar_por_vigencia_segun_sistema(
            pagos, actas_vigentes_sigi={"999"}, actas_vigentes_sigemi=set()
        )
        assert len(resultado) == 0
        assert descartados == 1