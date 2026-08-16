# services/pagos_service.py
# -----------------------------------------------------------------------------
# Único dueño de "descargar TXT de pagos" y "subir TXT de pagos". Antes
# DescargarPagosPage/ImportarPagosPage tenían su lógica de negocio (nombre
# de archivo, carpeta del día, chequeo de "hay registros para subir")
# repartida entre pasos/ (sync) y funciones sueltas en pagos_runner_async.py.
# -----------------------------------------------------------------------------
import os

from backend.pages.descargar_pagos_page import DescargarPagosPage
from app.services.sistemas.semyt.pages.importar_pagos_page import ImportarPagosPage, ResultadoImportacionPagos
from backend.utils.utils import Utilidades

class PagosService:
    def __init__(self, carpeta_descargas_pagos: str, log=print):
        self.carpeta_descargas_pagos = carpeta_descargas_pagos
        self.log = log

    async def descargar_txt_pagos(self, pagina, nombre_archivo: str = "ALL PAGOS DESCARGADOS.txt") -> str:
        page = DescargarPagosPage(pagina)
        await page.abrir()
        descarga = await page.descargar_txt_pagos()

        carpeta_hoy = Utilidades.carpeta_del_dia(self.carpeta_descargas_pagos)
        ruta_txt = os.path.join(carpeta_hoy, nombre_archivo)
        await descarga.save_as(ruta_txt)
        self.log(f"✅ TXT de pagos descargado en: {Utilidades.ruta_para_log(ruta_txt)}")
        return ruta_txt

    async def subir_txt_pagos(self, pagina, ruta_txt: str) -> ResultadoImportacionPagos:
        page = ImportarPagosPage(pagina)
        await page.abrir()
        return await page.importar_txt(ruta_txt)

    @staticmethod
    def hay_registros_para_subir(ruta_txt: str) -> bool:
        """El TXT final siempre tiene, como mínimo, la línea de encabezado.
        Si no hay ninguna línea más aparte de esa, no hay nada que subir."""
        try:
            with open(ruta_txt, "r", encoding="utf-8") as archivo:
                lineas = [l for l in archivo if l.strip()]
        except OSError:
            return False
        return len(lineas) > 1