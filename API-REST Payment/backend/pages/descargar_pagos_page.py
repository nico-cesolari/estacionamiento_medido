# pages/descargar_pagos_page.py
# -----------------------------------------------------------------------------
# Page Object async: SIGI - Descargar TXT UTN estacionamiento.
# -----------------------------------------------------------------------------
from backend.configs import rutas


class DescargarPagosPage:
    URL = rutas.SIGI_PROCESO

    def __init__(self, page):
        self.page = page
        self.boton_descargar = page.get_by_role("button", name="Descargar TXT UTN estacionamiento")

    async def abrir(self):
        await self.page.goto(self.URL, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_selector("text=Exportaciones", timeout=45000)

    async def descargar_txt_pagos(self):
        async with self.page.expect_download(timeout=100000) as descarga_info:
            await self.boton_descargar.wait_for()
            await self.boton_descargar.click()
        return await descarga_info.value