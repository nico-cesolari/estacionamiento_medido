# orquestador/trabajador.py
# (comentarios de cabecera sin cambios respecto al original)
import os
import sys
import signal
import fcntl
import traceback
import asyncio
import time
from datetime import datetime

from backend.orquestador.excepciones import EjecucionCancelada, TipoProcesoDesconocidoError


class _SalidaCapturada:
    # ... (sin cambios respecto al original)
    def __init__(self, cola, archivo_log, etiqueta):
        self._cola = cola
        self._archivo_log = archivo_log
        self._etiqueta = etiqueta
        self._buffer = ""

    def write(self, texto):
        self._buffer += texto
        while "\n" in self._buffer:
            linea, self._buffer = self._buffer.split("\n", 1)
            if linea.strip():
                self._emitir(linea)

    def flush(self):
        pass

    def _emitir(self, linea):
        marca = datetime.now().strftime("%H:%M:%S")
        self._archivo_log.write(f"[{marca}] {linea}\n")
        self._archivo_log.flush()
        self._cola.put({"tipo": "log", "texto": f"[{self._etiqueta}] {linea}"})


def _adquirir_lock_exclusivo(ruta_lock: str, tipo: str):
    os.makedirs(os.path.dirname(ruta_lock) or ".", exist_ok=True)
    archivo = open(ruta_lock, "w")
    try:
        fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        archivo.close()
        return None
    archivo.write(f"pid={os.getpid()}\ntipo={tipo}\ninicio={time.time()}\n")
    archivo.flush()
    return archivo


def _recuperar_sesion_ante_error(error: Exception):
    """Auto-recuperación de sesión (reemplaza el hábito manual de borrar
    los archivos de sesión "a mano" cada vez que algo se colgaba sin
    motivo aparente).

    Causa típica del problema: la sesión guardada en disco (storage_state)
    sigue teniendo un token presente, pero ese token ya venció del lado
    del servidor. La detección de "sesión activa" (ver
    pages/login_page.py) solo puede chequear que el token EXISTA, no que
    siga siendo válido contra el servidor — así que una sesión vencida se
    reporta como "activa", se omite el login, y recién explota más
    adelante en cualquier paso que dependa de estar realmente logueado.

    En vez de esperar a la limpieza programada de las 00:00 (ver
    ProgramadorAutomatico), CUALQUIER corrida que termine en error borra
    ya mismo los 3 archivos de sesión guardada: la corrida que sigue
    (automática con reintento, o manual) arranca sí o sí con un login
    limpio, en vez de arriesgarse a repetir el mismo error con la misma
    sesión rota. Es intencionalmente agresivo (mejor loguearse de más que
    quedar pegado indefinidamente con una sesión mala)."""
    try:
        from backend.utils.utils import Utilidades

        Utilidades.limpiar_sesiones_guardadas(
            motivo=f"Sesión guardada eliminada (auto-recuperación tras error: {type(error).__name__})"
        )
    except OSError:
        pass


def ejecutar_worker(tipo: str, etiqueta: str, origen: str, cola):
    def _al_recibir_cancelacion(signum, frame):
        raise EjecucionCancelada("Cancelado por el usuario desde el panel.")

    signal.signal(signal.SIGTERM, _al_recibir_cancelacion)

    ruta_log_temporal, ruta_log_final = _ruta_log_detallado(tipo)
    stdout_original, stderr_original = sys.stdout, sys.stderr
    conservar_log = False

    with open(ruta_log_temporal, "a", encoding="utf-8") as archivo_log:
        sys.stdout = _SalidaCapturada(cola, archivo_log, etiqueta)
        sys.stderr = sys.stdout

        def archivo_creado(ruta: str):
            cola.put({"tipo": "archivo", "ruta": ruta})

        from backend.configs import config

        lock = _adquirir_lock_exclusivo(config.ARCHIVO_LOCK_EJECUCION, tipo)
        if lock is None:
            print(
                "❌ Ya hay otra ejecución de este proyecto usando los archivos compartidos "
                "(puede ser el servicio en segundo plano, o el panel abierto en otra terminal). "
                "Se cancela esta corrida para no pisarla."
            )
            cola.put({
                "tipo": "error", "proceso": tipo,
                "texto": "Bloqueado por otra ejecución externa (lock de archivo ocupado).",
                "archivo_log": None,
            })
        else:
            try:
                cola.put({"tipo": "inicio", "proceso": tipo})
                print(f"=== Inicio de ejecución: {tipo} ===")
                try:
                    contexto_resultado = asyncio.run(
                        asyncio.wait_for(
                            _correr_tipo(tipo, archivo_creado),
                            timeout=config.TIMEOUT_MAXIMO_EJECUCION_SEGUNDOS,
                        )
                    )
                except asyncio.TimeoutError:
                    print(
                        f"❌ === La corrida superó los {config.TIMEOUT_MAXIMO_EJECUCION_SEGUNDOS}s "
                        f"máximos permitidos. Se aborta para no quedar colgada. ==="
                    )
                    raise
                archivo_subido = bool(contexto_resultado and getattr(contexto_resultado, "archivo_subido", False))
                print("=== Ejecución finalizada con éxito ===")
                if archivo_subido:
                    conservar_log = True
                else:
                    print(
                        "ℹ No se subió ningún archivo a ningún sistema externo en esta corrida: "
                        "por política, el log detallado no se conserva en disco."
                    )
                cola.put({"tipo": "completado", "proceso": tipo})

            except EjecucionCancelada:
                print("❌ === Ejecución cancelada por el usuario ===")
                cola.put({"tipo": "cancelado", "proceso": tipo})

            except Exception as e:
                print(f"❌ === ERROR: {type(e).__name__}: {e} ===")
                print(traceback.format_exc())
                _recuperar_sesion_ante_error(e)
                cola.put({
                    "tipo": "error", "proceso": tipo,
                    "texto": f"{type(e).__name__}: {e}",
                    "archivo_log": None,
                })
            finally:
                ruta_lock = config.ARCHIVO_LOCK_EJECUCION
                lock.close()
                try:
                    os.remove(ruta_lock)
                except OSError:
                    pass  # si ya no está (otra corrida lo re-tomó y sobrescribió), no pasa nada

    sys.stdout, sys.stderr = stdout_original, stderr_original

    if conservar_log:
        try:
            os.replace(ruta_log_temporal, ruta_log_final)
        except OSError:
            pass
    else:
        try:
            if os.path.exists(ruta_log_temporal):
                os.remove(ruta_log_temporal)
        except OSError:
            pass


async def _correr_tipo(tipo, archivo_creado):
    """Corre sobre un único BrowserContext async. Único tipo de proceso
    de negocio existente: 'pagos'."""
    from playwright.async_api import async_playwright
    from backend.configs import config
    from backend.workflows.pagos_workflow import PagosWorkflow

    async with async_playwright() as playwright:
        navegador = await playwright.chromium.launch(headless=not config.MODO_VISIBLE)
        try:
            if tipo == "pagos":
                return await PagosWorkflow(navegador).ejecutar(archivo_fn=archivo_creado, cerrar_navegador_al_final=False)
            else:
                raise TipoProcesoDesconocidoError(f"Tipo de proceso desconocido: '{tipo}'")
        finally:
            await navegador.close()


def _ruta_log_detallado(tipo: str) -> tuple[str, str]:
    from backend.configs import config
    from backend.utils.utils import Utilidades

    Utilidades.limpiar_carpetas_antiguas_por_fecha(config.CARPETA_LOGS, config.RETENCION_LOGS_DIAS)
    carpeta = Utilidades.carpeta_logs_del_dia(config.CARPETA_LOGS)
    nombre = f"{tipo}_{datetime.now():%H%M%S}.log"
    ruta_final = os.path.join(carpeta, nombre)
    ruta_temporal = ruta_final + ".tmp"
    return ruta_temporal, ruta_final