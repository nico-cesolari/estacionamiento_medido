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
    - A diferencia de los scripts históricos, NO ignora IMPAGA/PAGADA/EN REVISION:
      si el acta existe en SEMyT se intenta cargar igual. Si el estado no tiene
      equivalencia en MAPA_ESTADO_SEMYT, se guarda estado_semyt=NULL.
"""

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright

# Permite ejecutar:
#   python update/completar_actas_faltantes_semyt.py
CARPETA_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CARPETA_BACKEND))

from app.database import SessionLocal
from app.models import models
from app.services.sistemas.comun.sesion import PaginaConSesion, ruta_sesion
from app.paths import CARPETA_SESIONES_API_REST_PAYMENT
from cargar_actas_semyt import (
    LABEL_FILTRO_NUMERO,
    TEXTO_BOTON_BUSCAR,
    INDICE_COLUMNA_NRO,
    _parsear_fila,
)
from app.services.sistemas.semyt.rutas import (
    URL_SEMYT,
)

from app.reglas.reglas_semyt import (
    COLUMNAS_TABLA,
    MAPA_ESTADO_SEMYT,
    normalizar_estado,
    parsear_fecha_hora,
    obtener_url_foto_de_fila,
)

# ---------------------------------------------------------
# Configuración
# ---------------------------------------------------------

NUMERO_DESDE_DEFAULT = 1
NUMERO_HASTA_DEFAULT = 328792

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
# SEMyT
# ---------------------------------------------------------

async def buscar_fila_por_numero(page, numero_acta: str):
    """
    Busca un número mediante el filtro "Filtrar por Número".

    No confía solamente en networkidle: después de la búsqueda verifica
    que el número de la primera fila realmente sea el solicitado.
    """
    numero_acta = str(numero_acta).strip()

    await page.get_by_label(LABEL_FILTRO_NUMERO).fill(numero_acta)
    await page.get_by_role("button", name=TEXTO_BOTON_BUSCAR).click()
    await page.wait_for_load_state("networkidle")

    for intento in range(1, INTENTOS_BUSQUEDA + 1):
        filas = page.locator("table tbody tr")
        cantidad = await filas.count()

        if cantidad == 0:
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

    return nuevo, estado_texto, estado_enum


async def procesar_acta(
    page,
    contexto,
    db,
    numero_acta: str,
    commit: bool,
    actas_existentes: set[str],
):
    """
    Procesa un único número.

    Retorna:
        ya_existe
        no_encontrada
        cargada
        error
    """

    numero_acta = str(numero_acta).strip()

    # Primera protección: no tocar nada si ya existe.
    if numero_acta in actas_existentes:
        return "ya_existe"

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
        return "no_encontrada"

    estado_texto = normalizar_estado(datos.get("estado", ""))

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
        "faltantes": 0,
        "cargadas": 0,
        "no_encontradas": 0,
        "errores": 0,
    }

    # -----------------------------------------------------
    # DB
    # -----------------------------------------------------

    db = SessionLocal()

    try:
        actas_existentes = cargar_actas_existentes(db)

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
                # Comparación DB -> memoria.
                # -------------------------------------------------

                if numero_acta in actas_existentes:
                    contadores["ya_existe"] += 1

                else:
                    contadores["faltantes"] += 1

                    resultado = await procesar_acta(
                        page,
                        contexto,
                        db,
                        numero_acta,
                        commit,
                        actas_existentes,
                    )

                    if resultado == "cargada":
                        contadores["cargadas"] += 1

                    elif resultado == "no_encontrada":
                        contadores["no_encontradas"] += 1

                    elif resultado == "error":
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
                        f"faltantes={contadores['faltantes']:,} | "
                        f"cargadas={contadores['cargadas']:,} | "
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
        db.close()

    # ---------------------------------------------------------
    # Resumen
    # ---------------------------------------------------------

    log("FIN", "")
    log("FIN", "========================================")
    log("FIN", "RESUMEN")
    log("FIN", "========================================")
    log("FIN", f"Existentes en DB: {contadores['ya_existe']:,}")
    log("FIN", f"Faltantes consultadas: {contadores['faltantes']:,}")
    log("FIN", f"Cargadas: {contadores['cargadas']:,}")
    log("FIN", f"No encontradas en SEMyT: {contadores['no_encontradas']:,}")
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
        help="Ignora y elimina el checkpoint existente.",
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