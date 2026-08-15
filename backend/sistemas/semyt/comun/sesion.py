# sistemas/comun/sesion.py
# -----------------------------------------------------------------------------
# Reutiliza sesiones de Playwright ya logueadas (storage_state guardado en
# disco), sin importar qué proyecto las generó.
#
# Movido desde "backend/app/pasos/navegador.py". CAMBIO IMPORTANTE respecto
# al original: antes este archivo calculaba la carpeta de sesiones con una
# ruta relativa HARDCODEADA hacia el OTRO proyecto del repo:
#
#     RAIZ_PROYECTO = Path(__file__).resolve().parents[3]
#     CARPETA_SESIONES = RAIZ_PROYECTO / "API-REST Payment" / "datos" / "sesiones"
#
# Eso significa que "backend/" (estacionamiento_medido) SOLO podía leer
# sesiones si "API-REST Payment/" existía al lado, con ESE nombre exacto de
# carpeta. Ahora `PaginaConSesion` recibe la carpeta de sesiones como
# parámetro (o usa `SESIONES_CARPETA_DEFAULT`, configurable por variable de
# entorno) -- cada proyecto decide de dónde lee, sin acoplarse al otro por
# una ruta fija.
# -----------------------------------------------------------------------------
import os
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright

# Default: variable de entorno SISTEMAS_CARPETA_SESIONES, o
# "./datos/sesiones" relativo al directorio de trabajo del proceso que
# arranca. Cada proyecto puede (y debería) pasar `carpeta_sesiones`
# explícito en vez de depender de este default.
SESIONES_CARPETA_DEFAULT = Path(os.getenv("SISTEMAS_CARPETA_SESIONES", "datos/sesiones"))


def ruta_sesion(nombre: str, carpeta_sesiones: Optional[Path] = None) -> Path:
    """nombre: 'sesion_sigi.json', 'sesion_semyt.json' o 'sesion_general.json'."""
    carpeta = Path(carpeta_sesiones) if carpeta_sesiones else SESIONES_CARPETA_DEFAULT
    return carpeta / nombre


class PaginaConSesion:
    """Context manager async: abre navegador + contexto con storage_state ya logueado.

    `archivo_sesion` puede ser:
      - un nombre suelto (ej. "sesion_semyt.json"): se busca dentro de
        `carpeta_sesiones` (o SESIONES_CARPETA_DEFAULT si no se pasa), y
      - una ruta ya armada (absoluta o con separadores): se usa tal cual,
        sin tocar `carpeta_sesiones`.
    """

    def __init__(
        self,
        archivo_sesion: str,
        url_inicial: str,
        headless: bool = True,
        carpeta_sesiones: Optional[Path] = None,
    ):
        archivo_sesion_path = Path(archivo_sesion)
        es_ruta_armada = archivo_sesion_path.is_absolute() or len(archivo_sesion_path.parts) > 1
        self.archivo_sesion = archivo_sesion_path if es_ruta_armada else ruta_sesion(archivo_sesion, carpeta_sesiones)
        self.url_inicial = url_inicial
        self.headless = headless
        self._pw = None
        self._browser = None
        self._contexto = None

    async def __aenter__(self):
        if not self.archivo_sesion.exists():
            raise RuntimeError(
                f"No hay sesión guardada en {self.archivo_sesion}. "
                "Corré primero el login correspondiente."
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