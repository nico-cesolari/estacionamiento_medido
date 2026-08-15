# sistemas/comun/playwright_utils.py
# -----------------------------------------------------------------------------
# Utilidades genéricas de Playwright que no son propias de ningún sistema
# externo puntual (SEMyT/SIGI/SIGEMI): sirven para cualquier SPA que
# redirija sola a /login mientras el token todavía se está asentando, o
# que necesite un margen antes de seguir navegando tras un login.
#
# Movido tal cual desde "API-REST Payment/backend/utils/reintentos.py"
# (antes solo lo usaba ese proyecto; backend/ -- estacionamiento_medido --
# tenía su propia espera de red suelta y repetida en cada script de
# scraping, ver backend/alta/llenar_actas_sigi.py::_esperar_red por
# ejemplo, que hace básicamente lo mismo).
# -----------------------------------------------------------------------------
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


async def con_reintento_por_navegacion(
    fn_async: Callable[[], Awaitable[T]],
    intentos: int = 2,
    log: Callable[[str], None] = print,
    etiqueta: str = "",
) -> T:
    ultimo_error = None
    for intento in range(1, intentos + 1):
        try:
            return await fn_async()
        except Exception as error:
            ultimo_error = error
            es_navegacion_interrumpida = "interrupted by another navigation" in str(error)
            if intento == intentos or not es_navegacion_interrumpida:
                raise
            log(f"⚠ {etiqueta} intento {intento} falló (sesión no asentada todavía); reintentando...")
    raise ultimo_error


async def asentar_sesion(pagina, log: Callable[[str], None] = print):
    """Le da un margen a la SPA para terminar de asentar el login antes de
    que el script siga navegando. Necesario en async porque las llamadas
    van mucho más rápido que en sync y podemos 'ganarle de mano' a la
    validación interna del sitio."""
    from playwright.async_api import TimeoutError as PWTimeoutError
    try:
        await pagina.wait_for_load_state("networkidle", timeout=10000)
    except PWTimeoutError:
        pass
    await pagina.wait_for_timeout(1500)


async def esperar_red(page, timeout: int = 10000, log: Callable[[str], None] = print):
    """Espera a que la red esté 'quieta', pero SIN reventar el script si
    tarda demasiado -- en una SPA con polling/websockets 'networkidle'
    puede no cumplirse nunca.

    Antes esta MISMA función (con el MISMO cuerpo) estaba copiada suelta
    en cada script de scraping de backend/ (llenar_actas_sigi.py,
    llenar_actas_sigi_reverso.py, actualizar_actas_sigi.py, todos con una
    función interna "_esperar_red" idéntica). Se consolida acá."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        log("⚠ networkidle no se cumplió a tiempo, sigo igual "
            "(puede ser polling/websockets de la SPA, no necesariamente un problema)")