# orquestador/aplicacion.py
# -----------------------------------------------------------------------------
# Punto de arranque real del panel (invocado desde backend/main.py):
#   1) valida/renueva las sesiones de SEMyT y SIGI UNA vez, de forma
#      secuencial en este mismo proceso (todavía no hay nada corriendo en
#      simultáneo en este punto, así que no hace falta un proceso aparte).
#      Si ya había una sesión guardada y sigue activa, esto es rápido: no
#      vuelve a pedir usuario/contraseña, solo lo confirma.
#   2) arma el gestor de procesos y el menú, y entra en el bucle interactivo.
#
# El modo "Automático" NO se controla desde el panel interactivo: corre
# ÚNICAMENTE cuando el proceso se arranca con "--servicio" (ver
# iniciar_servicio más abajo), que es lo que usa el servicio de launchd.
# -----------------------------------------------------------------------------

import os
import signal
import sys
import asyncio
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from backend.configs import config
from backend.orquestador.console import Console
from backend.orquestador.gestor_procesos import GestorProcesos
from backend.orquestador.menu import Menu
from backend.orquestador.programador import ProgramadorAutomatico
from backend.utils.utils import Utilidades
from playwright.async_api import async_playwright
from backend.workflows.login_workflow import LoginProyectoWorkflow

class _SalidaDiaria:
    """Redirige stdout/stderr a un archivo que rota solo a la carpeta del
    día correspondiente (backend/logs/AAAA-MM-DD/servicio.log)."""

    def __init__(self, carpeta_logs_raiz: str, nombre_archivo: str = "servicio.log"):
        self._carpeta_logs_raiz = carpeta_logs_raiz
        self._nombre_archivo = nombre_archivo
        self._fecha_actual = None
        self._archivo = None

    def _asegurar_archivo_del_dia(self):
        ahora = datetime.now()
        if ahora.date() != self._fecha_actual:
            if self._archivo:
                self._archivo.close()
            carpeta = Utilidades.carpeta_logs_del_dia(self._carpeta_logs_raiz, ahora)
            self._archivo = open(os.path.join(carpeta, self._nombre_archivo), "a", encoding="utf-8")
            self._fecha_actual = ahora.date()

    def write(self, texto):
        self._asegurar_archivo_del_dia()
        self._archivo.write(texto)

    def flush(self):
        if self._archivo:
            self._archivo.flush()

    def isatty(self):
        return False


class Aplicacion:
    def __init__(self):
        self.consola = Console()

    def iniciar(self):
        self._mostrar_encabezado()
        self._limpiar_logs_temporales_huerfanos()

        try:
            self._verificar_y_guardar_sesiones()
        except SystemExit:
            raise
        except Exception as e:
            archivo_log = self._guardar_error_login_inicial(e)
            self.consola.imprimir(f"❌ No se pudo validar el login inicial: {type(e).__name__}: {e}")
            self.consola.imprimir("   Ocurrió durante: verificación de sesiones SEMyT/SIGI.")
            self.consola.imprimir(f"   Detalle técnico completo en: {archivo_log}")
            self.consola.imprimir("   Si el navegador abrió bien, revisá credenciales y URLs en backend/.env.")
            sys.exit(1)

        gestor = GestorProcesos(self.consola)

        def _salida_prolija(signum=None, frame=None):
            self.consola.imprimir("\n👋 Interrumpido (Ctrl+C). Cancelando procesos activos antes de salir...")
            gestor.cancelar_todos()
            sys.exit(0)

        signal.signal(signal.SIGINT, _salida_prolija)

        Menu(gestor, self.consola).ejecutar()

    def iniciar_servicio(self):
        """Punto de entrada para correr como servicio de verdad (sin panel
        interactivo), pensado para arrancar solo desde launchd al prender
        la máquina/iniciar sesión (ver carpeta servicio/).

        Este es el ÚNICO lugar donde se activa el modo Automático."""
        self._mostrar_encabezado()
        self.consola.imprimir("🧰 Modo servicio (sin panel interactivo).")
        self._limpiar_logs_temporales_huerfanos()

        sys.stdout = _SalidaDiaria(config.CARPETA_LOGS)
        sys.stderr = sys.stdout

        if config.MODO_VISIBLE:
            self.consola.imprimir(
                "⚠ MODO_VISIBLE=True en el .env, pero un servicio no tiene sesión gráfica "
                "garantizada: se fuerza headless igual para esta corrida."
            )

        detener = threading.Event()

        def _al_recibir_parada(signum=None, frame=None):
            detener.set()

        signal.signal(signal.SIGTERM, _al_recibir_parada)
        signal.signal(signal.SIGINT, _al_recibir_parada)

        self._verificar_sesiones_con_reintentos(detener)
        if detener.is_set():
            self.consola.imprimir("👋 Señal de apagado recibida durante el arranque. Saliendo.")
            return

        gestor = GestorProcesos(self.consola)
        programador = ProgramadorAutomatico(gestor, self.consola)
        programador.activar()

        self.consola.imprimir("✅ Servicio activo. Esperando la próxima ejecución programada...")
        detener.wait()

        self.consola.imprimir("\n👋 Deteniendo servicio: cancelando procesos activos antes de salir...")
        programador.desactivar()
        gestor.cancelar_todos()
        self.consola.imprimir("✅ Servicio detenido de forma prolija.")

    def _mostrar_encabezado(self):
        self.consola.imprimir("")
        self.consola.imprimir("╔══════════════════════════════════════════════════╗")
        self.consola.imprimir("║   Panel de Automatización — Juzgado de Faltas    ║")
        self.consola.imprimir("║   SIGI - Municipalidad de Villa María            ║")
        self.consola.imprimir("╚══════════════════════════════════════════════════╝")

    def _limpiar_logs_temporales_huerfanos(self):
        """Al arrancar (panel o servicio) no debería haber ninguna
        ejecución en curso todavía, así que cualquier '*.log.tmp' que
        haya quedado en backend/logs/ es de una corrida anterior que se
        cortó de una forma muy abrupta (kill -9, corte de luz, etc.) antes
        de poder decidir si conservaba su log o lo borraba (ver
        orquestador/trabajador.py). Se limpia acá para que no se acumulen
        para siempre."""
        try:
            Utilidades.limpiar_logs_temporales_huerfanos(config.CARPETA_LOGS)
        except OSError:
            pass

    def _verificar_y_guardar_sesiones(self, forzar_headless: bool = False):
        asyncio.run(self._verificar_y_guardar_sesiones_async(forzar_headless))

    async def _verificar_y_guardar_sesiones_async(self, forzar_headless: bool):
        self.consola.imprimir("\n🔐 Verificando sesiones (SEMyT y SIGI)...")
        headless = True if forzar_headless else not config.MODO_VISIBLE
        async with async_playwright() as playwright:
            navegador = await playwright.chromium.launch(headless=headless)
            try:
                await LoginProyectoWorkflow(navegador).ejecutar(
                    log=self.consola.imprimir,
                    cerrar_navegador_al_final=False,
                )
            finally:
                await navegador.close()
        self.consola.imprimir(
            "✅ Sesiones listas. Si ya estabas logueado de antes, se reutilizó la sesión "
            "guardada y no se volvió a pedir usuario/contraseña.\n"
        )

    def _verificar_sesiones_con_reintentos(self, detener: threading.Event):
        intento = 0
        espera = timedelta(minutes=1)
        tope = timedelta(minutes=30)
        while not detener.is_set():
            intento += 1
            try:
                self._verificar_y_guardar_sesiones(forzar_headless=True)
                return
            except Exception as e:
                archivo_log = self._guardar_error_login_inicial(e)
                minutos = max(1, int(espera.total_seconds() // 60))
                self.consola.imprimir(
                    f"❌ [Servicio] Intento {intento} de validar el login inicial falló: "
                    f"{type(e).__name__}: {e}"
                )
                self.consola.imprimir(f"   Detalle técnico completo en: {archivo_log}")
                self.consola.imprimir(f"   Reintentando en {minutos} min (sin límite de intentos)...")
                if detener.wait(timeout=espera.total_seconds()):
                    return
                espera = min(espera * 2, tope)

    def _guardar_error_login_inicial(self, error: Exception) -> str:
        Utilidades.limpiar_carpetas_antiguas_por_fecha(config.CARPETA_LOGS, config.RETENCION_LOGS_DIAS)
        carpeta = Path(Utilidades.carpeta_logs_del_dia(config.CARPETA_LOGS))
        ruta = carpeta / f"sistema_login_inicial_{datetime.now():%H%M%S}.log"
        with ruta.open("w", encoding="utf-8") as archivo:
            archivo.write("Error durante la verificación inicial de sesiones.\n")
            archivo.write(f"Tipo: {type(error).__name__}\n")
            archivo.write(f"Mensaje: {error}\n\n")
            archivo.write(traceback.format_exc())
        return str(ruta)