"""
Piezas compartidas entre los distintos scripts que hablan con la grilla de
SEMyT (https://ciudad.villamaria.gob.ar/#/actas): mapeo de estados,
normalización de texto, parseo de fecha, y la lógica de "bajar la foto de
una fila".

Usado por:
  - procesar_actas_semyt.py       (endpoint diario: altas de hoy + actualización)
  - cargar_actas_semyt.py         (backfill histórico, scrolleando desde el final)
  - actualizar_actas_semyt.py     (sólo actualización, sin crear altas)

Antes esto estaba duplicado (con una diferencia real: cargar_actas_semyt.py
ignoraba también "EN REVISION" y procesar_actas_semyt.py no) entre los dos
primeros archivos. Unificado acá, con "EN REVISION" incluido para los dos.
"""
import asyncio
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..models import models

COLUMNAS_TABLA = ["nro", "fecha", "dominio", "cuadra", "estado", "vencimiento", "importe", "acciones"]
INDICE_COLUMNA_ESTADO = COLUMNAS_TABLA.index("estado")

SELECTOR_FILAS_RESULTADO = "table tbody tr"

SELECTOR_BOTON_VER_IMAGEN = (
    "td:last-child button, td:last-child a, td:last-child [role='button'], "
    "button[aria-label*='foto' i], button[aria-label*='imagen' i], "
    "button[aria-label*='ver' i], [title*='foto' i], [title*='imagen' i]"
)
SELECTOR_BOTON_CERRAR_VISOR = "button.cerrar-button"
SELECTOR_BOTON_CERRAR_VISOR_FALLBACK_TEXTO = "Cerrar"
SELECTOR_DIALOG_CONTAINER = ".cdk-overlay-backdrop, .mat-dialog-container"

# Estados que NUNCA se procesan (ni se crean, ni se actualizan):
#   - IMPAGA: todavía no fue pagada, no hay nada que cargar.
#   - PAGADA: pago voluntario de estacionamiento (no confundir con "Pagada
#             en Juzgado", ese sí se carga).
#   - EN REVISION: estado transitorio, se espera a que se resuelva.
ESTADOS_IGNORADOS_SEMYT = {"IMPAGA", "PAGADA", "EN REVISION"}

ESTADO_PAGADA_EN_JUZGADO = "PAGADA EN JUZGADO"

MAPA_ESTADO_SEMYT = {
    "NO CARGADA": models.EstadoSemyt.no_cargada,
    "VENCIDA": models.EstadoSemyt.vencida,
    ESTADO_PAGADA_EN_JUZGADO: models.EstadoSemyt.pagada_en_juzgado,
    "RESUELTA EN JUZGADO": models.EstadoSemyt.resuelta_en_juzgado,
    "RECHAZADA": models.EstadoSemyt.rechazada,
}

def pagada_en_juzgado_con_datos(vencimiento_texto: str, importe_texto: str) -> bool:
    """
    True si vencimiento_texto y/o importe_texto tienen contenido real
    (no vacío, no '-'). Se usa para decidir si una fila en estado
    'PAGADA EN JUZGADO' se ignora (como IMPAGA/PAGADA/EN REVISION) o se
    carga/actualiza normal. Estos valores NO se persisten en la DB, solo
    se usan para esta decisión.
    """
    def _tiene_contenido(texto: str) -> bool:
        return bool(texto and texto.strip() not in ("", "-"))
    return _tiene_contenido(vencimiento_texto) or _tiene_contenido(importe_texto)

def normalizar_estado(texto: str) -> str:
    """'EN REVISIÓN' -> 'EN REVISION'. Saca tildes y pasa a mayúscula, para
    no depender de si el sitio muestra el texto con o sin acento."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return sin_tildes.strip().upper()


def parsear_fecha_hora(texto: str) -> Optional[datetime]:
    """'jueves 27/06/24 08:38' o '27/06/2024 08:38' -> datetime. None si no matchea."""
    if not texto:
        return None
    match = re.search(r"(\d{2}/\d{2}/\d{2,4})\s+(\d{2}:\d{2})", texto)
    if not match:
        return None
    fecha_str, hora_str = match.groups()
    formato = "%d/%m/%Y %H:%M" if len(fecha_str.split("/")[-1]) == 4 else "%d/%m/%y %H:%M"
    try:
        return datetime.strptime(f"{fecha_str} {hora_str}", formato)
    except ValueError:
        return None


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

    # Antes acá se usaba `expect_page(timeout=5000)`, que espera a que el
    # navegador dispare el evento "se abrió una pestaña nueva". El caso
    # normal en este sitio es que la foto se muestre en un dialog de
    # Angular Material DENTRO de la misma página (por eso el cierre de
    # abajo busca '.cerrar-button' / '.mat-dialog-container'), así que ese
    # evento nunca llega -- y ANTES DE CADA FOTO se tiraban 5 segundos
    # enteros esperando algo que no iba a pasar, más 800ms de sleep extra.
    # En una página de 100 actas eso es potencialmente +9-10 minutos
    # tirados a la basura solo en esta función.
    #
    # Ahora corremos las dos posibilidades EN PARALELO -- pestaña nueva
    # (evento del contexto) o modal en la misma página (selector) -- y nos
    # quedamos con la que resuelva primero. En el caso normal (modal en la
    # misma página) esto resuelve en el momento en que el dialog aparece,
    # no importa si son 150ms o 2 segundos, en vez de esperar siempre el
    # tope fijo.
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

    # Si abrió pestaña nueva, ese es el "pagina_detalle" a leer. Si en
    # cambio lo que resolvió fue el modal en la MISMA página (el caso
    # normal acá), no hay que hacer nada más: pagina_detalle ya es `page`
    # y el dialog ya está visible, listo para leer las imágenes. Si
    # ninguna de las dos resolvió en 3.5s, seguimos igual por las dudas
    # (mismo comportamiento de "no encontré nada" que antes, pero sin
    # esperar el tope viejo de 5.8s).
    if tarea_popup in terminadas and tarea_popup.exception() is None:
        pagina_detalle = tarea_popup.result()
        await pagina_detalle.wait_for_load_state("domcontentloaded")

    foto_url = None

    # El modal/contenedor puede estar "visible" con la <img> real TODAVÍA
    # cargando su src (el navegador reserva el layout antes de tener los
    # bytes). Si medimos en ese instante, o no hay nada que medir, o
    # agarramos el ícono en vez de la foto. Por eso esperamos a que
    # exista al menos una imagen con contenido real cargado (naturalWidth
    # > 0) antes de comparar tamaños -- esto resuelve apenas la imagen
    # termina de cargar, no un tope fijo, así que no volvemos a pagar el
    # costo del sleep ciego de antes.
    try:
        await pagina_detalle.wait_for_function(
            "() => Array.from(document.querySelectorAll('img')).some(im => im.naturalWidth > 0)",
            timeout=10000,
        )
    except Exception:
        pass
    # Colchón extra fijo -- aunque ya haya una imagen con naturalWidth>0,
    # a veces el sitio sigue reemplazando el src (thumbnail -> foto final)
    # unos instantes más. Este margen es chico a propósito, la espera
    # grande de verdad ya la hizo el wait_for_function de arriba.
    await pagina_detalle.wait_for_timeout(1000)

    imagenes = pagina_detalle.locator("img")
    # Antes: un round-trip al browser por CADA imagen (bounding_box) más
    # otro aparte para el src de la elegida. Ahora: un solo evaluate_all
    # trae tamaño y src de todas las imágenes de una. Usamos
    # naturalWidth/naturalHeight (tamaño REAL de la imagen ya cargada) en
    # vez de getBoundingClientRect (que depende del CSS/layout del
    # momento y puede dar 0 si la imagen no cargó todavía).
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