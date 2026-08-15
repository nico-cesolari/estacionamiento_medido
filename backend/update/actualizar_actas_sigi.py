"""
NO SE
Sincronización de actas SIGI -- reemplaza el par llenar_actas_sigi.py +
actualizar_actas_sigi.py por UN SOLO recorrido de la grilla.

Por qué: actualizar_actas_sigi.py hacía una búsqueda individual por
expediente (filtro + Enter + esperar refresco) por cada registro con
expediente en la DB -- un viaje de ida y vuelta a SIGI por registro.
Este script en cambio recorre la tabla UNA vez, fila por fila (como ya
hacía llenar_actas_sigi.py para el alta), y decide para cada fila:

  1) Lee expediente + estado DIRECTO de la grilla (sin abrir detalle).
  2) Si ese expediente YA está en la DB (`todos_los_expedientes_cargados`):
       - si el registro está archivada -> estado terminal, se saltea sin
         más (no hace falta ni comparar estado).
       - si no, se compara el estado leído contra el guardado y se
         actualiza si cambió -- SIN abrir el detalle (barato).
       Se marca esa fila como "vista" (ver más abajo, sección de
       desvinculación).
  3) Si el expediente NO es conocido -> abre el ojo, entra a la pestaña
     "Actas", lee el número de acta, y lo busca entre TODOS los
     registros de la DB (`todas_las_actas_conocidas`) -- con o sin
     expediente ya cargado, porque una misma acta puede tener más de un
     expediente (ver reglas_sigi.clonar_registro).
       - si encuentra un registro de esa acta SIN expediente todavía ->
         se lo asigna (alta).
       - si todos los registros de esa acta ya tienen expediente (todos
         distintos al de esta fila) -> se clona un registro nuevo
         (mismo caso que ya contemplaba llenar_actas_sigi.py).
       - si NINGÚN registro de la DB tiene esa acta -> se saltea y se
         cuenta en `posibles_actas_eliminadas_en_semyt` (existe en SIGI
         pero no hay rastro de ella en la base local -- a revisar a
         mano, puede ser un acta que nunca llegó desde SEMYT o que se
         borró de la base por error).

  Al terminar el recorrido completo de la grilla: cualquier expediente
  que estaba en `todos_los_expedientes_cargados` pero NUNCA apareció
  como fila (no fue "visto") ya no existe en SIGI -> se desvincula
  (mismo comportamiento que tenía actualizar_actas_sigi.py con
  `reglas_sigi.desvincular_expediente_no_encontrado`).

Uso standalone:
    cd backend
    python update/actualizar_actas_sigi.py
    python update/actualizar_actas_sigi.py --commit
    caffeinate -i python update/actualizar_actas_sigi.py --commit

También se puede importar `ejecutar_sincronizacion(db, page, commit=True)`
desde procesar_actas_sigi.py, en reemplazo de las llamadas separadas a
ejecutar_alta + ejecutar_actualizacion.
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import crud, models  # noqa: F401
from sistemas.sigi.reglas import reglas_sigi
from app.pasos.navegador import PaginaConSesion
from app.database import SessionLocal

# Reusamos TODA la infraestructura de Playwright ya probada en el alta --
# paginación, esperas, estrategias de click sobre el ojito, etc. No tiene
# sentido tener dos copias de esto que se puedan desincronizar. Si en algún
# momento molesta importar desde "alta", lo ideal es extraer estas
# funciones a un módulo compartido (ej. app/pasos/sigi_grilla.py) -- lo
# dejo así por ahora para no tocar más archivos de los necesarios.
from alta.llenar_actas_sigi import (
    log,
    _esperar_red,
    _indice_columna,
    _leer_celdas_con_reintento,
    _leer_primer_texto,
    _leer_primer_numero_acta,
    _leer_numero_pagina_actual,
    _describir_elemento,
    _esperar_tabla_lista,
    _scroll_y_detectar_filas_nuevas,
    _asegurar_pagina,
    _hay_pagina_siguiente,
    _ir_a_pagina_siguiente,
    _filtrar_por_tipo_acta_estacionamiento,
    _seleccionar_paginado_50,
    SELECTOR_FILAS_RESULTADO,
    SELECTOR_BOTON_VER,
    SELECTOR_BOTON_VOLVER,
    SELECTOR_MOTIVO_EN_DETALLE,
    TEXTO_HEADER_EXPEDIENTE,
    TEXTO_HEADER_ESTADO,
    URL_SIGI,
    ARCHIVO_SESION,
)

MAX_INTENTOS_FILA = 3


async def _reubicar_boton_ver(fila):
    """Vuelve a pedir el locator del botón 'Ver' DESDE CERO.

    Por qué hace falta -- este es el fix del bug de "a veces no
    clickea": en la versión anterior (llenar_actas_sigi.py) el locator
    `candidatos_ver` se armaba UNA vez al principio del intento y se
    reusaba en las 4 estrategias de click sucesivas. Si la fila se
    re-renderiza (ej. por el fetch async que completa la columna ESTADO)
    justo entre que armamos ese locator y que probamos la 2da/3ra
    estrategia, el locator sigue "vivo" (Playwright no tira error al
    armarlo) pero apunta a un nodo que ya no es el real -- el click
    puede salir sin excepción y no hacer nada, o pegarle a otro
    elemento. Por eso cada estrategia pide el locator de nuevo, igual
    que ya se hacía con `filas`/`fila` entre reintentos completos."""
    return fila.locator(SELECTOR_BOTON_VER)


async def _abrir_detalle_de_fila(page, fila, pagina_actual: int, fila_idx: int) -> bool:
    """Prueba varias formas de clickear el ojito, re-ubicando el botón
    ANTES DE CADA intento (ver _reubicar_boton_ver). Devuelve True si el
    detalle terminó abierto de verdad (chequeado, no asumido)."""
    tab_actas = page.get_by_role("button", name="Actas", exact=True)
    boton_volver = page.locator(SELECTOR_BOTON_VOLVER)

    async def _detalle_abierto(timeout=12000):
        transcurrido, intervalo = 0, 250
        while transcurrido < timeout:
            if await tab_actas.count() or await boton_volver.count():
                return True
            await page.wait_for_timeout(intervalo)
            transcurrido += intervalo
        return False

    estrategias = [
        ("click normal", lambda loc: loc.first.click(timeout=4000)),
        ("click forzado (force=True)", lambda loc: loc.first.click(timeout=3000, force=True)),
        ("el.click() vía JS", lambda loc: loc.first.evaluate("el => el.click()")),
        ("MouseEvent despachado vía JS", lambda loc: loc.first.evaluate(
            "el => el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}))"
        )),
    ]

    for nombre, hacer_click in estrategias:
        candidatos_ver = await _reubicar_boton_ver(fila)  # <- SIEMPRE fresco
        if await candidatos_ver.count() == 0:
            log("DEBUG-VER", f"  ⚠️ '{nombre}': el botón 'Ver' ya no está en la fila "
                              f"(re-render entre estrategias) -- reintento con locator fresco falló también")
            continue
        try:
            await hacer_click(candidatos_ver)
        except Exception as exc:
            log("DEBUG-VER", f"  ⚠️ '{nombre}' en 'Ver' falló: {exc}")
            continue

        try:
            await fila.wait_for(state="detached", timeout=6000)
        except PlaywrightTimeoutError:
            log("FILA", f"  ↻ '{nombre}' no tuvo ningún efecto visible, probando la siguiente estrategia...")
            continue

        if await _detalle_abierto(timeout=12000):
            if nombre != "click normal":
                log("FILA", f"  👁️ el detalle abrió con '{nombre}'")
            return True
        log("FILA", f"  ⚠️ '{nombre}': la fila se desprendió pero el detalle no pintó en 12s, margen extra...")
        return await _detalle_abierto(timeout=8000)

    log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: ninguna estrategia de click "
                f"abrió el detalle -- salteo esta fila (seguimos en el listado).")
    return False


async def _procesar_fila_sincronizacion(
    db: Session,
    page,
    pagina_actual: int,
    fila_idx: int,
    idx_expediente: Optional[int],
    idx_estado: Optional[int],
    expedientes_conocidos: dict,
    actas_conocidas: dict,
    vistos: set,
    contadores: dict,
    commit: bool,
) -> None:
    """Procesa una fila. No devuelve nada -- todo el resultado se anota
    en `contadores` (dict mutable) para no tener que ir combinando
    tuplas de retorno distintas según el camino que tomó la fila."""
    for intento in range(1, MAX_INTENTOS_FILA + 1):
        try:
            filas = page.locator(SELECTOR_FILAS_RESULTADO)
            fila = filas.nth(fila_idx)
            await fila.scroll_into_view_if_needed()

            valores = await _leer_celdas_con_reintento(
                fila, {"expediente": idx_expediente, "estado": idx_estado}
            )
            expediente_fila = valores["expediente"]
            estado_fila = valores["estado"]

            if expediente_fila is None and estado_fila is None:
                if intento >= MAX_INTENTOS_FILA:
                    log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: sigue sin datos, salteo.")
                    return
                await page.wait_for_timeout(500)
                await _esperar_tabla_lista(page)
                continue

            expediente_norm = reglas_sigi.normalizar_expediente(expediente_fila)

            # --- Camino 1: expediente ya conocido -- barato, sin abrir detalle ---
            registro_conocido = expedientes_conocidos.get(expediente_norm) if expediente_norm else None
            if registro_conocido is not None:
                vistos.add(expediente_norm)
                if registro_conocido.estado_sigi == models.EstadoSigi.archivada:
                    contadores["sin_cambios"] += 1
                    return

                cambios = reglas_sigi.armar_cambios_estado(estado_fila)
                if cambios is None:
                    log("FILA", f"  ⚠️ expediente {expediente_fila}: estado '{estado_fila}' no se pudo mapear")
                    contadores["errores"] += 1
                    return
                if not reglas_sigi.hay_cambio_real(registro_conocido, cambios):
                    contadores["sin_cambios"] += 1
                    return

                if commit:
                    reglas_sigi.aplicar_actualizacion(db, registro_conocido, cambios)
                    db.commit()
                    log("FILA", f"  ✅ expediente {expediente_fila} (acta {registro_conocido.acta}): "
                                f"estado actualizado a {cambios.get('estado_sigi')}")
                else:
                    log("FILA", f"  (dry-run) expediente {expediente_fila}: pasaría a "
                                f"{cambios.get('estado_sigi')}, NO se graba")
                contadores["actualizados"] += 1
                return

            # --- Camino 2: expediente NO conocido -- hay que abrir el detalle ---
            log("FILA", f"Página {pagina_actual}, fila {fila_idx + 1}: expediente {expediente_fila} "
                        f"desconocido -> abriendo detalle para leer el acta...")

            if not await _abrir_detalle_de_fila(page, fila, pagina_actual, fila_idx):
                contadores["errores"] += 1
                return

            numero_acta = await _leer_primer_numero_acta(page)
            acta_norm = reglas_sigi.normalizar_acta(numero_acta) if numero_acta else None

            if not numero_acta:
                log("FILA", "  ❌ no se pudo leer el Nº de acta en el detalle")
                contadores["errores"] += 1
            else:
                candidatos = actas_conocidas.get(acta_norm) or []
                candidato_libre = next((r for r in candidatos if not r.expediente), None)
                ya_con_este_expediente = next(
                    (r for r in candidatos if reglas_sigi.normalizar_expediente(r.expediente) == expediente_norm),
                    None,
                )

                motivo_texto = await _leer_primer_texto(page.locator(SELECTOR_MOTIVO_EN_DETALLE))
                cambios = reglas_sigi.armar_cambios_estado(estado_fila, motivo_texto)

                if ya_con_este_expediente is not None:
                    log("FILA", f"  ↩ acta {numero_acta} ya tenía este expediente guardado, no se duplica")
                    contadores["sin_cambios"] += 1

                elif candidato_libre is not None:
                    if commit:
                        candidato_libre.expediente = expediente_fila
                        if cambios:
                            reglas_sigi.aplicar_actualizacion(db, candidato_libre, cambios)
                        else:
                            db.add(candidato_libre)
                        db.commit()
                        log("FILA", f"  ✅ ALTA acta {numero_acta} -> patente {candidato_libre.patente} "
                                    f"(expediente {expediente_fila})")
                    else:
                        log("FILA", f"  (dry-run) ALTA acta {numero_acta} -> expediente {expediente_fila}, "
                                    f"NO se graba")
                    candidato_libre.expediente = expediente_fila  # también en dry-run, para no re-matchear
                    candidatos.append(candidato_libre) if candidato_libre not in candidatos else None
                    if acta_norm:
                        expedientes_conocidos[expediente_norm] = candidato_libre
                        vistos.add(expediente_norm)
                    contadores["altas"] += 1

                elif candidatos:
                    # todos los registros de esta acta ya tienen expediente
                    # (todos distintos al de esta fila) -> clonar.
                    base = candidatos[0]
                    if commit:
                        nuevo = reglas_sigi.clonar_registro(base)
                        nuevo.expediente = expediente_fila
                        if cambios:
                            reglas_sigi.aplicar_actualizacion(db, nuevo, cambios)
                        else:
                            db.add(nuevo)
                        db.commit()
                        candidatos.append(nuevo)
                        log("FILA", f"  ✅ acta {numero_acta} ya tenía expediente(s) "
                                    f"{[r.expediente for r in candidatos[:-1]]}, se agrega registro nuevo "
                                    f"con expediente {expediente_fila}")
                        if acta_norm:
                            expedientes_conocidos[expediente_norm] = nuevo
                            vistos.add(expediente_norm)
                    else:
                        log("FILA", f"  (dry-run) acta {numero_acta} -> se clonaría un registro nuevo "
                                    f"con expediente {expediente_fila}, NO se graba")
                    contadores["duplicados"] += 1

                else:
                    log("FILA", f"  ⚠️ acta {numero_acta} (expediente {expediente_fila}) no está en "
                                f"NINGÚN registro de la base -- posible acta eliminada en SEMYT")
                    contadores["posibles_actas_eliminadas_en_semyt"] += 1
                    contadores["detalle_posibles_eliminadas"].append(
                        {"acta": numero_acta, "expediente": expediente_fila, "estado": estado_fila}
                    )

            boton_volver = page.locator(SELECTOR_BOTON_VOLVER)
            if await boton_volver.count() == 0:
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
            return

        except PlaywrightTimeoutError as exc:
            if intento >= MAX_INTENTOS_FILA:
                log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: timeout {MAX_INTENTOS_FILA} "
                            f"veces seguidas -- salteo. Error: {exc}")
                contadores["errores"] += 1
                return
            await page.wait_for_timeout(1500)
            await _esperar_tabla_lista(page, min_filas=fila_idx + 1)

        except PlaywrightError as exc:
            if "not attached to the DOM" not in str(exc):
                raise
            if intento >= MAX_INTENTOS_FILA:
                log("FILA", f"  ⚠️ Página {pagina_actual}, fila {fila_idx + 1}: DOM re-renderizado "
                            f"{MAX_INTENTOS_FILA} veces seguidas -- salteo. Error: {exc}")
                contadores["errores"] += 1
                try:
                    boton_volver = page.locator(SELECTOR_BOTON_VOLVER)
                    if await boton_volver.count():
                        await boton_volver.first.click()
                    await _esperar_red(page)
                    await _esperar_tabla_lista(page)
                except Exception:
                    pass
                return
            await page.wait_for_timeout(500)
            await _esperar_tabla_lista(page)


async def _recorrer_grilla(db: Session, page, expedientes_conocidos: dict, actas_conocidas: dict,
                            vistos: set, contadores: dict, commit: bool):
    idx_expediente = await _indice_columna(page, TEXTO_HEADER_EXPEDIENTE)
    idx_estado = await _indice_columna(page, TEXTO_HEADER_ESTADO)

    pagina_actual = 1
    while True:
        fila_idx = 0
        while True:
            await _esperar_tabla_lista(page, min_filas=fila_idx + 1)
            filas = page.locator(SELECTOR_FILAS_RESULTADO)
            total_filas = await filas.count()

            if fila_idx >= total_filas:
                log("PAGINA", f"Se procesaron las {total_filas} filas visibles en la página "
                               f"{pagina_actual}. Probando si hay más por scroll...")
                if await _scroll_y_detectar_filas_nuevas(page):
                    continue
                break

            await _procesar_fila_sincronizacion(
                db, page, pagina_actual, fila_idx,
                idx_expediente, idx_estado,
                expedientes_conocidos, actas_conocidas, vistos, contadores, commit,
            )
            await _asegurar_pagina(page, pagina_actual)
            fila_idx += 1

        if not await _hay_pagina_siguiente(page):
            log("PAGINA", f"No hay más páginas (llegamos a la {pagina_actual}).")
            break
        await _ir_a_pagina_siguiente(page)
        pagina_actual += 1


async def ejecutar_sincronizacion(db: Session, page, commit: bool = True) -> dict:
    """
    Reemplaza a ejecutar_alta + ejecutar_actualizacion: UN solo recorrido
    de la grilla de SIGI que hace alta y actualización de estado juntas.

    commit=True  -> cada cambio se persiste al instante (igual que antes).
    commit=False -> dry-run, no toca la DB.
    """
    modo = "COMMIT (graba de verdad)" if commit else "DRY-RUN (no toca la base)"
    log("INICIO", f"Modo: {modo}")

    expedientes_conocidos = reglas_sigi.todos_los_expedientes_cargados(db)
    actas_conocidas = reglas_sigi.todas_las_actas_conocidas(db)
    log("INICIO", f"{len(expedientes_conocidos)} expediente(s) ya cargado(s) en la base, "
                  f"{len(actas_conocidas)} acta(s) distinta(s) conocidas en total")

    vistos: set = set()
    contadores = {
        "altas": 0,
        "actualizados": 0,
        "duplicados": 0,
        "sin_cambios": 0,
        "errores": 0,
        "posibles_actas_eliminadas_en_semyt": 0,
        "detalle_posibles_eliminadas": [],
        "desvinculados": 0,
    }

    await _filtrar_por_tipo_acta_estacionamiento(page)
    await _seleccionar_paginado_50(page)

    await _recorrer_grilla(db, page, expedientes_conocidos, actas_conocidas, vistos, contadores, commit)

    # Expedientes que la DB tenía guardados pero que NUNCA aparecieron
    # como fila en todo el recorrido -> ya no existen en SIGI.
    no_vistos = [r for norm, r in expedientes_conocidos.items() if norm not in vistos]
    for registro in no_vistos:
        if commit:
            if reglas_sigi.desvincular_expediente_no_encontrado(db, registro):
                db.commit()
                contadores["desvinculados"] += 1
                log("DESVINCULADO", f"expediente {registro.expediente} (acta {registro.acta}) no apareció "
                                    f"en todo el recorrido -> desvinculado, 'No Cargada'")
        else:
            contadores["desvinculados"] += 1
            log("DESVINCULADO", f"(dry-run) expediente {registro.expediente} (acta {registro.acta}) "
                                f"se desvincularía, NO se graba")

    if contadores["detalle_posibles_eliminadas"]:
        log("RESUMEN", "Posibles actas eliminadas en SEMYT (revisar a mano):")
        for item in contadores["detalle_posibles_eliminadas"]:
            log("RESUMEN", f"  - acta {item['acta']} / expediente {item['expediente']} / estado {item['estado']}")

    resumen = {k: v for k, v in contadores.items() if k != "detalle_posibles_eliminadas"}
    log("RESUMEN", str(resumen))
    return contadores


async def _main(commit: bool):
    db = SessionLocal()
    try:
        async with PaginaConSesion(ARCHIVO_SESION, URL_SIGI, False) as page:
            resumen = await ejecutar_sincronizacion(db, page, commit=commit)
        log("FIN", str({k: v for k, v in resumen.items() if k != "detalle_posibles_eliminadas"}))
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                         help="Graba en la DB de verdad. Sin este flag, corre en dry-run.")
    args = parser.parse_args()
    asyncio.run(_main(commit=args.commit))