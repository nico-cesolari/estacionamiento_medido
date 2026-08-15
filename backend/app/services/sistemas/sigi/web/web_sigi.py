# sistemas/sigi/web/web_sigi.py
# -----------------------------------------------------------------------------
# Navegación compartida de la grilla de SIGI (tabla con header "Expediente
# ID", filtrada por Tipo de acta = Estacionamiento Medido): selectores,
# paginación (adelante y en reversa), apertura del detalle de una fila y
# lectura de sus datos.
#
# Consolida lo que antes estaba CASI IDÉNTICO y triplicado en:
#   - backend/alta/llenar_actas_sigi.py
#   - backend/alta/llenar_actas_sigi_reverso.py
#   - backend/alta/cargar_actas_sigi.py
# y que además backend/update/actualizar_actas_sigi.py importaba directo
# como funciones privadas de llenar_actas_sigi.py (acoplamiento a los
# internals de otro script).
#
# Los 4 scripts de arriba pasan a ser wrappers delgados: sólo definen QUÉ
# hacer con cada fila (a través de un callback `procesar_fila`) y en qué
# orden recorrer las páginas (`recorrer_grilla(..., direccion=...)`). Toda
# la mecánica de Playwright (paginado, reintentos, estrategias de click,
# esperas de red/DOM) vive acá, en un solo lugar.
#
# Nada de esto sabe de la base de datos ni de reglas de negocio -- eso
# sigue viviendo en sistemas/sigi/reglas/reglas_sigi.py y en cada script
# que use este módulo.
# -----------------------------------------------------------------------------
import re
from typing import Awaitable, Callable, Optional

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.services.sistemas.comun.playwright_utils import esperar_red as _esperar_red_comun

# ---------------------------------------------------------------------------
# Selectores (confirmados por HTML real -- ver historial de los 3 scripts
# que se consolidaron acá si hace falta el detalle de cómo se dedujo cada
# uno)
# ---------------------------------------------------------------------------
SELECTOR_TABLA_PRINCIPAL = "table:has(th:has-text('Expediente ID'))"
SELECTOR_FILAS_RESULTADO = f"{SELECTOR_TABLA_PRINCIPAL} tbody tr"
SELECTOR_HEADERS_TABLA = f"{SELECTOR_TABLA_PRINCIPAL} thead th"
TEXTO_HEADER_EXPEDIENTE = "expediente"
TEXTO_HEADER_ESTADO = "estado"

# El botón "Ver" es un ícono de ojo -- un <svg> suelto, sin <button>/<a>
# que lo envuelva -- con dos <path>, el segundo con fill-rule="evenodd" y
# d empezando en "M.664 10.59a1.651..." (Heroicons EyeIcon). Es lo más
# estable para matchear: no depende de que la columna sea la última ni de
# clases Tailwind que podrían repetirse en otros íconos de la fila.
SELECTOR_BOTON_VER = (
    "svg:has(path[d^='M.664 10.59a1.651']), "
    "td:last-child button, td:last-child a, td:last-child [role='button'], "
    "button[aria-label='Ver'], [title='Ver'], "
    "td:last-child svg"
)
SELECTOR_TAB_ACTAS_FALLBACK = "button:has-text('Actas'), a:has-text('Actas'), [class*='tab']:has-text('Actas')"
SELECTOR_MOTIVO_EN_DETALLE = ".motivo-archivo, [data-campo='motivo']"
SELECTOR_BOTON_VOLVER = "button:has-text('Volver'), a:has-text('Volver')"

# Label real del valor "Número acta" dentro de la pestaña Actas (más
# directo que el regex de formato con puntos -- ver leer_numero_acta).
SELECTOR_LABEL_NUMERO_ACTA = "span:text-is('Número acta')"
REGEX_NUMERO_CON_PUNTOS = re.compile(r"\d{1,3}(?:\.\d{3})+")

SELECTOR_SPAN_PAGINACION_MOBILE = "span.sm\\:hidden"
SELECTOR_INPUT_PAGINA = "input[aria-label='Ir a la página']"

# "Mostrando 1 a 50 de 167817 resultados" -- no existe ningún "Página X de
# Y" en el sitio. El número de página se deriva del primer valor junto con
# TAMANO_PAGINA (ej. A=1 -> página 1, A=51 -> página 2, ...).
PATRON_PAGINA_ACTUAL = re.compile(r"Mostrando\s*(\d+)\s*a\s*(\d+)\s*de\s*(\d+)\s*resultados")
PATRON_TOTAL_PAGINAS = re.compile(r"de\s+([\d.,]+)")
TAMANO_PAGINA = 50


def log(paso: str, msg: str):
    print(f"[{paso}] {msg}", flush=True)


def _pagina_desde_match(match: "re.Match") -> int:
    inicio = int(match.group(1))
    return (inicio - 1) // TAMANO_PAGINA + 1


async def esperar_red(page, timeout: int = 10000):
    await _esperar_red_comun(page, timeout=timeout, log=lambda m: log("DEBUG-RED", m))


# ---------------------------------------------------------------------------
# Filtro y paginado inicial (una sola vez, al arrancar el recorrido)
# ---------------------------------------------------------------------------
async def filtrar_por_tipo_acta_estacionamiento(page):
    """Abre el panel de Filtros y selecciona 'Tipo de acta' = 'Estacionamiento
    Medido' (listbox tipo Headless UI)."""
    boton_filtros = page.locator("span:has-text('Filtros')").locator("xpath=ancestor::button[1]")
    if await boton_filtros.count():
        await boton_filtros.click()
    else:
        await page.locator("span:has-text('Filtros')").first.click()

    seccion_tipo_acta = page.get_by_text("Tipo de acta", exact=True).first.locator("xpath=following::button[1]")
    await seccion_tipo_acta.click()

    opcion = page.get_by_role("option", name="Estacionamiento Medido")
    await opcion.wait_for(state="visible")
    await opcion.click()
    await esperar_red(page)
    log("FILTRO", "✅ Tipo de acta = 'Estacionamiento Medido' aplicado")


async def seleccionar_paginado_50(page):
    """Fuerza el paginado a 50 por página (listbox tipo Headless UI)."""
    boton_paginado = page.locator("button:has-text('por página')").first
    await boton_paginado.click()
    opcion_50 = page.get_by_role("option", name="50 por página")
    await opcion_50.wait_for(state="visible")
    await opcion_50.click()
    await esperar_red(page)
    log("PAGINADO", "✅ Seteado a 50 por página")


async def preparar_grilla(page):
    """Atajo: aplica el filtro de tipo de acta + fuerza 50 por página.
    Llamar una sola vez al arrancar cualquier recorrido."""
    await filtrar_por_tipo_acta_estacionamiento(page)
    await seleccionar_paginado_50(page)


# ---------------------------------------------------------------------------
# Índices de columna / lectura de celdas
# ---------------------------------------------------------------------------
async def indice_columna(page, texto_header: str) -> Optional[int]:
    """Busca dinámicamente el índice de una columna por su header (más
    robusto a cambios de maquetado que asumir una posición fija)."""
    headers = page.locator(SELECTOR_HEADERS_TABLA)
    textos = await headers.all_inner_texts()
    for i, texto in enumerate(textos):
        if texto_header in texto.strip().lower():
            return i
    return None


async def indices_expediente_estado(page) -> tuple[Optional[int], Optional[int]]:
    idx_expediente = await indice_columna(page, TEXTO_HEADER_EXPEDIENTE)
    idx_estado = await indice_columna(page, TEXTO_HEADER_ESTADO)
    log("DEBUG-TABLA", f"Índice de columna resuelto -> expediente={idx_expediente}, estado={idx_estado}")
    return idx_expediente, idx_estado


async def leer_celdas_con_reintento(fila, indices: dict, intentos: int = 4, espera_ms: int = 400) -> dict:
    """
    Lee VARIAS columnas de la MISMA fila a la vez (ej. expediente + estado),
    reintentando sólo las que vienen vacías -- algunas columnas se
    completan con un fetch asíncrono después del render inicial de la fila.

    En cada intento se trae TODA la fila de una sola vez (all_inner_texts,
    un solo round-trip) en vez de un count()+nth()+inner_text() por
    columna y por intento.

    `indices`: dict clave -> índice de columna (o None, que se resuelve
    directo a None sin gastar ningún intento).
    """
    resultado = {clave: None for clave in indices}
    por_resolver = {clave: idx for clave, idx in indices.items() if idx is not None}

    for intento in range(1, intentos + 1):
        if not por_resolver:
            break
        textos_celdas = await fila.locator("td").all_inner_texts()
        for clave, idx in list(por_resolver.items()):
            if idx < len(textos_celdas):
                valor = textos_celdas[idx].strip()
                if valor:
                    resultado[clave] = valor
                    del por_resolver[clave]
        if por_resolver and intento < intentos:
            await fila.page.wait_for_timeout(espera_ms)

    return resultado


async def leer_primer_texto(locator) -> Optional[str]:
    """Trae el texto del primer match de un locator en un solo round-trip."""
    textos = await locator.all_inner_texts()
    return textos[0].strip() if textos else None


async def describir_elemento(locator) -> str:
    """Resumen (tag, atributos clave, outerHTML recortado) de un locator,
    para loguear cuando algo de la paginación no se comporta como se
    espera. Nunca revienta: si falla la inspección, devuelve el motivo."""
    try:
        return await locator.evaluate(
            "el => `<${el.tagName.toLowerCase()} "
            "aria-label=${JSON.stringify(el.getAttribute('aria-label'))} "
            "disabled=${el.disabled} "
            "class=${JSON.stringify(el.className)}>` "
            "+ el.outerHTML.slice(0, 300)"
        )
    except Exception as exc:
        return f"(no se pudo inspeccionar: {exc})"


# ---------------------------------------------------------------------------
# Detalle de una fila (abrir "Ver", pestaña "Actas", volver)
# ---------------------------------------------------------------------------
async def verificar_detalle_abierto(page, timeout: int = 8000) -> bool:
    """Confirma que el click en 'Ver' realmente navegó al detalle, en vez
    de asumirlo a ciegas apenas el click "sale" sin excepción (un click
    forzado vía JS puede salir sin excepción sin que la app reaccione).
    Se considera "detalle abierto" apenas aparece la pestaña 'Actas' O el
    botón 'Volver' -- lo que se pinte primero."""
    tab_actas = page.get_by_role("button", name="Actas", exact=True)
    boton_volver = page.locator(SELECTOR_BOTON_VOLVER)

    transcurrido, intervalo = 0, 250
    while transcurrido < timeout:
        if await tab_actas.count() or await boton_volver.count():
            return True
        await page.wait_for_timeout(intervalo)
        transcurrido += intervalo
    return False


async def abrir_detalle_de_fila(page, fila, etiqueta_log: str = "") -> bool:
    """
    Prueba varias formas de clickear el ojito, EN ORDEN, re-ubicando el
    locator del botón ANTES DE CADA estrategia (si la fila se re-renderiza
    entre estrategias -- ej. el fetch async que completa ESTADO -- un
    locator viejo puede seguir "vivo" apuntando a un nodo que ya no está,
    y el click sale sin excepción pero no hace nada).

    Devuelve True sólo si se CONFIRMÓ que el detalle abrió (ver
    verificar_detalle_abierto) -- nunca asume que "el click salió sin
    excepción" equivale a "funcionó". No toca la navegación si ninguna
    estrategia funciona (no hay go_back() acá: si nunca entramos al
    detalle, no hay nada de qué volver).
    """
    estrategias = [
        ("click normal", lambda loc: loc.first.click(timeout=4000)),
        ("click forzado (force=True)", lambda loc: loc.first.click(timeout=3000, force=True)),
        ("el.click() vía JS", lambda loc: loc.first.evaluate("el => el.click()")),
        ("MouseEvent despachado vía JS", lambda loc: loc.first.evaluate(
            "el => el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}))"
        )),
    ]

    for nombre, hacer_click in estrategias:
        candidatos = fila.locator(SELECTOR_BOTON_VER)  # SIEMPRE fresco
        if await candidatos.count() == 0:
            log("DEBUG-VER", f"  ⚠️ {etiqueta_log}'{nombre}': el botón 'Ver' ya no está en la fila "
                              f"(re-render entre estrategias) -- pruebo la siguiente")
            continue
        try:
            await hacer_click(candidatos)
        except Exception as exc:
            log("DEBUG-VER", f"  ⚠️ {etiqueta_log}'{nombre}' en 'Ver' falló: {exc}")
            continue

        try:
            await fila.wait_for(state="detached", timeout=6000)
        except PlaywrightTimeoutError:
            log("FILA", f"  ↻ {etiqueta_log}'{nombre}' no tuvo ningún efecto visible, "
                        f"probando la siguiente estrategia...")
            continue

        if await verificar_detalle_abierto(page, timeout=12000):
            if nombre != "click normal":
                log("FILA", f"  👁️ {etiqueta_log}el detalle abrió con '{nombre}'")
            return True

        log("FILA", f"  ⚠️ {etiqueta_log}'{nombre}': la fila se desprendió pero el detalle no "
                    f"pintó en 12s -- margen extra...")
        return await verificar_detalle_abierto(page, timeout=8000)

    log("FILA", f"  ⚠️ {etiqueta_log}ninguna estrategia de click abrió el detalle -- "
                f"salteo esta fila (seguimos en el listado, no se toca la navegación).")
    return False


async def abrir_tab_actas(page) -> bool:
    """Click en la pestaña 'Actas' del detalle. No alcanza con esperar
    'networkidle': en una SPA la red se calma antes de que el contenido
    del detalle termine de pintarse -- por eso se espera explícitamente a
    que la pestaña esté visible."""
    tab = page.get_by_role("button", name="Actas", exact=True)
    if await tab.count() == 0:
        tab = page.locator(SELECTOR_TAB_ACTAS_FALLBACK)

    try:
        await tab.first.wait_for(state="visible", timeout=15000)
    except Exception:
        log("DEBUG-ACTA", "⚠️ La pestaña 'Actas' no apareció en 15s -- el detalle "
                           "puede no haber terminado de cargar")
        return False

    await tab.first.click()
    await esperar_red(page)
    return True


async def leer_numero_acta(page, intentos: int = 5, espera_ms: int = 500) -> Optional[str]:
    """
    Abre la pestaña 'Actas' y devuelve el Nº de acta.

    Método principal: el label real 'Número acta' con su valor como
    hermano inmediato -- no depende de que el número tenga formato con
    puntos (ej. deja pasar actas cortas como '3', que el regex de puntos
    se perdía).
    Fallback: regex de formato con puntos de miles sobre el texto plano
    completo de la pestaña, por si el label cambia.
    """
    if not await abrir_tab_actas(page):
        return None

    label = page.locator(SELECTOR_LABEL_NUMERO_ACTA)
    texto = ""
    for intento in range(1, intentos + 1):
        if await label.count():
            valor_span = label.first.locator("xpath=following-sibling::span[1]")
            if await valor_span.count():
                valor = (await valor_span.first.inner_text()).strip()
                if valor:
                    return valor
        if intento < intentos:
            await page.wait_for_timeout(espera_ms)

    log("DEBUG-ACTA", f"No se encontró el label 'Número acta' tras {intentos} intentos -- "
                       f"caigo al regex de formato con puntos")
    for intento in range(1, intentos + 1):
        texto = await page.locator("body").inner_text()
        m = REGEX_NUMERO_CON_PUNTOS.search(texto)
        if m:
            return m.group(0)
        if intento < intentos:
            await page.wait_for_timeout(espera_ms)

    log("DEBUG-ACTA", f"No se encontró número de acta ni por label ni por regex. "
                       f"Primeros 500 chars:\n{texto[:500]}")
    return None


async def cerrar_detalle(page):
    """Vuelve del detalle al listado ('Volver' si aparece, go_back() como
    último recurso) y espera a que la tabla vuelva a estar lista."""
    boton_volver = page.locator(SELECTOR_BOTON_VOLVER)
    if await boton_volver.count() == 0:
        try:
            await boton_volver.first.wait_for(state="visible", timeout=3000)
        except Exception:
            pass
    if await boton_volver.count():
        await boton_volver.first.click()
        await esperar_red(page)
    else:
        await page.go_back()
        await esperar_red(page)
    await esperar_tabla_lista(page)


async def intentar_recuperacion_tras_error(page):
    """Best-effort: si algo revienta a mitad del procesamiento de una
    fila, intenta dejar la página en un estado sano (volver del detalle
    si estaba abierto) sin propagar un error nuevo si esto también
    falla."""
    try:
        boton_volver = page.locator(SELECTOR_BOTON_VOLVER)
        if await boton_volver.count():
            await boton_volver.first.click()
        await esperar_red(page)
        await esperar_tabla_lista(page)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Esperas de tabla / scroll perezoso
# ---------------------------------------------------------------------------
async def esperar_tabla_principal(page, timeout: int = 15000) -> bool:
    try:
        await page.locator(SELECTOR_TABLA_PRINCIPAL).first.wait_for(state="visible", timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        return False


async def esperar_tabla_lista(page, timeout: int = 15000, min_filas: int = 1):
    """Espera a que haya al menos `min_filas` filas visibles. Complementa
    a esperar_red: 'networkidle' puede cumplirse antes de que la grilla
    termine de pintarse."""
    try:
        await page.locator(SELECTOR_FILAS_RESULTADO).nth(min_filas - 1).wait_for(
            state="visible", timeout=timeout
        )
    except Exception:
        log("DEBUG-TABLA", f"⚠️ No aparecieron {min_filas} fila(s) en {timeout}ms")


async def scroll_y_detectar_filas_nuevas(page, intentos: int = 3, espera_ms: int = 600) -> bool:
    """Confirmado: este sitio NO tiene scroll infinito (pagina con
    'Siguiente'), pero se deja el chequeo por las dudas -- barato y
    defensivo ante un cambio futuro del sitio."""
    filas_antes = await page.locator(SELECTOR_FILAS_RESULTADO).count()
    for intento in range(1, intentos + 1):
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(espera_ms)
        filas_ahora = await page.locator(SELECTOR_FILAS_RESULTADO).count()
        if filas_ahora > filas_antes:
            log("PAGINADO", f"✅ Aparecieron filas nuevas al scrollear ({filas_antes} -> {filas_ahora})")
            return True
    return False


async def diagnosticar_paginador(page):
    """Vuelca el HTML real del contenedor de paginación una vez al
    arranque -- chequeo de salud si SIGI cambia el maquetado de la
    paginación en el futuro."""
    try:
        boton = await boton_siguiente(page)
        if await boton.count() == 0:
            log("DEBUG-PAGINADO", "⚠️ No se pudo ubicar 'Siguiente' para diagnosticar")
            return
        html = await boton.first.evaluate(
            "el => (el.closest('nav') || el.parentElement?.parentElement || "
            "el.parentElement || el).outerHTML.slice(0, 1500)"
        )
        log("DEBUG-PAGINADO", f"HTML real del contenedor de paginación:\n{html}")
    except Exception as exc:
        log("DEBUG-PAGINADO", f"⚠️ No se pudo diagnosticar la paginación: {exc}")


# ---------------------------------------------------------------------------
# Lectura del número de página actual / total de páginas
# ---------------------------------------------------------------------------
async def leer_numero_pagina_actual(page, intentos: int = 3, espera_ms: int = 300) -> Optional[int]:
    """Lee 'Mostrando A a B de C resultados' (deriva el número de página).
    Reintenta si lee 0/vacío -- puede ser un estado transitorio."""
    resultado = None
    for intento in range(1, intentos + 1):
        locator = page.get_by_text(PATRON_PAGINA_ACTUAL)
        total = await locator.count()
        if total == 0:
            resultado = None
        else:
            try:
                texto = await locator.first.inner_text(timeout=3000)
                match = PATRON_PAGINA_ACTUAL.search(texto)
                resultado = _pagina_desde_match(match) if match else None
            except PlaywrightTimeoutError:
                resultado = None

        if resultado:
            return resultado
        if intento < intentos:
            await page.wait_for_timeout(espera_ms)

    return resultado


async def leer_total_paginas(page) -> Optional[int]:
    """Lee el N de 'Página [n] de N' desde el input aria-label='Ir a la
    página' -- sólo existe/tiene sentido para el recorrido en reversa
    (saltar directo a la última página)."""
    input_pagina = page.locator(SELECTOR_INPUT_PAGINA)
    if await input_pagina.count() == 0:
        return None
    label = input_pagina.locator("xpath=ancestor::label[1]")
    try:
        texto = await label.first.inner_text(timeout=3000)
    except Exception:
        return None
    match = PATRON_TOTAL_PAGINAS.search(texto)
    if not match:
        return None
    return int(match.group(1).replace(".", "").replace(",", ""))


async def saltar_a_pagina(page, numero_pagina: int) -> bool:
    """Escribe el número de página en el input + Enter para saltar
    directo, en vez de clickear 'Siguiente' una por una. False (sin
    reventar) si el input no aparece o falla -- el llamador cae al
    método lento como red de seguridad."""
    input_pagina = page.locator(SELECTOR_INPUT_PAGINA)
    if await input_pagina.count() == 0:
        return False
    try:
        await input_pagina.fill(str(numero_pagina))
        await input_pagina.press("Enter")
    except Exception:
        return False
    await esperar_red(page)
    await esperar_tabla_lista(page)
    return True


# ---------------------------------------------------------------------------
# Botones Siguiente / Anterior
# ---------------------------------------------------------------------------
async def _boton_paginacion(page, texto_desktop: str, simbolo_mobile: str):
    """Ubica 'Siguiente' o 'Anterior'. Estrategia principal: subir desde
    el <span class="sm:hidden">símbolo</span> hasta su <button> ancestro
    (el texto está partido en 2 <span> según breakpoint responsive).
    Fallback: por posición relativa al texto de paginación."""
    span = page.locator(SELECTOR_SPAN_PAGINACION_MOBILE, has_text=re.compile(rf"^\s*{re.escape(simbolo_mobile)}\s*$"))
    total_spans = await span.count()

    if total_spans == 1:
        boton = span.locator("xpath=ancestor::button[1]")
        if await boton.count() == 1:
            return boton

    direccion_xpath = "following" if simbolo_mobile == ">" else "preceding"
    return page.get_by_text(PATRON_PAGINA_ACTUAL).first.locator(f"xpath={direccion_xpath}::button[1]")


async def boton_siguiente(page):
    return await _boton_paginacion(page, "Siguiente", ">")


async def boton_anterior(page):
    return await _boton_paginacion(page, "Anterior", "<")


async def _esperar_paginador_estable(page, timeout_ms: int = 8000, intervalo_ms: int = 300) -> bool:
    """Tras 'Volver' del detalle, el paginador puede flashear a '0 de 0'
    y DESAPARECER un rato mientras recarga. Si se consulta el botón
    'Siguiente'/'Anterior' justo en ese hueco, se concluye mal que no hay
    más páginas -- por eso se espera a que se estabilice antes."""
    transcurrido = 0
    while transcurrido < timeout_ms:
        span = page.locator(SELECTOR_SPAN_PAGINACION_MOBILE)
        if await span.count() >= 1:
            return True
        texto_pagina = page.get_by_text(PATRON_PAGINA_ACTUAL)
        if await texto_pagina.count() >= 1:
            texto = await texto_pagina.first.inner_text()
            match = PATRON_PAGINA_ACTUAL.search(texto)
            if match and int(match.group(3)) > 0:
                return True
        await page.wait_for_timeout(intervalo_ms)
        transcurrido += intervalo_ms
    return False


async def _hay_boton_habilitado(page, obtener_boton, intentos: int = 5, espera_ms: int = 500) -> bool:
    await _esperar_paginador_estable(page)
    boton = await obtener_boton(page)
    if await boton.count() == 0:
        return False
    for intento in range(1, intentos + 1):
        if await boton.is_enabled():
            return True
        if intento < intentos:
            await page.wait_for_timeout(espera_ms)
    return False


async def hay_pagina_siguiente(page) -> bool:
    return await _hay_boton_habilitado(page, boton_siguiente)


async def hay_pagina_anterior(page) -> bool:
    return await _hay_boton_habilitado(page, boton_anterior)


async def _ir_a_pagina(page, obtener_boton):
    boton = await obtener_boton(page)
    pagina_antes = await leer_numero_pagina_actual(page)
    await boton.click()
    await esperar_red(page)
    await esperar_tabla_lista(page)
    pagina_despues = await leer_numero_pagina_actual(page)
    if pagina_antes and pagina_despues and pagina_despues == pagina_antes:
        log("DEBUG-PAGINADO", f"⚠️ Hice click pero seguimos en la página {pagina_despues} "
                               f"-- el botón clickeado probablemente NO es el correcto")
    return pagina_despues


async def ir_a_pagina_siguiente(page):
    return await _ir_a_pagina(page, boton_siguiente)


async def ir_a_pagina_anterior(page):
    return await _ir_a_pagina(page, boton_anterior)


async def ir_a_ultima_pagina(page) -> int:
    """Para el recorrido en reversa: llega a la última página. Primero
    intenta saltar directo escribiendo el total de páginas en el input;
    si falla, cae a clickear 'Siguiente' hasta que se deshabilite."""
    total_paginas = await leer_total_paginas(page)
    pagina_actual = await leer_numero_pagina_actual(page) or 1

    if total_paginas:
        log("PAGINA", f"{total_paginas} página(s) en total -- salto directo a la última...")
        if await saltar_a_pagina(page, total_paginas):
            verificada = await leer_numero_pagina_actual(page)
            if verificada == total_paginas:
                log("PAGINA", f"📍 Última página alcanzada de un salto: {verificada}")
                return verificada
            pagina_actual = verificada or pagina_actual
        else:
            log("PAGINA", "⚠️ El salto directo falló -- recorro con 'Siguiente' (método lento)")
    else:
        log("PAGINA", "⚠️ No pude leer el total de páginas -- recorro con 'Siguiente' hasta la última")

    while await hay_pagina_siguiente(page):
        await ir_a_pagina_siguiente(page)
        pagina_actual += 1
    log("PAGINA", f"📍 Última página alcanzada: {pagina_actual}")
    return pagina_actual


async def asegurar_pagina(page, pagina_objetivo: int):
    """Si al volver del detalle la tabla se reseteó (ej. a la página 1),
    la recupera saltando directo o clickeando 'Siguiente' las veces que
    falten. Lectura rápida (intentos=1) a propósito: el widget flashea/
    desaparece tras volver, así que insistir con reintentos lentos acá
    sólo desperdicia tiempo en la enorme mayoría de casos donde no hubo
    reseteo."""
    actual = await leer_numero_pagina_actual(page, intentos=1, espera_ms=0)
    if not actual or actual == pagina_objetivo:
        return
    log("PAGINA", f"⚠️ Se reseteó a página {actual}, recuperando hasta {pagina_objetivo}...")

    if await saltar_a_pagina(page, pagina_objetivo):
        verificada = await leer_numero_pagina_actual(page)
        if verificada == pagina_objetivo:
            log("PAGINA", f"✅ Recuperada de un salto directo: {verificada}")
            return
        actual = verificada or actual

    while actual and actual < pagina_objetivo:
        if not await hay_pagina_siguiente(page):
            log("PAGINA", "❌ No pude recuperar la página objetivo: no hay 'Siguiente' habilitado")
            break
        await ir_a_pagina_siguiente(page)
        actual = await leer_numero_pagina_actual(page)
    log("PAGINA", f"✅ Recuperada, quedamos en página {actual}")


# ---------------------------------------------------------------------------
# Motor genérico de recorrido: reemplaza los DOS while anidados que
# estaban copiados en cada uno de los 4 scripts. `procesar_fila` es un
# callback async(pagina_actual, fila_idx) -> int (cuántos "hits" produjo
# esa fila, para el contador final del caller).
# ---------------------------------------------------------------------------
ProcesarFila = Callable[[int, int], Awaitable[int]]


async def recorrer_grilla(
    page,
    procesar_fila: ProcesarFila,
    direccion: str = "adelante",
    debe_continuar: Optional[Callable[[], bool]] = None,
) -> int:
    """
    Recorre TODA la grilla, página por página, fila por fila dentro de
    cada página (siempre de arriba hacia abajo), llamando a
    `procesar_fila(pagina_actual, fila_idx)` por cada una.

    direccion="adelante" -> arranca en la página 1, avanza con 'Siguiente'.
    direccion="reversa"  -> salta a la última página, retrocede con
                             'Anterior' (usado quien corre en paralelo con
                             el recorrido "adelante" desde el otro extremo).

    `debe_continuar`: callback sin argumentos que devuelve False para
    cortar el recorrido antes de tiempo (ej. "ya no quedan pendientes").
    Si no se pasa, se recorre la grilla completa siempre.

    Devuelve la suma de lo que fue devolviendo `procesar_fila`.
    """
    if direccion not in ("adelante", "reversa"):
        raise ValueError(f"direccion inválida: {direccion!r} (usar 'adelante' o 'reversa')")

    def _continuar() -> bool:
        return debe_continuar() if debe_continuar else True

    tabla_lista = await esperar_tabla_principal(page)
    if not tabla_lista:
        log("DEBUG-TABLA", "❌ La tabla con header 'Expediente ID' no apareció tras 15s")

    await diagnosticar_paginador(page)

    if direccion == "reversa":
        pagina_actual = await ir_a_ultima_pagina(page)
    else:
        pagina_actual = 1

    total = 0

    while _continuar():
        fila_idx = 0
        while _continuar():
            if fila_idx < TAMANO_PAGINA:
                await esperar_tabla_lista(page, min_filas=fila_idx + 1)
            filas = page.locator(SELECTOR_FILAS_RESULTADO)
            total_filas = await filas.count()

            if fila_idx >= total_filas:
                log("PAGINA", f"Se procesaron las {total_filas} filas visibles en la página "
                               f"{pagina_actual}. Probando si hay más por scroll...")
                if await scroll_y_detectar_filas_nuevas(page):
                    continue
                break

            total += await procesar_fila(pagina_actual, fila_idx)
            await asegurar_pagina(page, pagina_actual)
            fila_idx += 1

        if not _continuar():
            break

        if direccion == "adelante":
            if not await hay_pagina_siguiente(page):
                log("PAGINA", f"No hay más páginas (llegamos a la {pagina_actual}).")
                break
            await ir_a_pagina_siguiente(page)
            pagina_actual += 1
        else:
            if pagina_actual <= 1:
                log("PAGINA", "Llegamos a la página 1 -- fin del recorrido en reversa.")
                break
            if not await hay_pagina_anterior(page):
                log("PAGINA", f"No hay 'Anterior' habilitado en página {pagina_actual} -- fin.")
                break
            await ir_a_pagina_anterior(page)
            pagina_actual -= 1

    log("FIN", f"Recorrido ({direccion}) terminado: {total} resultado(s) acumulado(s)")
    return total


# ---------------------------------------------------------------------------
# Reintentos a nivel fila: envuelve un callback "intento único" con la
# misma política de reintentos que tenían los 3 scripts originales
# (timeout ubicando la fila, o "not attached to the DOM" tras un
# re-render) -- sin esto cada caller tendría que repetir el mismo
# try/except de 3 niveles.
# ---------------------------------------------------------------------------
IntentoFila = Callable[[], Awaitable[int]]


async def con_reintento_de_fila(
    page,
    pagina_actual: int,
    fila_idx: int,
    intento_unico: IntentoFila,
    max_intentos: int = 3,
) -> int:
    """
    Ejecuta `intento_unico()` (que debe volver a pedir `page.locator(...)`
    y `filas.nth(fila_idx)` DESDE CERO en cada llamada -- nunca reusar un
    locator de un intento anterior) hasta `max_intentos` veces, tratando
    como reintentable:
      - timeouts ubicando/operando sobre la fila (probable reload de
        grilla tras 'Volver'),
      - PlaywrightError "not attached to the DOM" (re-render de React a
        mitad de camino).
    Cualquier otro error se propaga tal cual -- ahí sí hace falta que el
    script corte y se vea el traceback.

    Si se agotan los intentos, loguea la fila como salteada, intenta
    dejar la página en un estado sano, y devuelve 0 (nunca revienta el
    recorrido completo por una fila puntual: con miles de filas, esto no
    es un caso raro, es matemática).
    """
    for intento in range(1, max_intentos + 1):
        try:
            return await intento_unico()

        except PlaywrightTimeoutError as exc:
            if intento >= max_intentos:
                log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: timeout "
                            f"{max_intentos} veces seguidas -- salteo. Error: {exc}")
                await intentar_recuperacion_tras_error(page)
                return 0
            log("FILA", f"  ↻ Página {pagina_actual}, fila {fila_idx + 1}: timeout, "
                        f"reintento {intento}/{max_intentos}...")
            await page.wait_for_timeout(1500)
            await esperar_tabla_lista(page, min_filas=fila_idx + 1)

        except PlaywrightError as exc:
            if "not attached to the DOM" not in str(exc):
                raise
            if intento >= max_intentos:
                log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: DOM re-renderizado "
                            f"{max_intentos} veces seguidas -- salteo. Error: {exc}")
                await intentar_recuperacion_tras_error(page)
                return 0
            log("FILA", f"  ↻ Página {pagina_actual}, fila {fila_idx + 1}: DOM cambió debajo "
                        f"nuestro, reintento {intento}/{max_intentos}...")
            await page.wait_for_timeout(500)
            await esperar_tabla_lista(page)

    return 0  # inalcanzable en la práctica