# pasos/cargar_pagos_paso.py
from backend.pasos.paso_base import PasoBase
from backend.pages.importar_pagos_page import ResultadoImportacionPagos
from backend.services.pagos_service import PagosService
from backend.utils.utils import Utilidades


class CargarPagosPaso(PasoBase):
    async def ejecutar(self, contexto):
        self.iniciar_paso()
        ruta_txt = contexto.ruta_txt_pagos

        if not PagosService.hay_registros_para_subir(ruta_txt):
            print("ℹ No se encontraron pagos para actualizar (el TXT final no tiene registros).")
            Utilidades.eliminar_archivo_si_existe(ruta_txt, motivo="TXT de pagos sin registros eliminado (nada para subir)")
            print("⏭ Se omite la carga al SEMyT.")
            self.finalizar_paso()
            return

        print("\n📤 Subiendo TXT de pagos al SEMyT...")
        service = PagosService(carpeta_descargas_pagos="", log=print)  # no se usa para subir
        resultado = await service.subir_txt_pagos(contexto.pagina_semyt, ruta_txt)

        if resultado == ResultadoImportacionPagos.SIN_ACTAS:
            Utilidades.eliminar_archivo_si_existe(ruta_txt, motivo="TXT sin actualizaciones eliminado")
            print("❌ No hubo pagos para actualizar. El TXT descargado fue eliminado.")
            self.finalizar_paso()
            return

        if resultado == ResultadoImportacionPagos.ERROR:
            raise RuntimeError("SEMyT informó: Error al procesar el archivo de pagos.")

        print("✅ Archivo TXT de pagos cargado en el SEMyT correctamente.")
        contexto.marcar_archivo_subido()
        self.finalizar_paso()