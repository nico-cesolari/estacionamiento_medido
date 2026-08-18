#!/usr/bin/env python3
"""
FUNCIONAL (revisar los puntos marcados con AJUSTAR antes de la primera corrida)
llenar_actas_sigi.py
------------------------------------
Variante optimizada de llenar_actas_sigi.py / cargar_actas_sigi.py: en vez
de recorrer TODA la grilla de SIGI (paginada de a 50, abriendo el detalle
de cada fila una por una), aprovecha que:

  - el número de expediente es correlativo dentro de cada año
    (EXP-2026-176985, EXP-2026-176984, ...), y
  - SIGI tiene un buscador individual por expediente (filtro + Enter),

para saltearse por completo las actas que YA están en la base, sin
siquiera buscarlas -- solo se abre el detalle de las que hacen falta.

Flujo:
  1. Filtra la grilla por Tipo de acta = "Estacionamiento Medido" (igual
     que siempre). NO hace falta tocar el paginado: no se recorren
     páginas, solo se lee la primera fila.
  2. Lee el expediente de esa primera fila (la más reciente, arriba de
     todo) -- ej. "EXP-2026-176985" -- y extrae año (2026) y número más
     alto (176985).
  3. Recorre los números desde --desde (el más bajo, punto de arranque;
     si no se pasa, usa 865 por default histórico) hacia ARRIBA, hasta
     --hasta (si se pasa) o hasta el máximo leído de la grilla (si no).
  4. Para cada número arma el expediente completo (ej. "EXP-2026-176984"):
       - si YA está en la base (según reglas_sigi.todos_los_expedientes_
         cargados) -> se IGNORA, ni se busca. Esto es lo que ahorra
         tiempo respecto a recorrer toda la grilla.
       - si NO está -> se escribe el string COMPLETO en el buscador por
         expediente y se confirma. El buscador de SIGI filtra por
         PREFIJO/SUBSTRING, no por igualdad exacta -- buscar
         "EXP-2026-1785" puede devolver también "EXP-2026-17850",
         "EXP-2026-17851", etc, y potencialmente más de 50 resultados
         (más de una página). Por eso `buscar()` recorre TODAS las filas
         devueltas, en TODAS las páginas del resultado filtrado, y sólo
         acepta la fila cuyo expediente normalizado matchea EXACTO al
         pedido. Si encuentra una fila así, se abre el detalle, se lee
         el número de acta, motivo, patente, dirección y fecha, y se da
         de alta -- o se clona el registro si el acta ya existía con
         otro expediente (mismo criterio que cargar_actas_sigi.py). Si
         no hay ninguna fila con match exacto en ninguna página, se
         cuenta como "no encontrado" (ese expediente no existe o no es
         de este tipo de acta) y se sigue con el número siguiente.

Uso:
    cd backend
    python alta/llenar_actas_sigi.py                    # dry-run
    python alta/llenar_actas_sigi.py --commit
    python alta/llenar_actas_sigi.py --commit --desde 23389 --hasta 144123
    python alta/llenar_actas_sigi.py --commit --limit 5   # probar con pocos

------------------------------------------------------------------------
Selector del input de expediente (confirmado por HTML real):
------------------------------------------------------------------------
    <label>Número de expediente</label>
    <input name="numero_expediente" placeholder="Ej: EXP-2026-0080" ...>

Dos cosas importantes que salen de ese HTML:
  - El input tiene `name="numero_expediente"` -- se ubica por ahí en vez
    de por label/placeholder (más estable).
  - El placeholder ("EXP-2026-0080") muestra que el número va con
    PADDING A 4 DÍGITOS como mínimo (0080, no 80) -- por eso
    _armar_expediente rellena con ceros a la izquierda hasta 4 dígitos
    (los expedientes de 5+ dígitos, como 176985, quedan igual, sin
    padding extra).

Si en algún expediente real la longitud del padding resultara distinta
(ej. 5 dígitos fijos en vez de 4), ajustar el `:04d` de _armar_expediente.

------------------------------------------------------------------------
AJUSTAR -- comportamiento del buscador por expediente (visto en la
práctica, corridas reales):
------------------------------------------------------------------------
  - Match por PREFIJO, no exacto: buscar "EXP-2026-1785" trae también
    "EXP-2026-17850..17859" y variantes de más dígitos. `buscar()` ya
    contempla esto (ver más arriba y la función en sí).
  - La PRIMERA búsqueda por expediente de toda la corrida es sensiblemente
    más lenta que las siguientes (la SPA viene de mostrar la grilla sin
    filtrar, con muchas más páginas, y tarda más en asentarse: se vieron
    timeouts de 30s clickeando "Siguiente" por botón inestable/
    deshabilitado, y "no aparecieron filas en 15000ms"). Por eso
    `ejecutar()` marca esa primera llamada real a `buscar()` (la del
    primer número que no viene ya cargado en la base, sea cual sea) con
    `primera_busqueda_global=True`: ahí se usan más intentos, más espera,
    y se espera explícitamente a que aparezca al menos una fila en vez de
    una pausa fija corta. Si en algún entorno esto no alcanza, subir los
    valores mínimos que fija `buscar()` cuando ese flag es True (intentos
    >= 6, espera_ms >= 2500, timeout de esperar_tabla_lista = 20s).
"""
import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Optional
import json
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.database import SessionLocal
from app.services.sistemas.comun.sesion import PaginaConSesion
from app.paths import CARPETA_SESIONES_API_REST_PAYMENT
from app.services.sistemas.sigi.reglas import reglas_sigi
from app.services.sistemas.sigi.web import web_sigi

URL_SIGI = "https://juzgado.villamaria.gob.ar/juzgado"
ARCHIVO_SESION = "sesion_sigi.json"
CARPETA_ARCHIVOS = Path(__file__).resolve().parent.parent / "archivos"
ARCHIVO_ACTAS_IGNORADAS = CARPETA_ARCHIVOS / "actas_sigi_ignoradas.json"
# Selector confirmado por HTML real (ver docstring del módulo):
#   <input name="numero_expediente" placeholder="Ej: EXP-2026-0080" ...>
SELECTOR_INPUT_FILTRO_EXPEDIENTE = "input[name='numero_expediente']"
LABEL_FILTRO_EXPEDIENTE = "Número de expediente"

REGEX_EXPEDIENTE = re.compile(r"EXP-(\d{4})-(\d+)", re.IGNORECASE)

# El placeholder ("EXP-2026-0080") muestra que el número lleva padding a
# 4 dígitos como mínimo. Los expedientes con 5+ dígitos no se ven
# afectados por el padding ({:04d} solo agrega ceros si hace falta).
DIGITOS_PADDING_NUMERO = 4

# AJUSTAR: umbral para detectar cuando el filtro por expediente NO se
# aplicó de verdad (fill()+Enter no disparó la búsqueda, y la grilla
# sigue mostrando el TOTAL sin filtrar -- miles/decenas de miles de
# resultados -- en vez de uno o unos pocos). Cualquier búsqueda real por
# un expediente puntual (aunque matchee por prefijo varios similares)
# debería devolver, como mucho, unos pocos cientos de filas -- nunca
# decenas de miles. Visto en la práctica: la grilla sin filtrar mostraba
# ~176985 resultados; 500 da margen de sobra sin arriesgarse a confundir
# un resultado filtrado legítimo con uno sin filtrar.
UMBRAL_TOTAL_SIN_FILTRAR = 500


def _descomponer_expediente(expediente: str):
    """'EXP-2026-176985' o 'EXP-2026-0080' -> ('2026', 176985) / ('2026', 80).
    None si no matchea el formato."""
    if not expediente:
        return None
    m = REGEX_EXPEDIENTE.search(expediente.strip())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _armar_expediente(anio: str, numero: int) -> str:
    return f"EXP-{anio}-{numero:0{DIGITOS_PADDING_NUMERO}d}"
def cargar_actas_sigi_ignoradas() -> list[str]:
    """
    Carga los expedientes ignorados desde actas_sigi_ignoradas.json.

    Si el archivo no existe, lo crea con una lista vacía.
    """
    CARPETA_ARCHIVOS.mkdir(parents=True, exist_ok=True)

    if not ARCHIVO_ACTAS_IGNORADAS.exists():
        ARCHIVO_ACTAS_IGNORADAS.write_text(
            "[]\n",
            encoding="utf-8",
        )
        return []

    try:
        contenido = ARCHIVO_ACTAS_IGNORADAS.read_text(
            encoding="utf-8"
        ).strip()

        if not contenido:
            return []

        datos = json.loads(contenido)

        if not isinstance(datos, list):
            web_sigi.log(
                "IGNORADAS",
                f"⚠️ {ARCHIVO_ACTAS_IGNORADAS} no contiene una lista válida. "
                "Se utilizará una lista vacía.",
            )
            return []

        return datos

    except json.JSONDecodeError as exc:
        web_sigi.log(
            "IGNORADAS",
            f"⚠️ No se pudo leer {ARCHIVO_ACTAS_IGNORADAS}: {exc}. "
            "Se utilizará una lista vacía.",
        )
        return []


def guardar_actas_sigi_ignoradas(
    actas_ignoradas: list[str],
) -> None:
    """
    Guarda los expedientes ignorados en actas_sigi_ignoradas.json.
    """
    CARPETA_ARCHIVOS.mkdir(parents=True, exist_ok=True)

    ARCHIVO_ACTAS_IGNORADAS.write_text(
        json.dumps(
            actas_ignoradas,
            indent=4,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

def agregar_acta_sigi_ignorada(
    actas_ignoradas: list[str],
    expediente: str,
) -> None:
    """
    Agrega un expediente a la lista de ignorados si todavía no existe.
    """
    expediente_norm = reglas_sigi.normalizar_expediente(expediente)

    if not expediente_norm:
        return

    if expediente_norm not in actas_ignoradas:
        actas_ignoradas.append(expediente_norm)
    
async def _localizar_input_filtro_expediente(page):
    """Ubica el input por name (confirmado por HTML real). Si en algún
    momento el sitio cambia el atributo name, cae a buscarlo por label
    como respaldo."""
    candidato = page.locator(SELECTOR_INPUT_FILTRO_EXPEDIENTE)
    if await candidato.count():
        return candidato.first

    candidato = page.get_by_label(LABEL_FILTRO_EXPEDIENTE, exact=True)
    if await candidato.count():
        return candidato.first

    return None


async def _leer_total_resultados(page) -> Optional[int]:
    """Lee el C de 'Mostrando A a B de C resultados'. Se usa para
    detectar cuando el filtro por expediente NO se aplicó de verdad (ver
    UMBRAL_TOTAL_SIN_FILTRAR)."""
    texto = await web_sigi.leer_primer_texto(page.get_by_text(web_sigi.PATRON_PAGINA_ACTUAL))
    if not texto:
        return None
    m = web_sigi.PATRON_PAGINA_ACTUAL.search(texto)
    if not m:
        return None
    return int(m.group(3).replace(".", "").replace(",", ""))


async def _escribir_filtro_y_confirmar(page, input_filtro, expediente_completo: str, intentos_submit: int = 3) -> None:
    """Escribe expediente_completo en el input y confirma con Enter,
    verificando con el contador 'Mostrando A a B de C resultados' que el
    filtro REALMENTE se aplicó (C chico) y no quedó mostrando la grilla
    sin filtrar (C enorme -- se vio en la práctica en la primera
    búsqueda de una corrida: fill()+Enter no disparaba nada y la grilla
    seguía mostrando el total general). Si detecta que no se aplicó,
    reintenta el fill()+Enter (con un click previo para forzar foco real,
    por si el primer fill() no alcanzó a "engancharse" con el listener de
    búsqueda de la SPA)."""
    for intento in range(1, intentos_submit + 1):
        if intento > 1:
            await input_filtro.click()
            await input_filtro.fill("")
        await input_filtro.fill(expediente_completo)
        await input_filtro.press("Enter")
        await web_sigi.esperar_red(page)
        await page.wait_for_timeout(400)

        total = await _leer_total_resultados(page)
        if total is not None and total <= UMBRAL_TOTAL_SIN_FILTRAR:
            return  # filtro aplicado de verdad

        web_sigi.log(
            "EXPEDIENTE",
            f"  ⚠️ Tras escribir {expediente_completo!r} la grilla muestra {total} resultado(s) "
            f"totales -- parece que el filtro NO se aplicó (intento {intento}/{intentos_submit}), "
            f"reintentando...",
        )
        await page.wait_for_timeout(800)

    web_sigi.log(
        "EXPEDIENTE",
        f"  ⚠️ No se pudo confirmar que el filtro se aplicara tras {intentos_submit} intentos -- "
        f"sigo igual, puede devolver falsos negativos.",
    )


async def buscar(
    page,
    expediente_completo: str,
    idx_expediente: int,
    intentos: int = 4,
    espera_ms: int = 900,
    max_paginas: int = 10,
    primera_busqueda_global: bool = False,
):
    """Escribe expediente_completo en el filtro y confirma con Enter.

    El buscador de SIGI matchea por PREFIJO/SUBSTRING, no por igualdad
    exacta -- buscar "EXP-2026-1785" puede devolver también
    "EXP-2026-17850", "EXP-2026-17851", etc, y potencialmente más de 50
    resultados (más de una página). Por eso este método:

      1. recorre TODAS las filas devueltas en la página actual,
      2. si ninguna matchea exacto y hay más páginas del resultado
         filtrado, avanza a la siguiente (hasta `max_paginas`),
      3. sólo devuelve una fila si su celda de Expediente coincide EXACTO
         (normalizado) con lo pedido.

    Si tras recorrer todas las páginas disponibles (o `max_paginas`) no
    apareció ningún match exacto, se reintenta la LECTURA COMPLETA desde
    la página 1 (sin volver a escribir en el input) hasta `intentos`
    veces -- cubre el caso de repintado atrasado de la SPA.

    `primera_busqueda_global`: True SOLO en la primera vez que se llama a
    esta función en toda la corrida (la pasa `ejecutar()`). Se vio en la
    práctica que ESA transición puntual -- de la grilla recién cargada
    (sin filtrar por expediente, con muchas más páginas) a un resultado
    filtrado -- es sensiblemente más lenta que las búsquedas siguientes:
    tabla vacía por más de 15s, botón 'Siguiente' inestable por 30s, etc.
    En ese caso se usan intentos/esperas más generosos Y se espera
    explícitamente a que aparezca al menos una fila, en vez de una pausa
    fija corta. No se aplica a las búsquedas normales para no penalizar
    el resto de la corrida con esperas innecesarias.

    Devuelve la fila (locator) con match exacto, o None si no existe /
    no se pudo confirmar tras agotar los intentos.
    """
    if primera_busqueda_global:
        intentos = max(intentos, 6)
        espera_ms = max(espera_ms, 2500)

    input_filtro = await _localizar_input_filtro_expediente(page)
    if input_filtro is None:
        raise RuntimeError(
            "No se pudo ubicar el input del filtro por expediente. Ajustar "
            "LABEL_FILTRO_EXPEDIENTE / _localizar_input_filtro_expediente "
            "contra el HTML real de SIGI antes de seguir (ver AJUSTAR en el "
            "docstring del módulo)."
        )

    await _escribir_filtro_y_confirmar(page, input_filtro, expediente_completo)

    if primera_busqueda_global:
        web_sigi.log("EXPEDIENTE", f"  ⏳ Primera búsqueda de la corrida ({expediente_completo}): "
                                    f"esperando más tiempo de lo normal a que asiente la SPA...")
        await web_sigi.esperar_red(page, timeout=20000)
        await web_sigi.esperar_tabla_lista(page, timeout=20000)
    else:
        await web_sigi.esperar_red(page)
        await page.wait_for_timeout(300)

    esperado_norm = reglas_sigi.normalizar_expediente(expediente_completo)

    for intento in range(1, intentos + 1):
        # Cada intento arranca desde la página 1 del resultado filtrado:
        # si el intento anterior avanzó páginas sin encontrar nada, no
        # tiene sentido seguir avanzando desde donde quedó -- reiniciamos
        # el barrido completo, por si la lectura anterior falló por
        # timing (repintado atrasado) más que por ausencia real del dato.
        pagina_actual = await web_sigi.leer_numero_pagina_actual(page) or 1
        if pagina_actual != 1:
            await web_sigi.saltar_a_pagina(page, 1)
            pagina_actual = 1

        paginas_recorridas = 0
        while True:
            filas = page.locator(web_sigi.SELECTOR_FILAS_RESULTADO)
            cantidad = await filas.count()

            if cantidad == 0 and paginas_recorridas == 0:
                # Sin resultados desde el vamos en esta página: puede ser
                # repintado atrasado -- se resuelve en el reintento de
                # más abajo (nivel `intentos`), no acá.
                break

            for i in range(cantidad):
                fila = filas.nth(i)
                valores = await web_sigi.leer_celdas_con_reintento(fila, {"expediente": idx_expediente})
                expediente_leido = valores.get("expediente")
                leido_norm = reglas_sigi.normalizar_expediente(expediente_leido) if expediente_leido else None

                if leido_norm == esperado_norm:
                    if paginas_recorridas > 0:
                        web_sigi.log(
                            "EXPEDIENTE",
                            f"  📄 {expediente_completo} encontrado en página "
                            f"{pagina_actual} del resultado filtrado",
                        )
                    return fila

                # DEBUG temporal: no matcheó -- log fila por fila de lo
                # que se leyó vs lo esperado, para diagnosticar por qué
                # una fila visible a simple vista no está matcheando
                # (ver AJUSTAR: sacar este log una vez resuelto).
                web_sigi.log(
                    "DEBUG-MATCH",
                    f"    fila {i+1}/{cantidad}: leído={expediente_leido!r} "
                    f"(norm={leido_norm!r}) vs esperado={expediente_completo!r} "
                    f"(norm={esperado_norm!r}) -> {'MATCH' if leido_norm == esperado_norm else 'no match'}",
                )

            paginas_recorridas += 1
            if paginas_recorridas >= max_paginas:
                web_sigi.log(
                    "EXPEDIENTE",
                    f"  ⚠️ {expediente_completo}: alcancé el tope de {max_paginas} página(s) "
                    f"filtradas sin match exacto.",
                )
                break

            hay_siguiente = await web_sigi.hay_pagina_siguiente(page)
            # DEBUG temporal: dejar visible la decisión de paginar, para
            # diagnosticar el caso "hay 1 sola página con <50 resultados
            # pero el script igual intenta ir a 'Siguiente'" (ver AJUSTAR:
            # sacar este log una vez resuelto).
            web_sigi.log(
                "DEBUG-MATCH",
                f"    {cantidad} fila(s) en esta página, ninguna matcheó -- "
                f"¿hay página siguiente? {hay_siguiente}",
            )
            if not hay_siguiente:
                break

            try:
                await web_sigi.ir_a_pagina_siguiente(page)
            except PlaywrightTimeoutError:
                # Visto en corridas reales: el botón "Siguiente" queda
                # inestable/deshabilitado un rato (sobre todo en la
                # primera búsqueda de la corrida, ver AJUSTAR en el
                # docstring del módulo). Una pausa larga + un solo
                # reintento del click suele alcanzar.
                web_sigi.log(
                    "EXPEDIENTE",
                    "  ⚠️ 'Siguiente' tardó demasiado en estabilizarse -- pausa y reintento",
                )
                await page.wait_for_timeout(2000)
                await web_sigi.ir_a_pagina_siguiente(page)

            pagina_actual += 1

        web_sigi.log(
            "EXPEDIENTE",
            f"  ↻ Pedí {expediente_completo}, recorrí {paginas_recorridas or 1} página(s) del "
            f"resultado filtrado y ninguna fila matcheó exacto (intento {intento}/{intentos})",
        )
        if intento < intentos:
            await page.wait_for_timeout(espera_ms)

    web_sigi.log(
        "EXPEDIENTE",
        f"  ⚠️ No se pudo confirmar la fila de {expediente_completo} tras {intentos} intentos; se descarta.",
    )
    return None


async def _leer_primer_expediente_de_la_grilla(page, idx_expediente: int) -> Optional[str]:
    fila = page.locator(web_sigi.SELECTOR_FILAS_RESULTADO).first
    valores = await web_sigi.leer_celdas_con_reintento(fila, {"expediente": idx_expediente})
    return valores.get("expediente")


async def _procesar_expediente_encontrado(
    db: Session,
    page,
    fila,
    expediente_completo: str,
    idx_estado: Optional[int],
    actas_conocidas: dict,
    commit: bool,
) -> str:
    """
    Solo expediente + estado_sigi. NO crea registros nuevos: 'patente' es
    NOT NULL en la base, así que si el acta leída no corresponde a ningún
    registro ya existente (cargado antes por otro lado, ej. SEMyT), no
    hay forma de insertarla con los datos que trae este script -- se
    descarta y se loguea.

    Devuelve 'actualizado' | 'sin_registro_base' | 'sin_acta'.
    """
    valores = await web_sigi.leer_celdas_con_reintento(fila, {"estado": idx_estado})
    estado_fila = valores.get("estado")
    nuevo_estado_sigi = reglas_sigi.mapear_estado(estado_fila)
    estado_legible = nuevo_estado_sigi.value if nuevo_estado_sigi else "(sin mapear)"

    if not await web_sigi.abrir_detalle_de_fila(page, fila):
        return "sin_acta"

    numero_acta = await web_sigi.leer_numero_acta(page)
    acta_norm = reglas_sigi.normalizar_acta(numero_acta) if numero_acta else None
    resultado = "sin_acta"

    if not numero_acta:
        web_sigi.log("EXPEDIENTE", f"  ❌ {expediente_completo}: no se pudo leer el Nº de acta en el detalle")
    else:
        registro_existente = actas_conocidas.get(acta_norm) if acta_norm else None

        if registro_existente is None:
            web_sigi.log("EXPEDIENTE", f"  ↩ acta {numero_acta} (expediente {expediente_completo}) no tiene "
                                        f"ningún registro en la base -- se descarta (no se crea nada, falta "
                                        f"patente y demás datos obligatorios).")
            resultado = "sin_registro_base"
        else:
            if commit:
                registro_existente.expediente = expediente_completo
                if nuevo_estado_sigi is not None:
                    reglas_sigi.aplicar_actualizacion(db, registro_existente, {"estado_sigi": nuevo_estado_sigi})
                db.commit()
                web_sigi.log("EXPEDIENTE", f"  ✅ acta {numero_acta}: expediente={expediente_completo}, "
                                            f"estado_sigi={estado_legible}")
            else:
                web_sigi.log("EXPEDIENTE", f"  (dry-run) acta {numero_acta}: expediente={expediente_completo}, "
                                            f"estado_sigi={estado_legible}, NO se graba")
            resultado = "actualizado"

    await web_sigi.cerrar_detalle(page)
    return resultado


async def ejecutar(
    db: Session, page, commit: bool, desde: Optional[int], hasta: Optional[int],
    limite: Optional[int], delay: float = 1.5
) -> dict:
    modo = "COMMIT (graba de verdad)" if commit else "DRY-RUN (no toca la base)"
    web_sigi.log("INICIO", f"Modo: {modo}")

    await web_sigi.filtrar_por_tipo_acta_estacionamiento(page)
    # A propósito NO se toca el paginado ni se recorren páginas: solo hace
    # falta la primera fila de la grilla (la más reciente).

    idx_expediente, idx_estado = await web_sigi.indices_expediente_estado(page)
    if idx_expediente is None:
        raise RuntimeError("No se encontró la columna 'Expediente' en la grilla.")

    primer_expediente = await _leer_primer_expediente_de_la_grilla(page, idx_expediente)
    descompuesto = _descomponer_expediente(primer_expediente or "")
    if not descompuesto:
        raise RuntimeError(
            f"No se pudo interpretar el primer expediente de la grilla ({primer_expediente!r}); "
            "revisar el formato esperado (EXP-AAAA-NNNNNN) o el índice de columna."
        )
    anio, numero_maximo = descompuesto

    # --desde / --hasta son opcionales: si no se pasan, se mantiene el
    # comportamiento histórico (865 -> el expediente más reciente leído
    # de la grilla). Si se pasan, pisan esos defaults.
    NUMERO_INICIO = desde if desde is not None else 865
    NUMERO_FIN = hasta if hasta is not None else numero_maximo

    if NUMERO_FIN > numero_maximo:
        web_sigi.log("INICIO", f"⚠️ --hasta {NUMERO_FIN} es mayor al expediente más reciente de la "
                                f"grilla (EXP-{anio}-{numero_maximo}) -- lo recorto a {numero_maximo}.")
        NUMERO_FIN = numero_maximo

    web_sigi.log("INICIO", f"Expediente más reciente: EXP-{anio}-{numero_maximo} -> "
                            f"recorriendo desde {NUMERO_INICIO} hasta {NUMERO_FIN}")

    expedientes_conocidos = set(reglas_sigi.todos_los_expedientes_cargados(db).keys())
    actas_conocidas_todas = reglas_sigi.todas_las_actas_conocidas(db)
    # Para el chequeo "¿ya existe esta acta?" alcanza con un registro
    # representativo de cada acta (el primero de la lista).
    actas_conocidas = {acta: regs[0] for acta, regs in actas_conocidas_todas.items() if regs}
    actas_ignoradas = cargar_actas_sigi_ignoradas()

    web_sigi.log(
        "INICIO",
        f"{len(expedientes_conocidos)} expediente(s) ya en la base "
        f"(se saltean sin buscar), "
        f"{len(actas_conocidas)} acta(s) distinta(s) conocidas, "
        f"{len(actas_ignoradas)} expediente(s) ignorado(s)"
    )

    contadores = {"ya_en_db": 0,"ignorados": 0, "altas": 0, "clones": 0, "no_encontrados": 0, "sin_acta": 0, "errores": 0}
    procesados = 0
    primera_busqueda = True
    for numero in range(NUMERO_INICIO, NUMERO_FIN + 1):
        if limite is not None and procesados >= limite:
            web_sigi.log("LIMITE", f"Se alcanzó --limit {limite}.")
            break

        expediente_completo = _armar_expediente(anio, numero)
        expediente_norm = reglas_sigi.normalizar_expediente(expediente_completo)

        if expediente_norm in expedientes_conocidos:
            contadores["ya_en_db"] += 1
            procesados += 1
            continue
        
        if expediente_norm in actas_ignoradas:
            contadores["ignorados"] += 1
            procesados += 1

            web_sigi.log(
                "EXPEDIENTE",
                f"⏭️ {expediente_completo}: ignorado "
            )

            continue
        # Pausa entre búsqueda y búsqueda (no antes de la primera): le da
        # tiempo a la SPA de terminar de repintar la grilla tras la
        # búsqueda/cierre de detalle anterior, evitando leer una fila
        # todavía desactualizada (ver buscar, que además
        # verifica el expediente leído contra el pedido como segunda
        # red de seguridad).
        if not primera_busqueda and delay > 0:
            await page.wait_for_timeout(int(delay * 1000))
        es_primera_busqueda_global = primera_busqueda
        primera_busqueda = False

        try:
            fila = await buscar(
                page, expediente_completo, idx_expediente,
                primera_busqueda_global=es_primera_busqueda_global,
            )
        except Exception as exc:  # noqa: BLE001
            web_sigi.log("EXPEDIENTE", f"❌ Error buscando {expediente_completo}: {exc}")
            contadores["errores"] += 1
            procesados += 1
            continue

        if fila is None:
            web_sigi.log("EXPEDIENTE", f"↩ {expediente_completo}: sin resultados (no existe, o no es "
                                        f"de tipo Estacionamiento Medido)")
            contadores["no_encontrados"] += 1
            if commit:
                agregar_acta_sigi_ignorada(
                    actas_ignoradas,
                    expediente_completo,
                )
                guardar_actas_sigi_ignoradas(actas_ignoradas)

                web_sigi.log(
                    "IGNORADAS",
                    f"💾 {expediente_completo} agregado a "
                    f"{ARCHIVO_ACTAS_IGNORADAS}",
                )

            procesados += 1
            continue

        resultado = await _procesar_expediente_encontrado(
            db, page, fila, expediente_completo, idx_estado, actas_conocidas, commit
        )
        if resultado == "alta":
            contadores["altas"] += 1
            expedientes_conocidos.add(expediente_norm)
        elif resultado == "clon":
            contadores["clones"] += 1
            expedientes_conocidos.add(expediente_norm)
        else:
            contadores["sin_acta"] += 1

        procesados += 1
        if procesados % 50 == 0:
            web_sigi.log("PROGRESO", f"{procesados} expediente(s) procesados: {contadores}")

    web_sigi.log("RESUMEN", str(contadores))
    return contadores


async def _main(commit: bool, desde: Optional[int], hasta: Optional[int], limite: Optional[int], delay: float):
    db = SessionLocal()
    try:
        async with PaginaConSesion(
            ARCHIVO_SESION, URL_SIGI, carpeta_sesiones=CARPETA_SESIONES_API_REST_PAYMENT
        ) as page:
            resumen = await ejecutar(db, page, commit=commit, desde=desde, hasta=hasta, limite=limite, delay=delay)
        web_sigi.log("FIN", str(resumen))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Carga actas de SIGI buscando por número de expediente, de mayor a menor, "
                    "salteando sin buscar los que ya están en la base."
    )
    parser.add_argument("--commit", action="store_true", help="Graba en la DB de verdad. Sin este flag, dry-run.")
    parser.add_argument(
        "--desde", type=int, default=None,
        help="Número de expediente donde arrancar el ascenso. Si no se pasa, arranca en 865 "
             "(default histórico)."
    )
    parser.add_argument(
        "--hasta", type=int, default=None,
        help="Número de expediente donde cortar (inclusive). Si no se pasa, sigue hasta el "
             "expediente más reciente leído de la grilla (comportamiento actual)."
    )
    parser.add_argument("--limit", type=int, default=None, help="Procesa como máximo N expedientes en esta corrida.")
    parser.add_argument(
        "--delay", type=float, default=1.5,
        help="Segundos de espera entre expediente y expediente antes de la siguiente búsqueda "
             "(default: 1.5). Subilo si seguís viendo el mismo acta repetida en varios expedientes "
             "seguidos (señal de que la grilla no llega a refrescar a tiempo); --delay 0 la desactiva."
    )
    args = parser.parse_args()
    asyncio.run(_main(commit=args.commit, desde=args.desde, hasta=args.hasta, limite=args.limit, delay=args.delay))