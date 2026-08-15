# sistemas/semyt/reglas/foto.py
# -----------------------------------------------------------------------------
# Único lugar que sabe "cómo se abre y se lee la foto de una fila" en la
# grilla de SEMyT. Movido desde "backend/app/reglas/reglas_semyt.py"
# (la mitad de ese archivo dedicada a fotos, separada de estados.py).
# -----------------------------------------------------------------------------
import asyncio
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

SELECTOR_BOTON_VER_IMAGEN = (
    "td:last-child button, td:last-child a, td:last-child [role='button'], "
    "button[aria-label*='foto' i], button[aria-label*='imagen' i], "
    "button[aria-label*='ver' i], [title*='foto' i], [title*='imagen' i]"
)
SELECTOR_BOTON_CERRAR_VISOR = "button.cerrar-button"
SELECTOR_BOTON_CERRAR_VISOR_FALLBACK_TEXTO = "Cerrar"
SELECTOR_DIALOG_CONTAINER = ".cdk-overlay-backdrop, .mat-dialog-container"


async def _cerrar_visor_imagen(pagina_detalle, page):
    if pagina_detalle is not page:
        await pagina_detalle.close()
        return

    boton_cerrar = page.locator(SELECTOR_BOTON_CERRAR_VISOR)
    if await boton_cerrar.count():
        await boton_cerrar.first.click()
    else:
        boton_por_texto = page.get_by_role("button", name=SELECTOR_BOTON_CERRAR_VISOR_FALLBACK_TEXTO, exact=True)
        if await boton_por_texto.count():
            await boton_por_texto.first.click()
        else:
            await page.keyboard.press("Escape")

    try:
        await page.locator(SELECTOR_DIALOG_CONTAINER).first.wait_for(state="hidden", timeout=5000)
    except PlaywrightTimeoutError:
        await page.wait_for_timeout(500)


async def obtener_url_foto_de_fila(contexto, page, fila, numero_acta: str,
                                    commit: bool) -> Optional[str]:
    """
    Click en el botón de imagen de la fila, detecta la URL de la foto más
    grande del detalle/popup que se abre, y (si commit=True) devuelve esa
    URL absoluta para guardarla en el atributo foto_url del registro.
    No descarga ni escribe ningún archivo a disco.
    Devuelve la URL absoluta o None si no había botón/imagen.
    En dry-run (commit=False) devuelve la URL igual, prefijada con
    "(dry-run) " para poder loguearla sin confundirla con una URL real
    ya persistida.
    """
    boton = fila.locator(SELECTOR_BOTON_VER_IMAGEN)
    if await boton.count() == 0:
        return None

    # Corremos EN PARALELO las dos posibilidades -- pestaña nueva (evento
    # del contexto) o modal en la misma página (selector) -- y nos
    # quedamos con la que resuelva primero.
    tarea_popup = asyncio.ensure_future(contexto.wait_for_event("page"))
    await boton.first.click()

    pagina_detalle = page
    tarea_modal = asyncio.ensure_future(
        page.locator(SELECTOR_DIALOG_CONTAINER).first.wait_for(state="visible")
    )
    tareas_pendientes = {tarea_popup, tarea_modal}
    terminadas, pendientes = await asyncio.wait(
        tareas_pendientes, timeout=6, return_when=asyncio.FIRST_COMPLETED
    )
    for tarea in pendientes:
        tarea.cancel()

    if tarea_popup in terminadas and tarea_popup.exception() is None:
        pagina_detalle = tarea_popup.result()
        await pagina_detalle.wait_for_load_state("domcontentloaded")

    foto_url = None

    # Esperamos a que exista al menos una imagen con contenido real
    # cargado (naturalWidth > 0) antes de comparar tamaños, así no
    # medimos un layout reservado sin bytes todavía.
    try:
        await pagina_detalle.wait_for_function(
            "() => Array.from(document.querySelectorAll('img')).some(im => im.naturalWidth > 0)",
            timeout=10000,
        )
    except Exception:
        pass
    await pagina_detalle.wait_for_timeout(1000)

    imagenes = pagina_detalle.locator("img")
    datos_imagenes = await imagenes.evaluate_all(
        "els => els.map(el => ({src: el.getAttribute('src'), "
        "area: (el.naturalWidth || 0) * (el.naturalHeight || 0)}))"
    )
    candidatas = [d for d in datos_imagenes if d.get("src") and d["area"] > 0]
    if candidatas:
        mejor = max(candidatas, key=lambda d: d["area"])
        url_absoluta = urljoin(pagina_detalle.url, mejor["src"])
        foto_url = url_absoluta if commit else f"(dry-run) {url_absoluta}"

    await _cerrar_visor_imagen(pagina_detalle, page)
    return foto_url