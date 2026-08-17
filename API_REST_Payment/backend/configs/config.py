# config.py
# -----------------------------------------------------------------------------
# Configuración del proceso de pagos.
#
# IMPORTANTE - SEGURIDAD DE CREDENCIALES:
# Las contraseñas y usuarios NO se escriben acá. Se leen desde variables de
# entorno, que a su vez se cargan desde un archivo ".env" (ver ".env.ejemplo").
# Esto permite subir este proyecto a un repositorio (GitHub, etc.) sin exponer
# datos sensibles, ya que el archivo ".env" real nunca se sube (está en
# ".gitignore").
#
# SEPARACIÓN CÓDIGO / DATOS:
# backend/ contiene ÚNICAMENTE código. Todo lo que se genera o se lee en
# tiempo de ejecución (sesiones guardadas, descargas, logs, el maestro de
# causas) vive fuera de backend/, en la carpeta "datos/" de la raíz del
# proyecto, para que backend/ se pueda versionar/copiar sin arrastrar
# archivos que cambian en cada corrida.
# -----------------------------------------------------------------------------

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
dotenv_path = BACKEND_DIR / ".env"

load_dotenv(dotenv_path)


def _variable_de_entorno_obligatoria(nombre: str) -> str:
    valor = os.getenv(nombre)
    if not valor:
        sys.exit(
            f"ERROR: la variable de entorno '{nombre}' no está configurada.\n"
            f"Creá un archivo '.env' (podés copiar '.env.ejemplo') y completá "
            f"'{nombre}=tu_valor'."
        )
    return valor


def _ruta(nombre: str, valor_por_defecto: str, base: Path) -> str:
    """Devuelve una ruta absoluta. Si el .env trae una ruta relativa, se
    interpreta desde 'base' (para que no dependa del cwd desde el que se
    arranque el proceso)."""
    valor = os.getenv(nombre, valor_por_defecto)
    ruta = Path(valor).expanduser()
    if not ruta.is_absolute():
        ruta = base / ruta
    return str(ruta)


def _ruta_datos(nombre: str, valor_por_defecto: str) -> str:
    return _ruta(nombre, valor_por_defecto, ROOT_DIR)


# --- Sitio 1: SEMyT (login) ---
SEMYT_USUARIO = _variable_de_entorno_obligatoria("SEMYT_USUARIO")
SEMYT_PASSWORD = _variable_de_entorno_obligatoria("SEMYT_PASSWORD")

# --- Sitio 2: Municipalidad de Villa María (login) ---
SIGI_USUARIO = _variable_de_entorno_obligatoria("CIDI_USUARIO")
SIGI_PASSWORD = _variable_de_entorno_obligatoria("CIDI_PASSWORD")

# --- Datos: todo lo que NO es código vive en <raíz>/datos/ ---
CARPETA_SESIONES = _ruta_datos("CARPETA_SESIONES", "datos/sesiones")

CARPETA_DESCARGAS_PAGOS = _ruta_datos("CARPETA_DESCARGAS_PAGOS", "datos/descargas/pagos")
# Carpeta temporal para los DOS Excel de actas vencidas usados para el cruce
# contra pagos (MULTAS_SIGEMI_CRUCE.xlsx y MULTAS_SIGI_CRUCE.xlsx, ver
# DescargasParalelasPaso). Ya NO se cachean de un día para otro: se
# descargan de nuevo en CADA corrida de pagos y se borran apenas se usan
# (ver CompararActasPaso), porque una multa que estaba "vencida" deja de
# estarlo en cuanto se registra su pago: si se reutilizara un Excel viejo,
# podría creerse vencida una multa que ya se actualizó.
CARPETA_CACHE_ACTAS_CRUCE = _ruta_datos("CARPETA_CACHE_ACTAS_CRUCE", "datos/descargas/cache_actas_cruce")
# Carpeta donde queda el causas.txt simplificado (ver más abajo).
CARPETA_DESCARGAS_CAUSAS = _ruta_datos("CARPETA_DESCARGAS_CAUSAS", "datos/descargas/causas")

# --- Archivo donde el proceso "recuerda" la última fecha procesada ---
ARCHIVO_ESTADO = _ruta_datos("ARCHIVO_ESTADO", "datos/state.json")

# --- Archivos SIGEMI ---
# Maestro: TODAS las causas que llegan de SIGEMI (puede tener miles de
# filas). Es de solo lectura, se actualiza a mano cuando llega gente nueva
# de SIGEMI. Nunca se escribe desde el código.
ARCHIVO_TOTAL_CAUSAS_SIGEMI = _ruta_datos("ARCHIVO_TOTAL_CAUSAS_SIGEMI", "datos/maestro/total_causas_sigemi.txt")
# Simplificado: SOLO las causas de ARCHIVO_TOTAL_CAUSAS_SIGEMI cuya acta
# aparece en el Excel de "multas vencidas viejas". Este SÍ lo genera el
# propio código en cada corrida de pagos (ver
# orquestador/comparador.generar_causas_simplificado) y es el que se usa
# para llenar CAUSA_NUMERO en el TXT de pagos (más chico y rápido de leer
# que el maestro completo).
ARCHIVO_CAUSAS_SIMPLIFICADO = _ruta_datos("ARCHIVO_CAUSAS_SIMPLIFICADO", "datos/descargas/causas/causas.txt")

# --- Modo visual: True = ves el navegador trabajar, False = en segundo plano ---
MODO_VISIBLE = os.getenv("MODO_VISIBLE", "True").strip().lower() in ("1", "true", "si", "sí")

# --- Logs ---
# Carpeta raíz de logs, organizados por DÍA: cada día tiene su propia
# subcarpeta (datos/logs/AAAA-MM-DD/) con todo lo de ese día adentro
# (pagos, automático, sistema, servicio), distinguido por el prefijo del
# nombre de archivo. Así alcanza con abrir una sola carpeta para ver todo
# lo que pasó un día puntual.
CARPETA_LOGS = _ruta_datos("CARPETA_LOGS", "datos/logs")
# Cuántos días se conservan los archivos de log antes de borrarse solos.
# Se limpia automáticamente en cada ejecución (ver
# Utilidades.limpiar_carpetas_antiguas_por_fecha), así la carpeta no crece
# sin límite con el automático corriendo 24/7.
RETENCION_LOGS_DIAS = int(os.getenv("RETENCION_LOGS_DIAS", "30") or 30)

# --- Lock entre procesos ---
# GestorProcesos.MAX_CONCURRENTES evita que dos ejecuciones arranquen desde
# el MISMO proceso (panel o servicio). Pero si en algún momento coexisten
# dos arranques distintos de "python3 -m backend.main" (por ejemplo, el
# servicio en segundo plano Y el panel interactivo abierto a mano), eso no
# alcanza: son procesos separados que no se conocen entre sí. Este archivo
# es un lock real de sistema operativo (fcntl.flock, ver
# orquestador/trabajador.py) que sí los coordina, vengan de donde vengan.
ARCHIVO_LOCK_EJECUCION = _ruta_datos("ARCHIVO_LOCK_EJECUCION", "datos/.ejecucion.lock")

# --- Horario del automático ---
# Valor de producción por defecto: pagos cada 60 minutos. Se puede acortar
# temporalmente por variable de entorno para PROBAR el automático sin
# esperar una hora (ver servicio/README o el mensaje de
# estado_servicio.command). Ejemplo para probar en 2 minutos: poner en
# backend/.env
#   INTERVALO_PAGOS_MINUTOS=2
# y sacarlo (o volver a 60) después de probar.
INTERVALO_PAGOS_MINUTOS = int(os.getenv("INTERVALO_PAGOS_MINUTOS", "60") or 60)

# --- Fecha desde la que existe "EM" (multas electrónicas) en SEMyT ---
FECHA_INICIO_EM = _variable_de_entorno_obligatoria("FECHA_INICIO_EM")
# -- rol a seleccionar en la municipalidad --
ROL_A_SELECCIONAR = _variable_de_entorno_obligatoria("ROL_SESION")

TIMEOUT_MAXIMO_EJECUCION_SEGUNDOS = int(os.getenv("TIMEOUT_MAXIMO_EJECUCION_SEGUNDOS", "900") or 900)
