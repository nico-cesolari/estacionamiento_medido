# utils/reintentos.py
# -----------------------------------------------------------------------------
# La SPA puede redirigir sola a /login si todavía no terminó de asentar el
# token justo cuando nosotros ya estamos navegando a otra parte (ver
# _asentar_sesion más abajo). Esto centraliza el reintento ante ese error
# puntual, para no repetirlo en cada descarga.
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