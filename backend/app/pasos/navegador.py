"""
Utilidad compartida por procesar_actas_sigi.py y procesar_actas_semyt.py.

Reutiliza las sesiones que ya deja logueadas la API-REST Payment (Playwright
storage_state guardado en disco) para no volver a loguearse acá.
"""
from pathlib import Path
from playwright.async_api import async_playwright

# estacionamiento_medido/backend/app/pasos/navegador.py -> estacionamiento_medido/
RAIZ_PROYECTO = Path(__file__).resolve().parents[3]
CARPETA_SESIONES = RAIZ_PROYECTO / "API-REST Payment" / "datos" / "sesiones"


def ruta_sesion(nombre: str) -> Path:
    """nombre: 'sesion_sigi.json' o 'sesion_semyt.json' (o 'sesion_general.json')."""
    return CARPETA_SESIONES / nombre


class PaginaConSesion:
    """Context manager async: abre navegador + contexto con storage_state ya logueado."""

    def __init__(self, archivo_sesion: str, url_inicial: str, headless: bool = True):
        self.archivo_sesion = ruta_sesion(archivo_sesion)
        self.url_inicial = url_inicial
        self.headless = headless
        self._pw = None
        self._browser = None
        self._contexto = None

    async def __aenter__(self):
        if not self.archivo_sesion.exists():
            raise RuntimeError(
                f"No hay sesión guardada en {self.archivo_sesion}. "
                "Corré primero el login correspondiente en API-REST Payment."
            )
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._contexto = await self._browser.new_context(
            storage_state=str(self.archivo_sesion)
        )
        page = await self._contexto.new_page()
        await page.goto(self.url_inicial, wait_until="domcontentloaded")
        return page

    async def __aexit__(self, exc_type, exc, tb):
        if self._contexto:
            await self._contexto.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()