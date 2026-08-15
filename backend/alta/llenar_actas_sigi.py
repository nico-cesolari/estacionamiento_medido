"""
FUNCIONAL
Alta de expediente / estado en SIGI:
recorre TODA la tabla de SIGI (filtrada por Tipo de acta = Estacionamiento
Medido, 50 filas por página), fila por fila, y compara el Nº de acta de
cada fila contra el conjunto de actas pendientes en la base local. Apenas
encuentra una coincidencia, persiste el cambio en la DB al instante
(commit inmediato) y saca ese registro de la lista de pendientes.

  1) Junta de la DB todos los registros sin expediente (con patente).
  2) Filtra la grilla de SIGI por "Tipo de acta" = "Estacionamiento Medido"
     y fuerza el paginado a 50 por página.
  3) Recorre página por página, fila por fila: hace click en el ojito
     ("Ver"), entra a la pestaña "Actas", lee el primer "Número acta"
     (formato "351.937") y lo compara (normalizado) contra los pendientes.
  4) Si coincide -> guarda expediente/estado/motivo de esa fila y comitea
     ya mismo. Si no coincide con ninguno -> sigue a la fila siguiente.
  5) Corta apenas no quedan pendientes o se acaban las páginas.
  6) Lo que sigue pendiente al terminar el recorrido se marca 'no_cargada'.

  Después de volver del detalle, se chequea en qué página quedó parada
  la grilla realmente (por si SIGI resetea el paginado a la página 1 al
  volver) y, si no coincide con la esperada, se recupera clickeando
  'Siguiente' las veces que falten -- esto es lo que evita que el
  recorrido se quede pegado sin avanzar de página.

  Si el script se corta a mitad de camino, lo ya encontrado hasta ese
  punto queda guardado (commit inmediato por match) -- se puede volver a
  correr y arranca de nuevo desde los que sigan sin expediente.

Uso standalone:
    cd backend
    python alta/llenar_actas_sigi.py
    python alta/llenar_actas_sigi.py --commit
    caffeinate -i python alta/llenar_actas_sigi.py --commit
También se puede importar `ejecutar_alta(db, page)` desde otro script
(ver backend/app/pasos/procesar_actas_sigi.py) para correrlo dentro de
una sesión de navegador ya abierta, junto con la actualización.
"""
import argparse
import asyncio
import re
import sys
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import inspect as sa_inspect
from pathlib import Path
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import crud, models
from sistemas.sigi.reglas import reglas_sigi
from app.pasos.navegador import PaginaConSesion
from app.database import SessionLocal

URL_SIGI = "https://juzgado.villamaria.gob.ar/juzgado"
ARCHIVO_SESION = "sesion_sigi.json"

SELECTOR_TABLA_PRINCIPAL = "table:has(th:has-text('Expediente ID'))"
SELECTOR_FILAS_RESULTADO = f"{SELECTOR_TABLA_PRINCIPAL} tbody tr"
SELECTOR_HEADERS_TABLA = f"{SELECTOR_TABLA_PRINCIPAL} thead th"
TEXTO_HEADER_EXPEDIENTE = "expediente"   # matchea el header "EXPEDIENTE ID"
TEXTO_HEADER_ESTADO = "estado"           # matchea el header "ESTADO"

# Confirmado por HTML real (inspección del navegador): el botón "Ver" es
# un ícono de ojo -- un <svg> suelto, sin <button>/<a> que lo envuelva --
# con dos <path>, el segundo con fill-rule="evenodd" y d empezando en
# "M.664 10.59a1.651...". Ese path es el de Heroicons "EyeIcon" y es lo
# más estable para matchear: no depende de que la columna sea la última
# (por eso "td:last-child svg" NO lo encontraba -- en esta grilla la
# columna del ojo no es necesariamente la última td de la fila) ni de
# clases Tailwind (w-7 h-7 text-primary-500, que además podrían repetirse
# en otros íconos de acciones de la misma fila con el mismo tamaño/color).
SELECTOR_BOTON_VER = (
    "svg:has(path[d^='M.664 10.59a1.651']), "
    "td:last-child button, td:last-child a, td:last-child [role='button'], "
    "button[aria-label='Ver'], [title='Ver'], "
    "td:last-child svg"
)
# AJUSTAR sólo si el sitio no expone role="tab" (el fallback por texto ya
# cubre la mayoría de los casos). Confirmado por HTML real: es un <button>
# de texto "Actas" (sin role="tab"), que puede venir con aria-current="page"
# ya seleccionado por default al entrar al detalle.
SELECTOR_TAB_ACTAS_FALLBACK = "button:has-text('Actas'), a:has-text('Actas'), [class*='tab']:has-text('Actas')"
# AJUSTAR: todavía no vimos una captura de un acta en estado "Archivada",
# así que no sabemos dónde se muestra el motivo en ese caso. Si hace
# falta, mandar una captura de ese estado y se agrega la lectura acá.
SELECTOR_MOTIVO_EN_DETALLE = ".motivo-archivo, [data-campo='motivo']"
SELECTOR_BOTON_VOLVER = "button:has-text('Volver'), a:has-text('Volver')"
# Texto de paginación real (confirmado por HTML real, ver diagnóstico
# _diagnosticar_paginador): "Mostrando 1 a 50 de 167817 resultados" --
# NO existe ningún texto "Página X de Y" en el sitio (el patrón viejo
# nunca matcheaba nada, dejando ciega la detección de reseteo de página).
# El número de página no viene directo: se deriva del primer valor
# (el "1" de "Mostrando 1 a 50...") junto con TAMANO_PAGINA.
PATRON_PAGINA_ACTUAL = re.compile(r"Mostrando\s*(\d+)\s*a\s*(\d+)\s*de\s*(\d+)\s*resultados")
# Debe coincidir con lo que fuerza _seleccionar_paginado_50.
TAMANO_PAGINA = 50


def _pagina_desde_match(match: "re.Match") -> int:
    """A partir de 'Mostrando A a B de C resultados', calcula el número
    de página con A y TAMANO_PAGINA (ej. A=1 -> página 1, A=51 -> página
    2, A=101 -> página 3...)."""
    inicio = int(match.group(1))
    return (inicio - 1) // TAMANO_PAGINA + 1


def log(paso: str, msg: str):
    print(f"[{paso}] {msg}", flush=True)


async def _esperar_red(page, timeout: int = 10000):
    """Espera a que la red esté 'quieta', pero SIN reventar el script si
    tarda demasiado -- en una SPA con polling/websockets 'networkidle'
    puede no cumplirse nunca. Antes esto se usaba sin try/except en
    varios lugares y una sola espera larga cortaba todo el recorrido."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        log("DEBUG-RED", "⚠️ networkidle no se cumplió a tiempo, sigo igual "
                          "(puede ser polling/websockets de la SPA, no necesariamente un problema)")


def _clonar_registro(registro):
    """Crea una instancia NUEVA (sin PK) con los mismos datos que
    `registro`, para el caso en que una misma acta aparezca en SIGI
    asociada a más de un expediente: el primero pisa el registro
    'pendiente' original, y para los siguientes hace falta un registro
    aparte (mismo acta/patente, expediente y estado distintos). El
    modelo ya contempla este caso -- ver crud.anotar_duplicadas /
    crud._subquery_actas_duplicadas ("misma acta, dos expedientes")..
    """
    Modelo = type(registro)
    mapper = sa_inspect(Modelo)
    columnas_pk = {c.name for c in mapper.primary_key}
    datos = {
        col.key: getattr(registro, col.key)
        for col in mapper.column_attrs
    }
    return Modelo(**datos)


async def _indice_columna(page, texto_header: str) -> Optional[int]:
    """Busca dinámicamente el índice de una columna por su header (más
    robusto a cambios de maquetado que asumir una posición fija)."""
    headers = page.locator(SELECTOR_HEADERS_TABLA)
    textos = await headers.all_inner_texts()
    for i, texto in enumerate(textos):
        if texto_header in texto.strip().lower():
            return i
    return None


async def _leer_celda(fila, idx: Optional[int]) -> Optional[str]:
    if idx is None:
        return None
    celdas = fila.locator("td")
    if await celdas.count() > idx:
        return (await celdas.nth(idx).inner_text()).strip()
    return None


async def _verificar_detalle_abierto(page, timeout: int = 8000) -> bool:
    """Confirma que el click en 'Ver' realmente navegó al detalle, en vez
    de asumirlo a ciegas apenas el click "sale" sin excepción.

    Por qué hace falta: con un click forzado vía JS (`el.click()`) puede
    pasar que el evento salga sin lanzar ninguna excepción pero que la
    app NO haya reaccionado igual que con un click real del usuario (por
    ejemplo si el handler depende de una secuencia de eventos de puntero
    más completa). Si seguimos de largo asumiendo que sí funcionó, más
    abajo no se encuentra el botón 'Volver' (porque seguimos parados en
    el listado) y el código cae a `page.go_back()` -- que navega hacia
    atrás en el historial SIN HABER AVANZADO NUNCA, rompiendo el filtro y
    la paginación aplicados. Eso deja la tabla "rota" para todas las
    filas que siguen (se ve en los logs como filas sin datos en cadena a
    partir de cierto punto, y el texto 'Mostrando A a B de C resultados'
    que desaparece).

    Se considera "detalle abierto" apenas aparece la pestaña 'Actas' O el
    botón 'Volver' -- lo que se pinte primero.

    Se llama después de CADA estrategia de click sobre el ojito (ver
    _procesar_fila_con_reintento) -- antes estaba escrita pero nunca se
    invocaba, así que un click que "salía" sin excepción pero no abría
    nada pasaba desapercibido y terminaba en page.go_back() rompiendo
    filtro/paginación para el resto del recorrido."""
    tab_actas = page.get_by_role("button", name="Actas", exact=True)
    boton_volver = page.locator(SELECTOR_BOTON_VOLVER)

    transcurrido = 0
    intervalo = 250
    while transcurrido < timeout:
        if await tab_actas.count() or await boton_volver.count():
            return True
        await page.wait_for_timeout(intervalo)
        transcurrido += intervalo
    return False


async def _abrir_tab_actas(page):
    """Click en la pestaña 'Actas' del detalle (Expediente | Actas |
    Notificaciones | Notas).

    OJO: no alcanza con wait_for_load_state("networkidle") después de
    entrar al detalle -- en una SPA la red se puede quedar quieta ANTES
    de que el contenido del detalle termine de pintarse (nos quedamos
    viendo sólo el shell general de la app: header/sidebar). Por eso acá
    esperamos explícitamente a que la pestaña 'Actas' esté visible, con
    un timeout generoso, en vez de asumir que ya está lista.
    """
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
    # Después del click a la pestaña puede haber otro fetch -- esperamos
    # a que el contenido de esa pestaña (el texto del Nº de acta) también
    # esté presente, no sólo a que la red se calme.
    await _esperar_red(page, timeout=10000)
    return True


async def _leer_primer_numero_acta(page) -> Optional[str]:
    """Abre la pestaña 'Actas' y devuelve el primer Nº de acta con formato
    de puntos (ej. '351.937') que aparece en el texto de la página.

    OJO: el botón 'Actas' puede pintarse rápido (a veces ya viene
    seleccionado con aria-current="page"), pero el CONTENIDO de esa
    pestaña -- el número de acta en sí -- puede tardar un poco más en
    aparecer. Por eso reintentamos la lectura unas cuantas veces con una
    pausa corta, en vez de leer una sola vez apenas se resuelve el click.
    """
    abrio_tab = await _abrir_tab_actas(page)
    if not abrio_tab:
        return None

    intentos = 5
    espera_entre_intentos_ms = 500
    for intento in range(1, intentos + 1):
        texto = await page.locator("body").inner_text()
        numero = reglas_sigi.extraer_numero_acta_de_texto(texto)
        if numero:
            return numero
        if intento < intentos:
            await page.wait_for_timeout(espera_entre_intentos_ms)

    log("DEBUG-ACTA", f"Pestaña 'Actas' abierta pero no se encontró número de acta tras "
                       f"{intentos} intentos. Primeros 500 chars del texto leído:\n{texto[:500]}")
    return None


async def _filtrar_por_tipo_acta_estacionamiento(page):
    """Abre el panel de Filtros y selecciona 'Tipo de acta' = 'Estacionamiento
    Medido' (listbox tipo Headless UI)."""
    boton_filtros = page.locator("span:has-text('Filtros')").locator(
        "xpath=ancestor::button[1]"
    )
    if await boton_filtros.count():
        await boton_filtros.click()
    else:
        await page.locator("span:has-text('Filtros')").first.click()

    seccion_tipo_acta = page.get_by_text("Tipo de acta", exact=True).first.locator("xpath=following::button[1]")
    await seccion_tipo_acta.click()

    opcion = page.get_by_role("option", name="Estacionamiento Medido")
    await opcion.wait_for(state="visible")
    await opcion.click()
    await _esperar_red(page)
    log("FILTRO", "✅ Tipo de acta = 'Estacionamiento Medido' aplicado")


async def _seleccionar_paginado_50(page):
    """Fuerza el paginado a 50 por página (listbox tipo Headless UI)."""
    boton_paginado = page.locator("button:has-text('por página')").first
    await boton_paginado.click()
    opcion_50 = page.get_by_role("option", name="50 por página")
    await opcion_50.wait_for(state="visible")
    await opcion_50.click()
    await _esperar_red(page)
    log("PAGINADO", "✅ Seteado a 50 por página")


async def _leer_celda_con_reintento(fila, idx: Optional[int], intentos: int = 4, espera_ms: int = 400) -> Optional[str]:
    """Igual que _leer_celda, pero reintenta si viene vacía -- algunas
    columnas (ej. estado) se completan con un fetch asíncrono después del
    render inicial de la fila, y leer una sola vez puede agarrar el
    momento en que todavía está en blanco."""
    if idx is None:
        return None
    for intento in range(1, intentos + 1):
        celdas = fila.locator("td")
        if await celdas.count() > idx:
            valor = (await celdas.nth(idx).inner_text()).strip()
            if valor:
                return valor
        if intento < intentos:
            await fila.page.wait_for_timeout(espera_ms)
    return None  # se agotaron los intentos, quedó vacía de verdad


async def _leer_celdas_con_reintento(fila, indices: dict, intentos: int = 4,
                                      espera_ms: int = 400) -> dict:
    """
    Igual que _leer_celda_con_reintento, pero para VARIAS columnas de la
    MISMA fila a la vez (ej. expediente + estado). Antes cada columna
    tenía su propio loop de reintentos independiente, y cada intento hacía
    su propio count() + nth(idx) + inner_text() -- dos columnas x hasta 4
    intentos x 3 llamadas c/u = hasta 24 round-trips al browser por fila,
    sólo para leer dos celdas.

    Acá, en cada intento se trae TODA la fila de una sola vez
    (all_inner_texts, un solo round-trip) y sólo se reintentan las
    columnas que todavía vinieron vacías -- mismo criterio de espera que
    antes, mismo resultado final por columna, muchos menos viajes.

    `indices`: dict clave -> índice de columna (o None, que se resuelve
    directo a None sin gastar ningún intento, igual que antes).
    Devuelve un dict con las mismas claves, cada una con su valor (o None
    si se agotaron los intentos y quedó vacía).
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


async def _leer_primer_texto(locator) -> Optional[str]:
    """Trae el texto del primer match de un locator en un solo
    round-trip (`all_inner_texts`), en vez del count() + inner_text()
    por separado que se usaba antes para lo mismo (dos idas y vueltas al
    browser)."""
    textos = await locator.all_inner_texts()
    return textos[0].strip() if textos else None


async def _describir_elemento(locator) -> str:
    """Devuelve un resumen (tag, atributos clave, texto y outerHTML
    recortado) de un locator, para loguear cuando algo de la paginación
    no se comporta como se espera. Nunca revienta -- si falla la
    inspección, devuelve el motivo en vez de cortar el script."""
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


async def _leer_numero_pagina_actual(page, intentos: int = 3, espera_ms: int = 300) -> Optional[int]:
    """Lee 'Mostrando A a B de C resultados' de la paginación (y deriva
    el número de página con _pagina_desde_match) para saber en qué página
    quedamos parados realmente (por si 'Volver' del detalle resetea la
    paginación a la página 1).

    Reintenta si lee un valor "raro" (0 o vacío) -- puede ser un estado
    transitorio de carga, no la página real."""
    for intento in range(1, intentos + 1):
        locator = page.get_by_text(PATRON_PAGINA_ACTUAL)
        total = await locator.count()
        if total == 0:
            resultado = None
            if intento == intentos:
                log("DEBUG-PAGINADO", f"No se encontró NINGÚN texto que matchee "
                                       f"'{PATRON_PAGINA_ACTUAL.pattern}' en toda la página "
                                       f"tras {intentos} intentos -- el texto real de la "
                                       f"paginación debe ser distinto al esperado")
        else:
            try:
                # timeout corto y explícito: si count() encontró el
                # elemento pero se desprende del DOM antes de leerlo
                # (el widget "flashea" y desaparece al volver del
                # detalle), no tiene sentido esperar los 30s default de
                # Playwright -- mejor fallar rápido y dejar que el loop
                # de reintentos de esta función se encargue.
                texto = await locator.first.inner_text(timeout=3000)
                match = PATRON_PAGINA_ACTUAL.search(texto)
                resultado = _pagina_desde_match(match) if match else None
                if intento == 1:
                    log("DEBUG-PAGINADO", f"Texto de paginación encontrado ({total} match(es)): "
                                           f"{texto!r} -> página leída: {resultado}")
            except PlaywrightTimeoutError:
                resultado = None
                log("DEBUG-PAGINADO", f"⚠️ count()={total} encontró el texto de paginación pero "
                                       f"inner_text() se colgó (el elemento debe haberse "
                                       f"desprendido del DOM justo después) -- lo trato como "
                                       f"lectura fallida en el intento {intento}/{intentos}")

        if resultado:  # descarta tanto None como 0 (0 no es una página válida)
            return resultado
        if intento < intentos:
            await page.wait_for_timeout(espera_ms)

    return resultado  # último valor leído (puede ser None o 0), ya sin más reintentos


SELECTOR_SPAN_SIGUIENTE_MOBILE = "span.sm\\:hidden"


async def _boton_siguiente(page):
    """
    Ubica el botón 'Siguiente'.

    Estrategia principal: subir desde el <span class="sm:hidden">&gt;</span>
    (confirmado por inspección real del HTML) hasta su <button> ancestro.
    Es más específico que buscar 'el primer botón después del texto de
    paginación', porque no depende de qué otros elementos haya en el
    medio del DOM.

    Fallback: si no aparece ese span (o aparece más de uno, ambiguo), se
    usa la ubicación por posición como antes -- el botón que sigue al
    texto 'Mostrando A a B de C resultados'.

    Motivo original del enfoque por spans: el texto del botón está
    partido en dos <span> según el breakpoint responsive -- uno dice
    "Siguiente" (desktop, oculto en mobile) y el otro ">" (mobile,
    oculto en desktop). El nombre accesible que ve Playwright depende de
    cuál de los dos esté visible en el viewport con el que corre el
    navegador headless, así que buscar por name="Siguiente" puede no
    encontrar nada.
    """
    span_mobile = page.locator(SELECTOR_SPAN_SIGUIENTE_MOBILE, has_text=re.compile(r"^\s*>\s*$"))
    total_spans = await span_mobile.count()

    if total_spans == 1:
        boton = span_mobile.locator("xpath=ancestor::button[1]")
        if await boton.count() == 1:
            log("DEBUG-PAGINADO", "Botón 'Siguiente' ubicado por el span mobile ('>' / sm:hidden)")
            return boton
        log("DEBUG-PAGINADO", f"⚠️ El span '>' no tiene un <button> ancestro directo "
                               f"(count={await boton.count()}) -- caigo al selector por posición")
    elif total_spans > 1:
        log("DEBUG-PAGINADO", f"⚠️ Hay {total_spans} spans '>' con clase sm:hidden en la página "
                               f"(ambiguo, no sé cuál es 'Siguiente') -- caigo al selector por posición")
    else:
        log("DEBUG-PAGINADO", "No se encontró ningún span '>' con clase sm:hidden -- "
                               "caigo al selector por posición")

    return page.get_by_text(PATRON_PAGINA_ACTUAL).first.locator("xpath=following::button[1]")


async def _esperar_paginador_estable(page, timeout_ms: int = 8000, intervalo_ms: int = 300) -> bool:
    """Espera a que el paginador (el bloque 'Mostrando A a B de C
    resultados' + botón 'Siguiente') termine de recargar después de
    'Volver' del detalle.

    Se descubrió que, justo tras volver, ese widget puede flashear un
    instante a un estado "vacío" (0 resultados) y LUEGO DESAPARECER del
    DOM por completo mientras recarga -- bastante más tiempo del que
    contemplábamos (nuestros reintentos anteriores sumaban ~1s). Si
    `_hay_pagina_siguiente` consulta justo en ese hueco, no encuentra el
    botón y concluye -mal- que no hay más páginas. Por eso esta espera es
    bastante más paciente (hasta `timeout_ms`) y se usa específicamente
    antes de decidir si hay que paginar."""
    transcurrido = 0
    while transcurrido < timeout_ms:
        span = page.locator(SELECTOR_SPAN_SIGUIENTE_MOBILE, has_text=re.compile(r"^\s*>\s*$"))
        if await span.count() >= 1:
            return True

        texto_pagina = page.get_by_text(PATRON_PAGINA_ACTUAL)
        if await texto_pagina.count() >= 1:
            texto = await texto_pagina.first.inner_text()
            match = PATRON_PAGINA_ACTUAL.search(texto)
            if match and int(match.group(3)) > 0:  # total de resultados > 0 = ya cargó de verdad
                return True

        await page.wait_for_timeout(intervalo_ms)
        transcurrido += intervalo_ms

    log("DEBUG-PAGINADO", f"⚠️ El paginador no se estabilizó en {timeout_ms}ms "
                           f"(puede seguir en el flash '0 de 0' o directamente ausente)")
    return False


async def _hay_pagina_siguiente(page, intentos: int = 5, espera_ms: int = 500) -> bool:
    """Chequea si el botón 'Siguiente' está habilitado. Antes de mirar el
    botón, espera a que el paginador se haya estabilizado (ver
    _esperar_paginador_estable) -- si no, se puede consultar en pleno
    flash de recarga tras 'Volver' del detalle y concluir mal que no hay
    más páginas."""
    await _esperar_paginador_estable(page)

    boton_siguiente = await _boton_siguiente(page)
    total = await boton_siguiente.count()
    if total == 0:
        log("DEBUG-PAGINADO", "No se encontró el botón 'Siguiente' (ni por posición)")
        return False

    log("DEBUG-PAGINADO", f"Botón candidato encontrado: {await _describir_elemento(boton_siguiente)}")

    for intento in range(1, intentos + 1):
        habilitado = await boton_siguiente.is_enabled()
        if habilitado:
            log("DEBUG-PAGINADO", f"Botón 'Siguiente' habilitado (intento {intento})")
            return True
        if intento < intentos:
            await page.wait_for_timeout(espera_ms)

    log("DEBUG-PAGINADO", f"Botón 'Siguiente' encontrado pero deshabilitado tras {intentos} intentos "
                           f"-- asumo que es el final real de la tabla")
    return False


async def _esperar_tabla_lista(page, timeout: int = 15000, min_filas: int = 1):
    """Espera a que haya al menos `min_filas` filas de resultados
    visibles (por default, 1). Complementa (no reemplaza) a
    _esperar_red: 'networkidle' puede cumplirse ANTES de que la grilla
    termine de pintarse, sobre todo en SPAs donde el fetch de la tabla
    es uno más entre varios.

    Pedir un mínimo más alto que 1 importa justo después de volver del
    detalle: la grilla entera se recarga (ver _esperar_paginador_estable
    -- el paginador flashea a "0 de 0" en ese momento), y "al menos 1
    fila visible" se cumple mucho antes de que la fila puntual que
    necesitamos (ej. la Nº 25) haya vuelto a aparecer."""
    try:
        await page.locator(SELECTOR_FILAS_RESULTADO).nth(min_filas - 1).wait_for(
            state="visible", timeout=timeout
        )
    except Exception:
        log("DEBUG-TABLA", f"⚠️ No aparecieron {min_filas} fila(s) en {timeout}ms -- "
                            f"la tabla puede no haber cargado (o tener menos filas de las esperadas)")


async def _scroll_y_detectar_filas_nuevas(page, intentos: int = 3, espera_ms: int = 600) -> bool:
    """Hace scroll hacia el fondo unas cuantas veces y chequea si aparecen
    filas NUEVAS en la tabla (algunas grillas cargan más filas al
    scrollear -- lazy load -- antes de que haga falta tocar 'Siguiente').

    Devuelve True apenas detecta que creció la cantidad de filas.
    Si después de todos los intentos no cambió nada, devuelve False
    (ahí es cuando corresponde probar el botón 'Siguiente')."""
    filas_antes = await page.locator(SELECTOR_FILAS_RESULTADO).count()
    for intento in range(1, intentos + 1):
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(espera_ms)
        filas_ahora = await page.locator(SELECTOR_FILAS_RESULTADO).count()
        if filas_ahora > filas_antes:
            log("PAGINADO", f"✅ Aparecieron filas nuevas al scrollear "
                             f"({filas_antes} -> {filas_ahora}, intento {intento})")
            return True
    return False


async def _ir_a_pagina_siguiente(page):
    boton_siguiente = await _boton_siguiente(page)
    pagina_antes = await _leer_numero_pagina_actual(page)
    log("DEBUG-PAGINADO", f"Voy a clickear: {await _describir_elemento(boton_siguiente)}")
    await boton_siguiente.click()
    await _esperar_red(page)
    await _esperar_tabla_lista(page)
    pagina_despues = await _leer_numero_pagina_actual(page)
    if pagina_antes and pagina_despues and pagina_despues == pagina_antes:
        log("DEBUG-PAGINADO", f"⚠️ Hice click pero seguimos en la página {pagina_despues} "
                               f"(antes: {pagina_antes}) -- el botón clickeado probablemente "
                               f"NO es el de 'Siguiente' real")
    else:
        log("DEBUG-PAGINADO", f"Página antes del click: {pagina_antes} -> después: {pagina_despues}")


async def _asegurar_pagina(page, pagina_objetivo: int):
    """Si al volver del detalle la tabla no quedó en la página que
    esperábamos (ej. SIGI resetea el paginado a la página 1), la
    recupera clickeando 'Siguiente' las veces que falten. Esto es lo que
    evita que el recorrido se quede pegado sin avanzar de página.

    OJO: la lectura de página acá usa `intentos=1` (rápida, sin
    reintentos) a propósito -- el widget de paginación flashea a "0 de 0"
    y después DESAPARECE un rato tras volver del detalle (ver
    _esperar_paginador_estable), así que insistir acá con reintentos
    lentos sólo desperdicia tiempo sin ganar precisión: en la enorme
    mayoría de los casos esto no está reseteado, así que preferimos
    fallar rápido (asumir 'no se pudo determinar, no toco nada') antes
    que sumar ~1s de espera inútil por cada una de las miles de filas."""
    actual = await _leer_numero_pagina_actual(page, intentos=1, espera_ms=0)
    if not actual or actual == pagina_objetivo:  # 0 o None = no se pudo determinar, no arriesgamos nada
        return
    log("PAGINA", f"⚠️ Se reseteó a página {actual} al volver del detalle, "
                   f"recuperando hasta página {pagina_objetivo}...")
    while actual and actual < pagina_objetivo:
        if not await _hay_pagina_siguiente(page):
            log("PAGINA", "❌ No pude recuperar la página objetivo: no hay botón 'Siguiente' habilitado")
            break
        await _ir_a_pagina_siguiente(page)
        actual = await _leer_numero_pagina_actual(page)
    log("PAGINA", f"✅ Recuperada, quedamos en página {actual}")


async def _procesar_fila_con_reintento(
    db: Session,
    page,
    pagina_actual: int,
    fila_idx: int,
    idx_expediente: Optional[int],
    idx_estado: Optional[int],
    pendientes: dict,
    encontrados_por_acta: dict,
    commit: bool,
    expedientes_conocidos: set,
    max_intentos: int = 3,
) -> int:
    """
    Procesa UNA fila completa (scroll, abrir detalle, leer acta, comparar
    contra pendientes, volver) con reintentos si el DOM se la "come"
    debajo nuestro a mitad de camino.

    `expedientes_conocidos`: set de expedientes (normalizados, ver
    reglas_sigi.normalizar_expediente) que YA están guardados en la base
    -- se calcula una sola vez en ejecutar_alta con
    reglas_sigi.todos_los_expedientes_cargados(db) y se pasa en cadena
    hasta acá. Sirve para saltear la fila sin abrir el detalle cuando el
    expediente que muestra la grilla ya lo tenemos guardado (de una
    corrida anterior, o porque el mismo expediente aparece más de una vez
    en la grilla): abrir el detalle ahí no puede sumar ningún match
    nuevo, sólo cuesta el ciclo más caro del script.

    Por qué hace falta: después de cada 'Volver' del detalle, la tabla se
    vuelve a renderizar COMPLETA (React reemplaza los nodos, no los
    reutiliza). Si entre que ubicamos `fila = filas.nth(fila_idx)` y el
    momento en que efectivamente la usamos (scroll, click, lo que sea)
    ocurre un re-render, el handle que teníamos queda "not attached to
    the DOM". Con ~18.000 filas esto no es un caso raro, es matemática:
    tarde o temprano va a pasar, así que en vez de tratarlo como un error
    fatal lo tratamos como algo esperable y reintentable.

    La clave del reintento es que CADA intento vuelve a pedir
    `page.locator(...)` y `filas.nth(fila_idx)` DESDE CERO -- nunca
    reutiliza el locator/handle del intento anterior, que es justamente
    el que quedó viejo.

    Si se agotan los `max_intentos` intentos, se loguea la fila como
    salteada (no se cuenta como match, sigue de largo) en vez de
    reventar todo el recorrido.

    Devuelve 1 si hubo un match nuevo guardado/simulado en esta fila,
    0 en cualquier otro caso (incluida la fila salteada por reintentos
    agotados).
    """
    for intento in range(1, max_intentos + 1):
        try:
            filas = page.locator(SELECTOR_FILAS_RESULTADO)
            fila = filas.nth(fila_idx)
            await fila.scroll_into_view_if_needed()

            valores_fila = await _leer_celdas_con_reintento(
                fila, {"expediente": idx_expediente, "estado": idx_estado}
            )
            expediente_fila = valores_fila["expediente"]
            estado_fila = valores_fila["estado"]

            sufijo_reintento = f" (reintento {intento}/{max_intentos})" if intento > 1 else ""

            if expediente_fila is None and estado_fila is None:
                # Ambas columnas vacías después de que _leer_celdas_con_reintento
                # ya agotó SUS reintentos: no es un problema de timing de esa
                # función puntual, es la fila entera todavía sin poblar (placeholder
                # de carga tras un 'Volver' -- la grilla se repinta de a poco y las
                # últimas filas de la página son las que más tardan). Clickear
                # 'Ver' acá casi seguro cuelga 30s porque el botón ni existe todavía.
                if intento >= max_intentos:
                    log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: la fila "
                                f"sigue sin datos tras {max_intentos} intentos (probable "
                                f"placeholder de carga) -- salteo esta fila.")
                    return 0
                log("FILA", f"  ↻ Página {pagina_actual}, fila {fila_idx + 1}: fila sin "
                            f"datos aún (la grilla puede seguir recargando tras 'Volver'), "
                            f"reintento {intento}/{max_intentos}...")
                await page.wait_for_timeout(500)
                await _esperar_tabla_lista(page)
                continue  # próximo intento: vuelve a pedir `filas`/`fila` desde cero

            expediente_norm = reglas_sigi.normalizar_expediente(expediente_fila)
            if expediente_norm and expediente_norm in expedientes_conocidos:
                # Este expediente ya está guardado en la base (de una
                # corrida anterior, o porque el mismo expediente aparece
                # más de una vez en la grilla) -- abrir el detalle acá no
                # suma nada, sólo cuesta el ciclo más caro del script
                # (ojo -> Actas -> leer -> Volver) sin ninguna chance de
                # match nuevo. Se saltea directo.
                log("FILA", f"Página {pagina_actual}, fila {fila_idx + 1}: expediente "
                            f"{expediente_fila} ya está cargado en la base -- salteo sin abrir detalle.")
                return 0

            log("FILA", f"Página {pagina_actual}, fila {fila_idx + 1}: "
                        f"expediente={expediente_fila}, estado={estado_fila} -> "
                        f"abriendo detalle...{sufijo_reintento}")

            candidatos_ver = fila.locator(SELECTOR_BOTON_VER)
            total_candidatos_ver = await candidatos_ver.count()
            if total_candidatos_ver != 1:
                if total_candidatos_ver == 0:
                    # No alcanza con "primero: ..." si no hay ningún candidato --
                    # volcamos el outerHTML COMPLETO de la fila tal cual está en
                    # el DOM en este instante, para comparar contra lo que se ve
                    # en el inspector en vez de seguir ajustando el selector a
                    # ciegas (por ejemplo si la tabla es virtualizada y la fila
                    # se desmontó/remontó entre que leímos las celdas y ahora).
                    try:
                        html_fila = await fila.evaluate("el => el.outerHTML")
                    except Exception as exc:
                        html_fila = f"(no se pudo leer outerHTML: {exc})"
                    log("DEBUG-VER", f"  ⚠️ SELECTOR_BOTON_VER no matcheó NADA en esta fila. "
                                      f"outerHTML actual de la fila:\n{html_fila}")
                else:
                    log("DEBUG-VER", f"  ⚠️ SELECTOR_BOTON_VER matcheó {total_candidatos_ver} "
                                      f"elemento(s) en esta fila (se esperaba 1) -- primero: "
                                      f"{await _describir_elemento(candidatos_ver.first)}")

            if total_candidatos_ver == 0:
                if intento >= max_intentos:
                    log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: no "
                                f"apareció el botón 'Ver' tras {max_intentos} intentos "
                                f"-- salteo esta fila.")
                    return 0
                log("FILA", f"  ↻ Página {pagina_actual}, fila {fila_idx + 1}: el botón "
                            f"'Ver' no apareció a tiempo, reintento {intento}/{max_intentos}...")
                await page.wait_for_timeout(500)
                await _esperar_tabla_lista(page)
                continue

            # El ícono es un <svg> suelto (sin <button>/<a> que lo envuelva).
            # UN SOLO click "que sale sin excepción" NO garantiza que la app
            # haya reaccionado -- ver docstring de _verificar_detalle_abierto.
            # Por eso acá se prueban varias formas de clickear, EN ORDEN, y
            # después de cada una se chequea de verdad (con
            # _verificar_detalle_abierto) si el detalle abrió, en vez de
            # asumirlo. Sólo si ninguna estrategia funciona se da por
            # perdida la fila -- y, clave, sin tocar la navegación (no hay
            # ningún page.go_back() acá: si nunca entramos al detalle, no
            # hay nada de qué "volver").
            estrategias_click = [
                ("click normal", lambda loc: loc.first.click(timeout=4000)),
                ("click forzado (force=True)", lambda loc: loc.first.click(timeout=3000, force=True)),
                ("el.click() vía JS", lambda loc: loc.first.evaluate("el => el.click()")),
                ("MouseEvent despachado vía JS", lambda loc: loc.first.evaluate(
                    "el => el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}))"
                )),
            ]

            detalle_abierto = False
            for nombre_estrategia, hacer_click in estrategias_click:
                # Re-ubicado DESDE CERO en cada vuelta (no se reusa
                # `candidatos_ver`): si la fila se re-renderizó entre
                # estrategias (ej. el fetch async que completa la columna
                # ESTADO), el locator viejo puede seguir "vivo" mientras
                # apunta a un nodo que ya no está -- el click sale sin
                # excepción pero no hace nada. Esto es lo que causaba que
                # a veces el click al ojito pareciera no procesarse.
                candidatos_ver_fresco = fila.locator(SELECTOR_BOTON_VER)
                if await candidatos_ver_fresco.count() == 0:
                    log("DEBUG-VER", f"  ⚠️ '{nombre_estrategia}': el botón 'Ver' ya no está en la "
                                      f"fila al re-ubicarlo (re-render entre estrategias) -- "
                                      f"pruebo la siguiente estrategia")
                    continue
                try:
                    await hacer_click(candidatos_ver_fresco)
                except Exception as exc_click:
                    log("DEBUG-VER", f"  ⚠️ '{nombre_estrategia}' en 'Ver' falló: {exc_click}")
                    continue

                # El click "salió" sin excepción -- pero eso NO dice si
                # funcionó. Antes de decidir, miramos si la FILA se
                # desprendió del DOM: es la señal más rápida y confiable
                # de que React re-renderizó por una navegación real (más
                # rápida que esperar a que aparezca el contenido del
                # detalle). El timeout es generoso a propósito -- si el
                # sitio está lento, tarda, pero es MUCHO peor mandar un
                # SEGUNDO click mientras la navegación anterior todavía
                # está en curso (dos clicks pisándose es peor que uno que
                # tarda) que esperar de más acá.
                try:
                    await fila.wait_for(state="detached", timeout=6000)
                except PlaywrightTimeoutError:
                    log("FILA", f"  ↻ '{nombre_estrategia}' no tuvo ningún efecto visible "
                                f"(la fila sigue en el listado), probando la siguiente "
                                f"estrategia de click...")
                    continue

                # La fila se desprendió -- ya navegamos, no tiene sentido
                # probar otra forma de clickear (aterrizaría en la página
                # de detalle). Ahora esperamos, con margen extra generoso
                # por si el sitio está lento, a que el detalle termine de
                # pintarse.
                if await _verificar_detalle_abierto(page, timeout=12000):
                    detalle_abierto = True
                    if nombre_estrategia != "click normal":
                        log("FILA", f"  👁️ el detalle abrió con '{nombre_estrategia}' "
                                    f"(las estrategias anteriores no habían funcionado)")
                    break

                log("FILA", f"  ⚠️ '{nombre_estrategia}': la fila se desprendió del listado "
                            f"pero el detalle no terminó de pintarse en 12s -- puede ser el "
                            f"sitio lento, doy un margen extra antes de resignarme...")
                if await _verificar_detalle_abierto(page, timeout=8000):
                    detalle_abierto = True
                    log("FILA", f"  👁️ el detalle terminó de abrir con margen extra "
                                f"('{nombre_estrategia}')")
                break  # ya navegamos con esta estrategia, no probar otra click

            if not detalle_abierto:
                if intento >= max_intentos:
                    log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: ninguna "
                                f"estrategia de click abrió el detalle tras {max_intentos} "
                                f"intentos -- salteo esta fila (seguimos en el listado, no se "
                                f"toca la navegación).")
                    return 0
                log("FILA", f"  ↻ Página {pagina_actual}, fila {fila_idx + 1}: no se pudo abrir "
                            f"el detalle con ninguna estrategia de click, reintento "
                            f"{intento}/{max_intentos}...")
                await page.wait_for_timeout(500)
                await _esperar_tabla_lista(page)
                continue

            numero_acta = await _leer_primer_numero_acta(page)
            acta_norm = reglas_sigi.normalizar_acta(numero_acta) if numero_acta else None

            nuevos_encontrados = 0

            if not numero_acta:
                log("FILA", "  ❌ no se pudo leer el Nº de acta en el detalle")

            elif acta_norm and acta_norm in pendientes:
                # primer expediente que aparece para esta acta
                registro = pendientes.pop(acta_norm)

                motivo_texto = await _leer_primer_texto(page.locator(SELECTOR_MOTIVO_EN_DETALLE))
                cambios = reglas_sigi.armar_cambios_estado(estado_fila, motivo_texto)

                if commit:
                    registro.expediente = expediente_fila
                    if cambios:
                        reglas_sigi.aplicar_actualizacion(db, registro, cambios)
                    else:
                        db.add(registro)
                    db.commit()  # persistir YA, no esperar a que termine todo el recorrido
                    nuevos_encontrados = 1
                    log("FILA", f"  ✅ MATCH acta {numero_acta} -> patente {registro.patente} "
                                f"(expediente {expediente_fila}) -- guardado en DB. Faltan {len(pendientes)}.")
                else:
                    nuevos_encontrados = 1
                    log("FILA", f"  (dry-run) MATCH acta {numero_acta} -> patente {registro.patente} "
                                f"(expediente {expediente_fila}, cambios={cambios}) -- NO se graba. "
                                f"Faltan {len(pendientes)}.")

                encontrados_por_acta[acta_norm] = {"base": registro, "expedientes": {expediente_fila}}

            elif acta_norm and acta_norm in encontrados_por_acta:
                # esta acta YA tiene al menos un expediente guardado --
                # ¿el de esta fila es distinto? si es igual, no hacemos nada
                # (ya lo tenemos); si es distinto, se guarda como registro
                # aparte -- el modelo ya contempla "misma acta, expedientes
                # distintos" como dos filas separadas (ver crud.anotar_duplicadas).
                info_acta = encontrados_por_acta[acta_norm]
                ya_guardados = info_acta["expedientes"]
                if expediente_fila in ya_guardados:
                    log("FILA", f"  ↩ acta {numero_acta} ya guardada con este mismo expediente "
                                f"({expediente_fila}), no se duplica")
                else:
                    log("FILA", f"  ⚠️ acta {numero_acta} ya tenía expediente(s) {sorted(ya_guardados)}, "
                                f"aparece con expediente DISTINTO ({expediente_fila}) -- se agrega igual")

                    motivo_texto = await _leer_primer_texto(page.locator(SELECTOR_MOTIVO_EN_DETALLE))
                    cambios = reglas_sigi.armar_cambios_estado(estado_fila, motivo_texto)

                    if commit:
                        nuevo_registro = _clonar_registro(info_acta["base"])
                        nuevo_registro.expediente = expediente_fila
                        if cambios:
                            reglas_sigi.aplicar_actualizacion(db, nuevo_registro, cambios)
                        else:
                            db.add(nuevo_registro)
                        db.commit()
                        log("FILA", f"  ✅ registro nuevo guardado para acta {numero_acta} "
                                    f"(expediente {expediente_fila})")
                    else:
                        log("FILA", f"  (dry-run) se guardaría un registro nuevo para acta {numero_acta} "
                                    f"(expediente {expediente_fila}, cambios={cambios}), NO se graba")

                    ya_guardados.add(expediente_fila)
            else:
                log("FILA", f"  ↩ acta {numero_acta} no está entre las pendientes")

            boton_volver = page.locator(SELECTOR_BOTON_VOLVER)
            if await boton_volver.count() == 0:
                # Llegamos hasta acá con el detalle CONFIRMADO abierto (ver
                # _verificar_detalle_abierto más arriba), así que si 'Volver'
                # no aparece a la primera puede ser sólo timing -- se le da
                # un margen corto antes de recurrir a go_back().
                try:
                    await boton_volver.first.wait_for(state="visible", timeout=3000)
                except Exception:
                    pass
            if await boton_volver.count():
                await boton_volver.first.click()
                await _esperar_red(page)
            else:
                await page.go_back()
                await _esperar_red(page)
            await _esperar_tabla_lista(page)

            return nuevos_encontrados

        except PlaywrightTimeoutError as exc:
            # Un timeout ubicando/operando sobre la fila (ej.
            # scroll_into_view_if_needed esperando "nth(fila_idx)" sin
            # éxito) casi siempre es la grilla todavía recargándose tras
            # un 'Volver' -- el mismo fenómeno que "not attached to the
            # DOM" más abajo, sólo que acá Playwright nunca llegó a
            # encontrar el elemento para empezar. Se trata igual: se
            # reintenta hasta max_intentos en vez de tirar abajo toda la
            # corrida por una fila puntual.
            if intento >= max_intentos:
                log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: timeout ubicando/"
                            f"operando sobre la fila {max_intentos} veces seguidas -- salteo esta "
                            f"fila y sigo con la próxima. Error: {exc}")
                try:
                    boton_volver = page.locator(SELECTOR_BOTON_VOLVER)
                    if await boton_volver.count():
                        await boton_volver.first.click()
                    await _esperar_red(page)
                    await _esperar_tabla_lista(page)
                except Exception:
                    pass
                return 0

            log("FILA", f"  ↻ Página {pagina_actual}, fila {fila_idx + 1}: timeout ubicando/"
                        f"operando sobre la fila (probable reload de grilla tras 'Volver'), "
                        f"reintento {intento}/{max_intentos}...")
            await page.wait_for_timeout(1500)
            await _esperar_tabla_lista(page, min_filas=fila_idx + 1)
            # sigue el for -> el próximo intento vuelve a pedir `filas` y
            # `fila` desde cero

        except PlaywrightError as exc:
            # Sólo "tapamos" el error puntual de re-render a mitad de
            # camino -- cualquier otro error de Playwright (timeout real,
            # selector roto, etc.) se sigue propagando tal cual, porque
            # ahí SÍ hace falta que el script corte y se vea el traceback.
            if "not attached to the DOM" not in str(exc):
                raise

            if intento >= max_intentos:
                log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: el DOM se "
                            f"re-renderizó debajo nuestro {max_intentos} veces seguidas -- "
                            f"salteo esta fila y sigo con la próxima (no se puede determinar "
                            f"si tenía match). Error: {exc}")
                # No sabemos en qué paso se cortó (puede haber quedado a
                # mitad del detalle). Intentamos dejar la página en un
                # estado sano para la fila siguiente; si esto también
                # falla, no revienta el script -- ya perdimos esta fila,
                # no vale la pena perder también todo el recorrido.
                try:
                    boton_volver = page.locator(SELECTOR_BOTON_VOLVER)
                    if await boton_volver.count():
                        await boton_volver.first.click()
                    await _esperar_red(page)
                    await _esperar_tabla_lista(page)
                except Exception:
                    pass
                return 0

            log("FILA", f"  ↻ Página {pagina_actual}, fila {fila_idx + 1}: el DOM cambió "
                        f"debajo nuestro (fila no attached), reintento {intento}/{max_intentos}...")
            await page.wait_for_timeout(500)
            await _esperar_tabla_lista(page)
            # sigue el for -> el próximo intento vuelve a pedir `filas` y
            # `fila` desde cero, ya con el DOM re-renderizado

    return 0  # inalcanzable en la práctica (el for siempre retorna o relanza), por las dudas


async def _esperar_tabla_principal(page, timeout: int = 15000) -> bool:
    """Espera a que aparezca la tabla anclada (la que tiene el header
    'Expediente ID') antes de calcular índices de columna o loguear
    diagnósticos.

    Por qué hace falta: aplicar el filtro de 'Tipo de acta' y el paginado
    de 50 dispara un fetch nuevo -- _esperar_red espera a que la RED esté
    quieta, pero eso no garantiza que React ya haya vuelto a pintar la
    tabla (headers incluidos) con los datos nuevos. Si _indice_columna
    corre en esa ventana, page.locator(SELECTOR_HEADERS_TABLA) puede
    devolver una lista vacía o incompleta -> no encuentra 'expediente' ni
    'estado' en ningún header -> los dos índices quedan en None -> cada
    celda se lee como None -> se ve como 'fila sin datos' en cadena en
    TODAS las filas, aunque los datos sí estén ahí, simplemente todavía
    no llegamos a leerlos bien."""
    try:
        await page.locator(SELECTOR_TABLA_PRINCIPAL).first.wait_for(state="visible", timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        return False


async def _diagnosticar_paginador(page):
    """Vuelca el HTML real del contenedor de paginación una sola vez, al
    arranque del recorrido -- útil como chequeo de salud si en el futuro
    SIGI cambia el HTML de la paginación (el patrón real ya fue
    encontrado y corregido: 'Mostrando A a B de C resultados', ver
    PATRON_PAGINA_ACTUAL)."""
    try:
        boton_siguiente = await _boton_siguiente(page)
        if await boton_siguiente.count() == 0:
            log("DEBUG-PAGINADO", "⚠️ No se pudo ubicar el botón 'Siguiente' para "
                                   "diagnosticar el contenedor de paginación.")
            return
        html_contenedor = await boton_siguiente.first.evaluate(
            "el => (el.closest('nav') || el.parentElement?.parentElement || "
            "el.parentElement || el).outerHTML.slice(0, 1500)"
        )
        log("DEBUG-PAGINADO", f"HTML real del contenedor de paginación (para ajustar "
                               f"PATRON_PAGINA_ACTUAL):\n{html_contenedor}")
    except Exception as exc:
        log("DEBUG-PAGINADO", f"⚠️ No se pudo diagnosticar el contenedor de paginación: {exc}")


async def _procesar_todas_las_paginas(db: Session, page, pendientes: dict, commit: bool,
                                       expedientes_conocidos: set) -> dict:
    """
    Recorre la tabla completa de SIGI, fila por fila.

    Al terminar de recorrer las filas actualmente renderizadas:
      1) primero intenta SCROLL varias veces por si la grilla carga más
         filas de forma perezosa (lazy load) -- si aparecen filas
         nuevas, sigue leyendo sin tocar el paginado.
      2) si el scroll no trae filas nuevas, recién ahí prueba el botón
         'Siguiente' (paginado clásico).

    Sobre una acta que YA fue encontrada antes: si vuelve a aparecer en
    otra fila con un Nº de EXPEDIENTE distinto al ya guardado, no se
    ignora -- se guarda como un registro aparte (misma acta/patente,
    expediente y estado de esa fila puntual). Si el expediente es el
    mismo que ya se guardó, se lo saltea (ya lo tenemos).

    Devuelve los pendientes (por acta, sin ningún match todavía) que NO
    se encontraron.
    """
    tabla_lista = await _esperar_tabla_principal(page)
    if not tabla_lista:
        log("DEBUG-TABLA", "❌ La tabla con header 'Expediente ID' no apareció tras 15s de "
                            "espera -- puede que el texto del header haya cambiado, o que la "
                            "grilla siga cargando por algún motivo distinto (revisar filtro/"
                            "paginado aplicados antes de este punto).")

    total_tablas_dom = await page.locator("table").count()
    total_tablas_ancladas = await page.locator(SELECTOR_TABLA_PRINCIPAL).count()
    log("DEBUG-TABLA", f"<table> en el DOM: {total_tablas_dom} total, "
                        f"{total_tablas_ancladas} con header 'Expediente ID'")
    if total_tablas_dom > 1:
        log("DEBUG-TABLA", "⚠️ Hay más de una <table> en el DOM -- si el selector no "
                            "estuviera anclado a la que tiene 'Expediente ID', se habrían "
                            "mezclado filas/headers de tablas distintas.")
    if total_tablas_ancladas == 0:
        log("DEBUG-TABLA", "❌ Ninguna tabla tiene un header con el texto 'Expediente ID' "
                            "-- revisar si el texto del header cambió o si la tabla todavía "
                            "no terminó de cargar en este punto del script.")
    elif total_tablas_ancladas > 1:
        log("DEBUG-TABLA", f"⚠️ {total_tablas_ancladas} tablas distintas tienen un header "
                            f"'Expediente ID' -- el selector puede seguir ambiguo, hace "
                            f"falta un texto de header más específico o filtrar por visibilidad.")

    idx_expediente = await _indice_columna(page, TEXTO_HEADER_EXPEDIENTE)
    idx_estado = await _indice_columna(page, TEXTO_HEADER_ESTADO)
    log("DEBUG-TABLA", f"Índice de columna resuelto -> expediente={idx_expediente}, estado={idx_estado}")

    await _diagnosticar_paginador(page)

    pagina_actual = 1
    encontrados = 0
    # acta_norm -> {"base": Registro original (para clonar), "expedientes": set(...)}
    # Guardamos el registro ya encontrado en memoria (no hace falta volver
    # a pedirlo a la DB: es el mismo objeto Python que ya tenemos).
    encontrados_por_acta: dict[str, dict] = {}

    while pendientes:
        fila_idx = 0
        while pendientes:
            # Antes de contar filas, esperamos a que la fila que vamos a
            # necesitar (fila_idx) haya vuelto a aparecer. Esto es lo que
            # evita pisar el reload de la grilla tras 'Volver' del
            # detalle (ver _esperar_paginador_estable): sin esto,
            # total_filas puede leerse en pleno reload y quedar
            # "confirmando" una fila que en realidad todavía no repintó.
            #
            # OJO: esto sólo tiene sentido mientras fila_idx todavía
            # puede existir en esta página (< TAMANO_PAGINA). Una vez que
            # llegamos al techo del paginado no tiene caso esperar los
            # 15s default por una fila 51 que estructuralmente no puede
            # aparecer -- el sitio pagina con el botón 'Siguiente', no
            # con scroll infinito (confirmado: _scroll_y_detectar_filas_nuevas
            # nunca encuentra filas nuevas en este sitio). Saltamos
            # directo a leer total_filas y probar 'Siguiente'.
            if fila_idx < TAMANO_PAGINA:
                await _esperar_tabla_lista(page, min_filas=fila_idx + 1)
            filas = page.locator(SELECTOR_FILAS_RESULTADO)
            total_filas = await filas.count()

            if fila_idx >= total_filas:
                log("PAGINA", f"Se procesaron las {total_filas} filas visibles en la página "
                               f"{pagina_actual}. Probando si hay más por scroll...")
                if await _scroll_y_detectar_filas_nuevas(page):
                    continue  # vuelve a leer total_filas, ahora más grande, sin tocar 'Siguiente'
                break  # no aparecieron filas nuevas -> se prueba 'Siguiente' más abajo

            encontrados += await _procesar_fila_con_reintento(
                db, page, pagina_actual, fila_idx,
                idx_expediente, idx_estado,
                pendientes, encontrados_por_acta, commit,
                expedientes_conocidos,
            )

            await _asegurar_pagina(page, pagina_actual)
            fila_idx += 1

        if not pendientes:
            break
        if not await _hay_pagina_siguiente(page):
            log("PAGINA", f"No hay más páginas (llegamos a la {pagina_actual}).")
            break
        await _ir_a_pagina_siguiente(page)
        pagina_actual += 1

    log("FIN", f"Recorrido terminado: {encontrados} encontrados, {len(pendientes)} sin coincidencia")
    return pendientes


async def ejecutar_alta(db: Session, page, commit: bool = True) -> dict:
    """
    Núcleo del paso de alta, pensado para reutilizarse con una sesión de
    navegador ya abierta (ver backend/app/pasos/procesar_actas_sigi.py).
    Recorre TODA la tabla de SIGI una sola vez, fila por fila, buscando
    coincidencias contra las actas pendientes de la DB.

    commit=True  -> cada match se persiste al instante en la DB, y lo
                    que sigue pendiente al final se marca 'no_cargada'.
    commit=False -> dry-run: NO se toca la DB para nada (ni matches ni
                    'no_cargada'), sólo se loguea qué habría pasado.
    """
    modo = "COMMIT (graba de verdad)" if commit else "DRY-RUN (no toca la base)"
    log("INICIO", f"Modo: {modo}")

    registros_totales = list(reglas_sigi.registros_sin_expediente(db))
    registros_con_patente = [r for r in registros_totales if r.patente]
    sin_patente = len(registros_totales) - len(registros_con_patente)

    pendientes = {reglas_sigi.normalizar_acta(r.acta): r for r in registros_con_patente}
    total_inicial = len(pendientes)
    log("INICIO", f"{total_inicial} acta(s) pendiente(s) con patente "
                  f"({sin_patente} sin patente, van directo a sin_coincidencia)")

    # Expedientes que YA están guardados en la base (de una corrida
    # anterior u otro registro) -- se usa para saltear filas de la grilla
    # sin abrir el detalle cuando el expediente ya lo tenemos. Se calcula
    # una sola vez acá (no adentro del loop de filas) porque es la misma
    # consulta para las ~171mil filas que se van a recorrer.
    expedientes_conocidos = set(reglas_sigi.todos_los_expedientes_cargados(db).keys())
    log("INICIO", f"{len(expedientes_conocidos)} expediente(s) ya cargado(s) en la base "
                  f"(se saltean sin abrir detalle si aparecen en la grilla)")

    await _filtrar_por_tipo_acta_estacionamiento(page)
    await _seleccionar_paginado_50(page)

    no_encontrados = await _procesar_todas_las_paginas(db, page, pendientes, commit, expedientes_conocidos)

    sin_coincidencia = sin_patente
    for registro in no_encontrados.values():
        if commit:
            log("SIN-COINCIDENCIA", f"acta {registro.acta} (patente {registro.patente}) -> se marca 'no_cargada'")
            if reglas_sigi.marcar_sin_coincidencia(db, registro):
                db.commit()
        else:
            log("SIN-COINCIDENCIA", f"(dry-run) acta {registro.acta} (patente {registro.patente}) "
                                     f"-> se marcaría 'no_cargada', NO se graba")
        sin_coincidencia += 1

    encontrados = total_inicial - len(no_encontrados)

    resumen = {
        "altas_expediente": encontrados,
        "actualizados": encontrados,
        "sin_cambios": 0,
        "sin_coincidencia": sin_coincidencia,
        "errores": 0,
    }
    log("RESUMEN", str(resumen))
    return resumen


async def _main(commit: bool):
    """Corrida standalone: abre su propia sesión de navegador y su propia
    sesión de DB. Si commit=True, los matches ya se comitean al instante
    dentro de ejecutar_alta, así que acá no hace falta un commit final.
    Si commit=False, la DB no se toca en ningún momento."""
    db = SessionLocal()
    try:
        async with PaginaConSesion(ARCHIVO_SESION, URL_SIGI) as page:
            resumen = await ejecutar_alta(db, page, commit=commit)
        log("FIN", str(resumen))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                         help="Graba en la DB de verdad. Sin este flag, corre en dry-run: "
                              "recorre y loguea todo pero NO toca la base.")
    args = parser.parse_args()
    asyncio.run(_main(commit=args.commit))