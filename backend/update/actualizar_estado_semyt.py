# update/actualizar_estado_semyt.py
"""
FUNCIONAL PERFECTO
python update/actualizar_estado_semyt.py --headed
python update/actualizar_estado_semyt.py
python update/actualizar_estado_semyt.py --commit
"""
import argparse
import asyncio
import contextlib
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional
import pandas as pd
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.paths import CARPETA_SESIONES_API_REST_PAYMENT
from app.pasos.procesar_actas_semyt import ARCHIVO_SESION, URL_SEMYT, _leer_acta, _registros_pendientes
from app.reglas.reglas_semyt import (
    ESTADOS_IGNORADOS_SEMYT,
    ESTADO_PAGADA_EN_JUZGADO,
    MAPA_ESTADO_SEMYT,
    pagada_en_juzgado_con_datos,
)
from app.services.estados import aplicar_cambios_estado
from app.services.sistemas.comun.sesion import ruta_sesion
from app.services.sistemas.semyt.pages.exportar_actas_page import (
    ExportarActasPage,
)
from app.services.sistemas.semyt.pages.login_page import LoginSemytPage
import os
from datetime import datetime
from dotenv import load_dotenv
ENV_PATH = Path(__file__).resolve().parents[2] / "API_REST_Payment" / "backend" / ".env"
load_dotenv(ENV_PATH)
FECHA_INICIO_EM = datetime.strptime(
    os.environ["FECHA_INICIO_EM"],
    "%d/%m/%Y",
)
# -----------------------------------------------------------------------------
# Credenciales
# -----------------------------------------------------------------------------

@dataclass
class CredencialesSemyt:
    usuario: str
    contrasena: str


def _cargar_credenciales_semyt():
    usuario = os.environ.get("SEMYT_USUARIO")
    contrasena = os.environ.get("SEMYT_PASSWORD")

    if not usuario or not contrasena:
        raise RuntimeError(
            "Faltan SEMYT_USUARIO y/o SEMYT_PASSWORD en el .env"
        )

    return CredencialesSemyt(
        usuario=usuario,
        contrasena=contrasena,
    )


# -----------------------------------------------------------------------------
# Sesión SEMyT
# -----------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _pagina_semyt_con_login_automatico(headless: bool = True):
    archivo_sesion = ruta_sesion(ARCHIVO_SESION, CARPETA_SESIONES_API_REST_PAYMENT)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        try:
            storage_state = str(archivo_sesion) if archivo_sesion.exists() else None
            contexto = await browser.new_context(accept_downloads=True, storage_state=storage_state)
            page = await contexto.new_page()
            login_page = LoginSemytPage(page)

            print("[SEMyT-ARCHIVO] 🔐 Verificando sesión...")
            await login_page.abrir_semyt_con_sesion()

            if await login_page.sesion_semyt_activa():
                print("[SEMyT-ARCHIVO] ✅ Sesión existente detectada. Login omitido.")
            else:
                print("[SEMyT-ARCHIVO] ❌ Sesión no válida. Iniciando login...")
                credenciales = _cargar_credenciales_semyt()
                await login_page.iniciar_sesion_semyt(credenciales)
                print("[SEMyT-ARCHIVO] ✅ Login completado.")

            archivo_sesion.parent.mkdir(parents=True, exist_ok=True)
            await contexto.storage_state(path=str(archivo_sesion))

            yield page
        finally:
            await browser.close()


# -----------------------------------------------------------------------------
# Grilla de actas
# -----------------------------------------------------------------------------

async def _abrir_grilla_actas_en_nueva_pestana(page):
    """Abre #/actas en una pestaña nueva utilizando el mismo contexto de
    Playwright, conservando cookies y localStorage de la sesión."""
    print("[SEMyT-ARCHIVO] Abriendo grilla de actas en una pestaña nueva...")

    base_url = URL_SEMYT.split("#", 1)[0]
    url_actas = f"{base_url}#/actas"
    nueva_page = await page.context.new_page()

    try:
        await nueva_page.goto(url_actas, wait_until="domcontentloaded", timeout=60000)

        try:
            await nueva_page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        login_page = LoginSemytPage(nueva_page)
        if not await login_page.sesion_semyt_activa():
            raise RuntimeError("[SEMyT-ARCHIVO] ❌ La nueva pestaña no tomó la sesión.")

        await nueva_page.get_by_label("Filtrar por Número").wait_for(state="visible", timeout=15000)
        print("[SEMyT-ARCHIVO] ✅ Grilla de actas lista en nueva pestaña.")
        return nueva_page

    except Exception:
        await nueva_page.close()
        raise


# -----------------------------------------------------------------------------
# Exportación del Excel
# -----------------------------------------------------------------------------

async def _descargar_excel_actas_vencidas(page):
    fecha_desde = FECHA_INICIO_EM.strftime("%Y-%m-%d")
    fecha_hasta = date.today().strftime("%Y-%m-%d")

    print(
        f"[SEMyT-ARCHIVO] Descargando Excel de actas vencidas "
        f"({FECHA_INICIO_EM.strftime('%d/%m/%Y')} a "
        f"{date.today().strftime('%d/%m/%Y')})..."
    )

    exportar = ExportarActasPage(page)

    await exportar.abrir()
    await exportar.completar_fechas(fecha_desde, fecha_hasta)

    descarga = await exportar.descargar()

    fd, ruta_temporal = tempfile.mkstemp(
        suffix=".xlsx",
        prefix="actas_vencidas_",
    )
    os.close(fd)

    ruta_excel = Path(ruta_temporal)

    await descarga.save_as(str(ruta_excel))

    return ruta_excel


# -----------------------------------------------------------------------------
# Procesamiento
# -----------------------------------------------------------------------------

def _resumen_vacio(vencidas_en_db: int, vencidas_segun_archivo: int = 0, a_revisar: int = 0) -> dict:
    """Arma el dict de resumen con contadores en cero -- evita repetir la
    misma estructura de 8 claves en los 2 early-return de la función de
    abajo."""
    return {
        "vencidas_en_db": vencidas_en_db,
        "vencidas_segun_archivo_semyt": vencidas_segun_archivo,
        "a_revisar_individualmente": a_revisar,
        "actualizados": 0,
        "sin_cambios": 0,
        "ignorados_por_estado": 0,
        "no_encontrados_en_semyt": 0,
        "errores": 0,
    }


async def actualizar_estado_semyt_por_archivos(
    db: Session, limit: Optional[int] = None, commit: bool = False, delay: float = 0.0, headless: bool = True,
):
    registros = _registros_pendientes(db)
    vencidas_en_db = len(registros)
    print(f"[SEMyT-ARCHIVO] {vencidas_en_db} acta(s) 'Vencida' en la base.")

    if not registros:
        return _resumen_vacio(vencidas_en_db=0)

    async with _pagina_semyt_con_login_automatico(headless=headless) as page:
        ruta_excel = await _descargar_excel_actas_vencidas(page)

        try:
            df = pd.read_excel(ruta_excel, dtype=str)
        finally:
            ruta_excel.unlink(missing_ok=True)

        def _normalizar_columna(nombre):
            import unicodedata

            nombre = str(nombre).strip().lower()
            nombre = unicodedata.normalize("NFKD", nombre)
            nombre = "".join(
                c for c in nombre
                if not unicodedata.combining(c)
            )
            return nombre

        columnas_normalizadas = {
            _normalizar_columna(columna): columna
            for columna in df.columns
        }

        columna_numero = columnas_normalizadas.get("numero")

        if columna_numero is None:
            raise RuntimeError(
                "[SEMyT-ARCHIVO] ❌ No se encontró la columna de número de acta "
                f"en el Excel. Columnas encontradas: {list(df.columns)}"
            )

        actas_vencidas_semyt = set()

        for valor in df[columna_numero]:
            if pd.isna(valor):
                continue

            try:
                numero = int(float(str(valor).strip()))
                actas_vencidas_semyt.add(numero)
            except (TypeError, ValueError):
                continue

        print(
            f"[SEMyT-ARCHIVO] "
            f"{len(actas_vencidas_semyt)} acta(s) siguen 'Vencida' según SEMyT."
        )

        def _normalizar_numero_acta(valor):
            if valor is None or pd.isna(valor):
                return None

            try:
                return int(float(str(valor).strip()))
            except (TypeError, ValueError):
                return None


        registros_a_revisar = [
            r for r in registros
            if _normalizar_numero_acta(r.acta) not in actas_vencidas_semyt
        ]

        if limit is not None:
            registros_a_revisar = registros_a_revisar[:limit]

        print(
            f"[SEMyT-ARCHIVO] "
            f"{len(registros_a_revisar)} acta(s) ya NO figuran vencidas "
            f"en el archivo -- se re-consultan individualmente."
        )

        if not registros_a_revisar:
            return _resumen_vacio(vencidas_en_db, vencidas_segun_archivo=len(actas_vencidas_semyt))

        page_actas = await _abrir_grilla_actas_en_nueva_pestana(page)

        actualizados = sin_cambios = ignorados_por_estado = no_encontrados = errores = 0

        try:
            for indice, registro in enumerate(registros_a_revisar):
                if indice > 0 and delay > 0:
                    await asyncio.sleep(delay)

                try:
                    datos_acta = await _leer_acta(page_actas, registro.acta)

                    if datos_acta is None:
                        print(f"[SEMyT-ARCHIVO] Acta {registro.acta} no encontrada en SEMyT.")
                        no_encontrados += 1
                        continue

                    estado_texto = datos_acta["estado"]

                    if estado_texto in ESTADOS_IGNORADOS_SEMYT:
                        ignorados_por_estado += 1
                        continue

                    if estado_texto == ESTADO_PAGADA_EN_JUZGADO and pagada_en_juzgado_con_datos(
                        datos_acta["vencimiento"], datos_acta["importe"]
                    ):
                        ignorados_por_estado += 1
                        continue

                    nuevo_estado = MAPA_ESTADO_SEMYT.get(estado_texto)
                    if nuevo_estado is None:
                        print(f"[SEMyT-ARCHIVO] Estado desconocido '{estado_texto}' en acta {registro.acta}.")
                        errores += 1
                        continue

                    if registro.estado_semyt == nuevo_estado:
                        sin_cambios += 1
                        continue

                    prefijo = "[COMMIT]" if commit else "[DRY-RUN]"
                    print(f"{prefijo} acta {registro.acta}: {registro.estado_semyt} -> {nuevo_estado.value}")

                    if commit:
                        aplicar_cambios_estado(db, registro, {"estado_semyt": nuevo_estado})
                        db.commit()

                    actualizados += 1

                except Exception as exc:
                    print(f"[SEMyT-ARCHIVO] Error procesando acta {registro.acta}: {exc}")
                    errores += 1

        finally:
            await page_actas.close()

    modo = "COMMIT (cambios grabados)" if commit else "DRY-RUN (no se grabó nada)"
    print()
    print(f"Modo: {modo}")

    return {
        "vencidas_en_db": vencidas_en_db,
        "vencidas_segun_archivo_semyt": len(actas_vencidas_semyt),
        "a_revisar_individualmente": len(registros_a_revisar),
        "actualizados": actualizados,
        "sin_cambios": sin_cambios,
        "ignorados_por_estado": ignorados_por_estado,
        "no_encontrados_en_semyt": no_encontrados,
        "errores": errores,
    }


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Actualiza el estado SEMyT de las actas que ya no aparecen como vencidas en el Excel."
    )
    parser.add_argument("--limit", type=int, default=None, help="Cantidad máxima de actas a re-consultar.")
    parser.add_argument("--commit", action="store_true", help="Graba los cambios en la base de datos.")
    parser.add_argument("--delay", type=float, default=0.0, help="Segundos entre consultas individuales.")
    parser.add_argument("--headed", action="store_true", help="Ejecuta Chromium con interfaz visible.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        resumen = asyncio.run(
            actualizar_estado_semyt_por_archivos(
                db=db, limit=args.limit, commit=args.commit, delay=args.delay, headless=not args.headed,
            )
        )

        print()
        print(f"  vencidas_en_db: {resumen['vencidas_en_db']}")
        print(f"  vencidas_segun_archivo_semyt: {resumen['vencidas_segun_archivo_semyt']}")
        print(f"  a_revisar_individualmente: {resumen['a_revisar_individualmente']}")
        print(f"  actualizados: {resumen['actualizados']}")
        print(f"  sin_cambios: {resumen['sin_cambios']}")
        print(f"  ignorados_por_estado: {resumen['ignorados_por_estado']}")
        print(f"  no_encontrados_en_semyt: {resumen['no_encontrados_en_semyt']}")
        print(f"  errores: {resumen['errores']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()