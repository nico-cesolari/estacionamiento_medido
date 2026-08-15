# sistemas/semyt/paginas/exportar_actas_page.py
# -----------------------------------------------------------------------------
# Page Object async: exportar/descargar el Excel de actas. Movido desde
# "API-REST Payment/backend/pages/exportar_actas_page.py" sin cambios de
# lógica -- ya era la única fuente de verdad de estos selectores dentro
# de ese proyecto (antes duplicado en pagos_runner_async.py y en
# descargas_paralelas_paso.py). Ahora también disponible para backend/
# (estacionamiento_medido) si necesita exportar actas por su cuenta.
# -----------------------------------------------------------------------------
import time

from playwright.async_api import TimeoutError as PWTimeoutError


class CartelSinActasSemyt(Exception):
    """SEMyT mostró 'no se encontraron actas vencidas' en vez de descargar."""
    pass


class CartelFechaInvalidaSemyt(Exception):
    """SEMyT mostró 'período inválido' en vez de descargar."""
    pass


class ExportarActasPage:
    def __init__(self, page):
        self.page = page
        self.campos_fecha = page.locator("input[type='date']")
        self.boton_descargar = page.locator("text=Descargar")
        self.mensaje_sin_actas = page.locator("text=No se encontraron actas vencidas")
        self.mensaje_fecha_invalida = page.locator("text=período seleccionado es inválido")
        self.boton_ok_cartel_error = page.locator("text=OK")

    async def abrir(self):
        await self.page.click("text=Exportar actas")

    async def completar_fechas(self, fecha_desde_html: str, fecha_hasta_html: str):
        await self.campos_fecha.nth(0).fill(fecha_desde_html)
        await self.campos_fecha.nth(1).fill(fecha_hasta_html)

    async def descargar(self, segundos_maximo: int = 100):
        """Descarga el Excel de actas. Si en vez de descargar aparece uno de
        los carteles de error conocidos de SEMyT, levanta la excepción
        correspondiente con el texto tal cual lo mostró el sitio."""
        descarga_capturada = {}
        self.page.on("download", lambda d: descarga_capturada.setdefault("valor", d))
        await self.boton_descargar.click()

        limite = time.monotonic() + segundos_maximo
        while time.monotonic() < limite:
            if "valor" in descarga_capturada:
                return descarga_capturada["valor"]

            if await self.mensaje_sin_actas.is_visible():
                texto = await self.mensaje_sin_actas.inner_text()
                await self._cerrar_cartel_error()
                raise CartelSinActasSemyt(texto)

            if await self.mensaje_fecha_invalida.is_visible():
                texto = await self.mensaje_fecha_invalida.inner_text()
                await self._cerrar_cartel_error()
                raise CartelFechaInvalidaSemyt(texto)

            await self.page.wait_for_timeout(250)

        raise PWTimeoutError(
            f"Timeout esperando la descarga (sin cartel de error reconocido) tras {segundos_maximo}s."
        )

    async def _cerrar_cartel_error(self):
        try:
            await self.boton_ok_cartel_error.click(timeout=2000)
        except PWTimeoutError:
            pass