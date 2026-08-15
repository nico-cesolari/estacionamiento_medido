# pages/importar_pagos_page.py
# -----------------------------------------------------------------------------
# Page Object async: SEMyT - Procesamiento de información (cargar TXT de pagos).
# -----------------------------------------------------------------------------
import os
from enum import Enum


class ResultadoImportacionPagos(Enum):
    EXITOSA = "exitosa"
    SIN_ACTAS = "sin_actas"
    ERROR = "error"
    DESCONOCIDA = "desconocida"


class ImportarPagosPage:
    def __init__(self, page):
        self.page = page
        self.boton_subir_archivo = page.locator("text=Nueva importación de archivo")
        self.boton_importar_archivo = page.locator("text=Importar archivo")
        self.boton_confirmacion = page.locator("text=Ok")
        self.modal = page.locator(".mat-dialog-container")
        self.boton_lupa = self.modal.locator("button:has(mat-icon:text-is('search'))")

    async def abrir(self):
        await self.page.click("text=Procesamiento de infracciones", timeout=10000)

    async def importar_txt(self, ruta_txt: str) -> ResultadoImportacionPagos:
        archivo = os.path.abspath(ruta_txt)
        if not os.path.exists(archivo):
            raise FileNotFoundError(f"No se encontró el archivo: {archivo}")

        await self.boton_subir_archivo.click(timeout=10000)
        await self.page.get_by_role("cell", name="Archivo:").get_by_role("button").click()

        async with self.page.expect_file_chooser() as fc_info:
            await self.boton_lupa.click(timeout=10000)
        file_chooser = await fc_info.value
        await file_chooser.set_files(archivo)

        await self.boton_importar_archivo.click(timeout=5000)

        popup = self.page.locator(".swal2-popup")
        await popup.wait_for(timeout=15000)
        texto = await popup.inner_text()

        if "Error al procesar el archivo." in texto:
            print("❌ Error al procesar el archivo.")
            resultado = ResultadoImportacionPagos.ERROR
        elif "No se encontraron actas para actualizar." in texto:
            print("❌ No se encontraron actas para actualizar.")
            resultado = ResultadoImportacionPagos.SIN_ACTAS
        elif "Operación exitosa" in texto:
            print("✅ Importación de pagos completada con éxito.")
            resultado = ResultadoImportacionPagos.EXITOSA
        else:
            resultado = ResultadoImportacionPagos.DESCONOCIDA

        await self.boton_confirmacion.click(timeout=5000)
        return resultado