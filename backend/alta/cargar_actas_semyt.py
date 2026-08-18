#!/usr/bin/env python3
"""
Completa actas faltantes de la DB buscando cada número directamente en SEMyT.

Estrategia:
    1. Carga UNA sola vez todos los números de acta existentes en la DB.
    2. Recorre el rango solicitado (por defecto 1..328792).
    3. Si el acta ya existe en DB -> no hace nada.
    4. Si falta -> busca ese número en SEMyT mediante "Filtrar por Número".
    5. Verifica que la fila realmente corresponde al número solicitado.
    6. Lee todos los datos de la fila.
    7. Abre la foto y obtiene su URL usando la lógica existente.
    8. Crea el Registro completo en la DB.
    9. Guarda checkpoint para poder continuar si el proceso se corta.

IMPORTANTE:
    - Por defecto es DRY-RUN: no modifica la DB.
    python alta/cargar_actas_semyt.py
    - Usar --commit para guardar realmente.
    - Ignora actas en estado IMPAGA, PAGADA o EN REVISION (mismo criterio
      que el resto del proyecto, ver ESTADOS_IGNORADOS_SEMYT). Sólo carga
      RECHAZADA, PAGADA EN JUZGADO, RESUELTA EN JUZGADO y VENCIDA.
      python alta/cargar_actas_semyt.py --commit
    - Las actas ignoradas se recuerdan entre corridas en
      actas_ignoradas_semyt.json (al lado de este script), para no volver
      a golpear SEMyT por algo que ya sabemos que no se va a cargar. Ese
      archivo se escribe UNA sola vez al final (o al cortar con Ctrl+C),
      nunca dentro del loop -- así un problema de disco no puede tumbar
      la corrida completa.
"""

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Optional
from app.services.consistencia import calcular_consistencia

# Permite ejecutar:
#   python update/cargar_actas_faltantes_semyt.py
CARPETA_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CARPETA_BACKEND))
CARPETA_ARCHIVOS_SEMYT = CARPETA_BACKEND / "app" / "services" / "sistemas" / "semyt" / "archivos"
# Estado persistente de actas ya vistas y descartadas por estado. JSON en
# vez de texto plano línea por línea: escritura atómica de una sola vez
# al final, en vez de un append por cada acta ignorada dentro del loop
# (ese append en caliente era el punto de falla que podía tumbar toda la
# corrida si algo raro pasaba con el disco a mitad de camino).
ARCHIVO_IGNORADAS = CARPETA_ARCHIVOS_SEMYT / "actas_ignoradas_semyt.json"
ARCHIVO_ELIMINADAS = CARPETA_ARCHIVOS_SEMYT / "actas_eliminadas_semyt.json"
from app.database import SessionLocal
from app.models import models
from app.services.sistemas.comun.sesion import PaginaConSesion, ruta_sesion
from app.paths import CARPETA_SESIONES_API_REST_PAYMENT
from app.pasos.procesar_actas_semyt import (
    LABEL_FILTRO_NUMERO,
    TEXTO_BOTON_BUSCAR,
    INDICE_COLUMNA_NRO,
    _parsear_fila,
)
from app.services.sistemas.semyt.rutas import (
    URL_SEMYT,
)

from app.reglas.reglas_semyt import (
    MAPA_ESTADO_SEMYT,
    ESTADOS_IGNORADOS_SEMYT,
    normalizar_estado,
    parsear_fecha_hora,
    obtener_url_foto_de_fila,
)

# ---------------------------------------------------------
# Configuración
# ---------------------------------------------------------

NUMERO_DESDE_DEFAULT = 1
NUMERO_HASTA_DEFAULT = 330160

# Reintentos cuando SEMyT tarda en repintar la grilla.
INTENTOS_BUSQUEDA = 3
ESPERA_REINTENTO_MS = 800

def log(paso: str, mensaje: str):
    print(f"[{paso}] {mensaje}", flush=True)



# ---------------------------------------------------------
# DB
# ---------------------------------------------------------

def cargar_actas_existentes(db) -> set[str]:
    """
    Trae SOLO el número de acta de todos los registros existentes.

    Se hace una única consulta al comienzo y después todas las comparaciones
    se hacen en memoria.
    """
    log("DB", "Cargando números de acta existentes...")

    filas = db.query(models.Registro.acta).all()

    resultado = {
        str(acta).strip()
        for (acta,) in filas
        if acta is not None and str(acta).strip()
    }

    log("DB", f"✅ {len(resultado)} números de acta cargados en memoria.")
    return resultado


# ---------------------------------------------------------
# Actas ignoradas (persistencia JSON, lectura/escritura únicas)
# ---------------------------------------------------------

def _cargar_set_json(ruta: Path, etiqueta: str) -> set[str]:
    """Lectura genérica para los dos archivos de estado persistente
    (ignoradas y eliminadas): mismo comportamiento defensivo en ambos."""
    if not ruta.exists():
        return set()
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return {str(v) for v in json.load(f)}
    except (json.JSONDecodeError, OSError) as exc:
        log(etiqueta, f"⚠️ No se pudo leer {ruta} ({exc}); se arranca vacío.")
        return set()


def _guardar_set_json(ruta: Path, valores: set[str], etiqueta: str):
    """Escritura completa, UNA sola vez, ordenada numéricamente."""
    try:
        ordenados = sorted(valores, key=lambda numero: int(numero))
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(ordenados, f, indent=2)
        log(etiqueta, f"💾 {len(valores)} acta(s) guardadas en {ruta.name}")
    except OSError as exc:
        log(etiqueta, f"⚠️ No se pudo guardar {ruta}: {exc}")


def cargar_actas_ignoradas() -> set[str]:
    """Actas descartadas por estado (IMPAGA/PAGADA/EN REVISION)."""
    return _cargar_set_json(ARCHIVO_IGNORADAS, "IGNORADAS")


def guardar_actas_ignoradas(actas_ignoradas: set[str]):
    _guardar_set_json(ARCHIVO_IGNORADAS, actas_ignoradas, "IGNORADAS")


def cargar_actas_eliminadas() -> set[str]:
    """Actas que SEMyT no encontró (posible baja/eliminación del lado
    del sitio), tras agotar los reintentos."""
    return _cargar_set_json(ARCHIVO_ELIMINADAS, "ELIMINADAS")


def guardar_actas_eliminadas(actas_eliminadas: set[str]):
    _guardar_set_json(ARCHIVO_ELIMINADAS, actas_eliminadas, "ELIMINADAS")


# ---------------------------------------------------------
# SEMyT
# ---------------------------------------------------------

async def buscar_fila_por_numero(page, numero_acta: str):
    """
    Busca un número mediante el filtro "Filtrar por Número".

    No confía solamente en networkidle: después de la búsqueda verifica
    que el número de la primera fila realmente sea el solicitado.

    Si la grilla aparece vacía (0 filas), NO se asume "no existe" al
    primer intento: puede ser un estado transitorio (la SPA todavía no
    terminó de re-pintar tras el filtro/click). Se reintenta con la
    misma política de espera que el caso "fila con número equivocado",
    para no perderse actas reales por una lectura demasiado apurada.
    """
    numero_acta = str(numero_acta).strip()

    await page.get_by_label(LABEL_FILTRO_NUMERO).fill(numero_acta)
    await page.get_by_role("button", name=TEXTO_BOTON_BUSCAR).click()
    await page.wait_for_load_state("networkidle")

    for intento in range(1, INTENTOS_BUSQUEDA + 1):
        filas = page.locator("table tbody tr")
        cantidad = await filas.count()

        if cantidad == 0:
            if intento < INTENTOS_BUSQUEDA:
                await page.wait_for_timeout(ESPERA_REINTENTO_MS)
                continue
            return None

        for i in range(cantidad):
            fila = filas.nth(i)
            celdas = fila.locator("td")

            if await celdas.count() <= INDICE_COLUMNA_NRO:
                continue

            nro = (
                await celdas.nth(INDICE_COLUMNA_NRO).inner_text()
            ).strip()

            if nro == numero_acta:
                return fila

        if intento < INTENTOS_BUSQUEDA:
            await page.wait_for_timeout(ESPERA_REINTENTO_MS)

    return None


async def leer_acta_completa(page, numero_acta: str):
    """
    Busca el acta y devuelve:
        (fila_playwright, datos_dict)

    datos_dict contiene las columnas reales de SEMyT:
        nro, fecha, dominio, cuadra, estado, vencimiento, importe
    """
    fila = await buscar_fila_por_numero(page, numero_acta)

    if fila is None:
        return None, None

    datos = await _parsear_fila(fila)

    if not datos:
        return None, None

    # Segunda comprobación defensiva.
    nro_leido = str(datos.get("nro", "")).strip()
    if nro_leido != str(numero_acta).strip():
        log(
            "SEMyT",
            f"⚠️ Pedí {numero_acta}, pero recibí {nro_leido}. "
            "No cargo la fila.",
        )
        return None, None

    return fila, datos


# ---------------------------------------------------------
# Creación del Registro
# ---------------------------------------------------------

def construir_registro(datos: dict, foto_url: Optional[str]):
    """
    Construye models.Registro con los campos que conocemos del proyecto.

    El archivo progresivo existente usa:
        acta
        patente
        direccion
        fecha_hora
        foto_url
        estado_semyt
        estado_sigemi
        estado_sigi

    Para vencimiento/importe se intenta asignar también si esos atributos
    existen en el modelo. Si no existen, se dejan fuera sin romper la carga.
    """
    numero_acta = str(datos.get("nro", "")).strip()
    patente = str(datos.get("dominio", "")).strip() or None
    direccion = str(datos.get("cuadra", "")).strip() or None

    estado_texto = normalizar_estado(datos.get("estado", ""))
    estado_enum = MAPA_ESTADO_SEMYT.get(estado_texto)

    fecha_hora = parsear_fecha_hora(datos.get("fecha", ""))

    nuevo = models.Registro(
        acta=numero_acta,
        patente=patente,
        direccion=direccion,
        fecha_hora=fecha_hora,
        foto_url=foto_url,
        estado_semyt=estado_enum,
        estado_sigemi=models.EstadoSigemi.no_cargada,
        estado_sigi=models.EstadoSigi.no_cargada,
    )

    # Si el modelo tiene vencimiento / importe, los cargamos.
    # No asumimos que existan porque el código existente no los persiste
    # directamente en models.Registro.
    if hasattr(nuevo, "vencimiento"):
        setattr(
            nuevo,
            "vencimiento",
            datos.get("vencimiento") or None,
        )

    if hasattr(nuevo, "importe"):
        setattr(
            nuevo,
            "importe",
            datos.get("importe") or None,
        )
    nuevo.consistente = calcular_consistencia(nuevo)
    return nuevo, estado_texto, estado_enum


async def procesar_acta(
    page,
    contexto,
    db,
    numero_acta: str,
    commit: bool,
    actas_existentes: set[str],
    actas_ignoradas: set[str],
    actas_eliminadas: set[str],
):
    """
    Procesa un único número.

    Retorna:
        ya_existe
        ya_ignorada
        no_encontrada
        cargada
        ignorada
        error
    """

    numero_acta = str(numero_acta).strip()

    if numero_acta in actas_existentes:
        return "ya_existe"

    if numero_acta in actas_ignoradas:
        return "ya_ignorada"
    if numero_acta in actas_eliminadas:
        return "ya_eliminada"
    try:
        fila, datos = await leer_acta_completa(page, numero_acta)
    except Exception as exc:
        log(
            "SEMyT",
            f"❌ Error buscando acta {numero_acta}: {exc}",
        )
        return "error"

    if fila is None or datos is None:
        log(
            "SEMyT",
            f"❌ Acta {numero_acta}: no encontrada.",
        )
        if commit:
            actas_eliminadas.add(numero_acta)
        return "no_encontrada"

    estado_texto = normalizar_estado(datos.get("estado", ""))

    if estado_texto in ESTADOS_IGNORADOS_SEMYT:
        # Sólo se actualiza el set en memoria acá -- la escritura a disco
        # pasa UNA sola vez al final (ver guardar_actas_ignoradas en
        # main()), nunca dentro del loop.
        actas_ignoradas.add(numero_acta)
        log(
            "SEMyT",
            f"⏭ Acta {numero_acta}: estado '{estado_texto}' ignorado "
            "(PAGADA/IMPAGA/EN REVISION). No se carga.",
        )
        return "ignorada"

    log(
        "SEMyT",
        f"✅ Acta {numero_acta} encontrada | "
        f"patente={datos.get('dominio', '')} | "
         f"fecha={datos.get('fecha', '')} | "
        f"estado={estado_texto or '(vacío)'}",
    )

    # Obtener la foto SIEMPRE que exista el botón, incluso para estados
    # que los scripts anteriores ignoraban.
    try:
        foto_url = await obtener_url_foto_de_fila(
            contexto,
            page,
            fila,
            numero_acta,
            commit=True,
        )
    except Exception as exc:
        log(
            "FOTO",
            f"⚠️ Acta {numero_acta}: error obteniendo foto: {exc}",
        )
        foto_url = None

    if foto_url:
        log("FOTO", f"✅ Acta {numero_acta}: foto encontrada.")
    else:
        log("FOTO", f"⚠️ Acta {numero_acta}: sin foto.")

    try:
        nuevo, estado_texto, estado_enum = construir_registro(
            datos,
            foto_url,
        )
    except Exception as exc:
        log(
            "DB",
            f"❌ Acta {numero_acta}: error construyendo Registro: {exc}",
        )
        return "error"

    log(
        "DB",
        f"{'[COMMIT]' if commit else '[DRY-RUN]'} "
        f"acta={numero_acta} | "
        f"patente={datos.get('dominio') or '-'} | "
        f"estado={estado_texto or 'NULL'} | "
        f"foto={'SI' if foto_url else 'NO'}",
    )

    if not commit:
        return "cargada"

    try:
        db.add(nuevo)
        db.commit()

        # Importantísimo: recién después de commit la agregamos al set.
        actas_existentes.add(numero_acta)

        return "cargada"

    except Exception as exc:
        db.rollback()

        log(
            "DB",
            f"❌ Acta {numero_acta}: falló el INSERT -> {exc}",
        )

        return "error"


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

async def main(
    desde: int,
    hasta: int,
    commit: bool,
    reiniciar: bool,
    limite: Optional[int],
):

    modo = "COMMIT" if commit else "DRY-RUN"
    log("INICIO", f"Modo: {modo}")
    log("RANGO", f"Actas {desde} -> {hasta}")

    # -----------------------------------------------------
    # Checkpoint
    # -----------------------------------------------------

    siguiente_numero = desde

    contadores = {
        "ya_existe": 0,
        "ya_ignorada": 0,
        "ya_eliminada": 0,
        "faltantes": 0,
        "cargadas": 0,
        "ignoradas": 0,
        "no_encontradas": 0,
        "errores": 0,
    }

    # -----------------------------------------------------
    # DB
    # -----------------------------------------------------

    db = SessionLocal()
    actas_ignoradas: set[str] = set()
    actas_eliminadas: set[str] = set()
    try:
        actas_existentes = cargar_actas_existentes(db)

        if reiniciar:
            log("IGNORADAS", "⚠️ --reiniciar: se ignora el registro previo de actas descartadas.")
        else:
            actas_ignoradas = cargar_actas_ignoradas()
            actas_eliminadas = cargar_actas_eliminadas()
            log("DB", f"✅ {len(actas_ignoradas)} acta(s) ignorada(s) y {len(actas_eliminadas)} eliminada(s) recordadas de corridas anteriores.")

        total_rango = hasta - desde + 1
        log(
            "DB",
            f"Rango total: {total_rango:,} números.",
        )

        # -------------------------------------------------
        # SEMyT
        # -------------------------------------------------

        archivo_sesion_semyt = ruta_sesion("sesion_semyt.json", CARPETA_SESIONES_API_REST_PAYMENT)
        if not archivo_sesion_semyt.exists():
            log(
                "SESION",
                f"❌ No existe el archivo de sesión: "
                f"{archivo_sesion_semyt}",
            )
            return

        log("SESION", "✅ Archivo de sesión encontrado.")

        async with PaginaConSesion(
            "sesion_semyt.json",
            URL_SEMYT,
            carpeta_sesiones=CARPETA_SESIONES_API_REST_PAYMENT,
        ) as page:
            # PaginaConSesion ya expone una Page, pero la función de fotos
            # necesita el BrowserContext para detectar una posible pestaña
            # nueva. Obtenemos el contexto desde page.
            contexto = page.context

            procesados_desde_esta_corrida = 0

            for numero in range(siguiente_numero, hasta + 1):
                numero_acta = str(numero)

                # -------------------------------------------------
                # Límite opcional para pruebas.
                # -------------------------------------------------
                if (
                    limite is not None
                    and procesados_desde_esta_corrida >= limite
                ):
                    log(
                        "LIMITE",
                        f"Se alcanzó --limit {limite}.",
                    )
                    break

                # -------------------------------------------------
                # Comparación DB -> memoria. Envuelto en try/except: un
                # error puntual en una acta (ej. una colisión de datos
                # insertados a mano en paralelo) no debe tumbar el resto
                # del rango.
                # -------------------------------------------------
                try:
                    if numero_acta in actas_existentes:
                        contadores["ya_existe"] += 1

                    elif numero_acta in actas_ignoradas:
                        contadores["ya_ignorada"] += 1
                        
                    elif numero_acta in actas_eliminadas:
                        contadores["ya_eliminada"] += 1
                    else:
                        contadores["faltantes"] += 1

                        resultado = await procesar_acta(
                            page,
                            contexto,
                            db,
                            numero_acta,
                            commit,
                            actas_existentes,
                            actas_ignoradas,
                            actas_eliminadas,
                        )

                        if resultado == "cargada":
                            contadores["cargadas"] += 1

                        elif resultado == "ignorada":
                            contadores["ignoradas"] += 1

                        elif resultado == "no_encontrada":
                            contadores["no_encontradas"] += 1

                        elif resultado == "error":
                            contadores["errores"] += 1

                except Exception as exc:
                    log("ACTA", f"❌ Error inesperado procesando acta {numero_acta}: {exc}")
                    traceback.print_exc()
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    contadores["errores"] += 1

                procesados_desde_esta_corrida += 1

                # -------------------------------------------------
                # Progreso.
                # -------------------------------------------------

                if (
                    numero == desde
                    or numero % 100 == 0
                    or numero == hasta
                ):
                    porcentaje = (
                        (numero - desde + 1)
                        / total_rango
                        * 100
                    )

                    log(
                        "PROGRESO",
                        f"{numero:,}/{hasta:,} "
                        f"({porcentaje:.2f}%) | "
                        f"existentes={contadores['ya_existe']:,} | "
                        f"ya_ignoradas={contadores['ya_ignorada']:,} | "
                        f"ya_eliminadas={contadores['ya_eliminada']:,} | "
                        f"faltantes={contadores['faltantes']:,} | "
                        f"cargadas={contadores['cargadas']:,} | "
                        f"ignoradas={contadores['ignoradas']:,} | "
                        f"no_encontradas={contadores['no_encontradas']:,} | "
                        f"errores={contadores['errores']:,}",
                    )

            # -----------------------------------------------------
            # Si terminó todo el rango, eliminamos checkpoint.
            # -----------------------------------------------------

            termino_rango = (
                siguiente_numero <= hasta
                and (
                    limite is None
                    or procesados_desde_esta_corrida >=
                    (hasta - siguiente_numero + 1)
                )
            )

            # Si no se utilizó límite, llegar al final significa que
            # terminamos el rango.
            if limite is None:
                termino_rango = (
                    procesados_desde_esta_corrida
                    >= (hasta - siguiente_numero + 1)
                )

    except Exception:
        log(
            "ERROR-GENERAL",
            "❌ Excepción no prevista:",
        )
        traceback.print_exc()
    finally:
        # Se guarda SIEMPRE, incluso si el script se corta por una
        # excepción no prevista o por Ctrl+C: lo que se llegó a acumular
        # en memoria durante esta corrida no se pierde.
        guardar_actas_ignoradas(actas_ignoradas)
        guardar_actas_eliminadas(actas_eliminadas)
        db.close()

    # ---------------------------------------------------------
    # Resumen
    # ---------------------------------------------------------

    log("FIN", "")
    log("FIN", "========================================")
    log("FIN", "RESUMEN")
    log("FIN", "========================================")
    log("FIN", f"Existentes en DB: {contadores['ya_existe']:,}")
    log("FIN", f"Ya ignoradas (de corridas previas): {contadores['ya_ignorada']:,}")
    log("FIN", f"Ya eliminadas (de corridas previas): {contadores['ya_eliminada']:,}")
    log("FIN", f"Faltantes consultadas: {contadores['faltantes']:,}")
    log("FIN", f"Cargadas: {contadores['cargadas']:,}")
    log("FIN", f"Ignoradas ahora (PAGADA/IMPAGA/EN REVISION): {contadores['ignoradas']:,}")
    log("FIN", f"No encontradas ahora (posibles eliminadas): {contadores['no_encontradas']:,}")
    log("FIN", f"Errores: {contadores['errores']:,}")
    log("FIN", "========================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Busca en SEMyT las actas que faltan en la DB y las carga "
            "completas, incluyendo la foto."
        )
    )

    parser.add_argument(
        "--desde",
        type=int,
        default=NUMERO_DESDE_DEFAULT,
        help="Primer número de acta a revisar (default: 1).",
    )

    parser.add_argument(
        "--hasta",
        type=int,
        default=NUMERO_HASTA_DEFAULT,
        help="Último número de acta a revisar (default: 328792).",
    )

    parser.add_argument(
        "--commit",
        action="store_true",
        help="Guarda realmente las actas en la DB.",
    )

    parser.add_argument(
        "--reiniciar",
        action="store_true",
        help="Ignora el registro previo de actas descartadas (actas_ignoradas_semyt.json) y las vuelve a evaluar todas.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Procesa como máximo N números en esta ejecución.",
    )

    args = parser.parse_args()

    if args.desde < 1:
        parser.error("--desde debe ser >= 1")

    if args.hasta < args.desde:
        parser.error("--hasta debe ser >= --desde")

    asyncio.run(
        main(
            desde=args.desde,
            hasta=args.hasta,
            commit=args.commit,
            reiniciar=args.reiniciar,
            limite=args.limit,
        )
    )