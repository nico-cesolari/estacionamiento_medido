# pasos/comparar_actas_paso.py
import os
from datetime import datetime

from backend.configs import config
from backend.models.claves_contexto import ClavesContexto
from backend.orquestador.comparador import comparar
from backend.pasos.paso_base import PasoBase
from backend.utils.utils import Utilidades

class CompararActasPaso(PasoBase):
    async def ejecutar(self, contexto):
        self.iniciar_paso()

        ruta_excel_sigemi = contexto.ruta_excel_actas_cruce_sigemi
        ruta_excel_sigi = contexto.ruta_excel_actas_cruce_sigi
        ruta_txt_pagos_crudo = contexto.ruta_txt_pagos

        print("\n🔍 Comparando actas del SEMyT contra pagos del SIGI...")

        carpeta_hoy = Utilidades.carpeta_del_dia(config.CARPETA_DESCARGAS_PAGOS)
        nombre = f"PAGOS {datetime.now().strftime('%Y%m%d %H%M%S')}.txt"
        ruta_salida = os.path.join(carpeta_hoy, nombre)

        comparar(
            archivo_excel_sigemi=ruta_excel_sigemi,
            archivo_excel_sigi=ruta_excel_sigi,
            archivo_pagos=ruta_txt_pagos_crudo,
            archivo_total_causas_sigemi=config.ARCHIVO_TOTAL_CAUSAS_SIGEMI,
            archivo_causas_simplificado=config.ARCHIVO_CAUSAS_SIMPLIFICADO,
            archivo_salida=ruta_salida,
        )

        print(f"✅ TXT de pagos (ya cruzado con actas y causas) generado en: {Utilidades.ruta_para_log(ruta_salida)}")

        Utilidades.eliminar_archivo_si_existe(ruta_txt_pagos_crudo, motivo="TXT de pagos crudo eliminado (ya usado en el cruce)")
        Utilidades.eliminar_archivo_si_existe(ruta_excel_sigemi, motivo="Excel MULTAS_SIGEMI_CRUCE eliminado (ya usado en el cruce)")
        Utilidades.eliminar_archivo_si_existe(ruta_excel_sigi, motivo="Excel MULTAS_SIGI_CRUCE eliminado (ya usado en el cruce)")

        contexto.guardar(ClavesContexto.RUTA_TXT_PAGOS, ruta_salida)
        contexto.registrar_archivo(ruta_salida)
        self.finalizar_paso()