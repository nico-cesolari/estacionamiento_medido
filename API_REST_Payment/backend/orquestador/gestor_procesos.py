# orquestador/gestor_procesos.py
# -----------------------------------------------------------------------------
# Administra las ejecuciones activas (pagos / multas):
#   - arranca cada una en su propio proceso (ver trabajador.py),
#   - respeta el máximo de ejecuciones simultáneas,
#   - no permite arrancar dos veces el mismo tipo a la vez,
#   - permite cancelar una ejecución puntual, limpiando los archivos que
#     esa ejecución haya generado.
#
# MAX_CONCURRENTES = 1 (a propósito, no es un límite arbitrario):
# pagos/multas comparten varios archivos de un solo uso con NOMBRE FIJO
# (no varía entre corridas, más allá de la carpeta del día):
#   - backend/sesiones/sesion_general.json, sesion_semyt.json, sesion_sigi.json
#     (se reescriben en CADA login, de cualquiera de los 2 tipos)
#   - backend/descargas/pagos/<AAAA-MM-DD>/"PAGOS DESCARGADOS.txt" (PagosService, vía DescargasParalelasPaso)
#   - backend/descargas/cache_actas_cruce/MULTAS_SIGEMI_CRUCE.xlsx y
#     MULTAS_SIGI_CRUCE.xlsx (ObtenerActasCrucePaso)
#   - backend/descargas/causas/causas.txt (CompararActasPaso)
# Si dos ejecuciones corrieran a la vez, podrían pisarse esos archivos entre
# sí (una los borra/reescribe mientras la otra los está leyendo). Con 1 sola
# ejecución activa por vez ese riesgo desaparece del todo, a costa de que
# pagos y multas ya no puedan correr literalmente en simultáneo (si ambas
# están "por tocarle el turno", la que llega segunda espera a que la primera
# termine; ver ProgramadorAutomatico, que reintenta sin perder el turno).
# -----------------------------------------------------------------------------

import multiprocessing
import os
import threading
from dataclasses import dataclass, field

from backend.orquestador.trabajador import ejecutar_worker

ETIQUETAS = {
    "pagos": "Actualizar pagos",
}

SEGUNDOS_ESPERA_CANCELACION = 8

@dataclass
class _Ejecucion:
    id: str
    tipo: str
    origen: str  # "manual" | "automático"
    proceso: multiprocessing.Process
    cola: "multiprocessing.Queue"
    archivos_creados: list = field(default_factory=list)
    on_finalizar: object = None


class GestorProcesos:
    MAX_CONCURRENTES = 1

    def __init__(self, consola):
        self.consola = consola
        self._ejecuciones: dict[str, _Ejecucion] = {}
        self._lock = threading.Lock()
        self._contador = 0

    def hay_lugar(self) -> bool:
        with self._lock:
            return len(self._ejecuciones) < self.MAX_CONCURRENTES

    def esta_en_ejecucion(self, tipo: str) -> bool:
        with self._lock:
            return any(e.tipo == tipo for e in self._ejecuciones.values())

    def ejecucion_activa(self, id_ejec: str) -> bool:
        with self._lock:
            return id_ejec in self._ejecuciones

    def motivo_bloqueo(self, tipo: str) -> str | None:
        with self._lock:
            if len(self._ejecuciones) >= self.MAX_CONCURRENTES:
                otras = ", ".join(sorted({ETIQUETAS[e.tipo] for e in self._ejecuciones.values()}))
                return f"ya hay una ejecución en curso ({otras}); esperando a que termine"
            return None

    def listar_en_ejecucion(self):
        with self._lock:
            return [
                (e.id, e.tipo, ETIQUETAS[e.tipo], e.origen)
                for e in self._ejecuciones.values()
            ]

    def iniciar(
        self,
        tipo: str,
        origen: str = "manual",
        on_finalizar=None,
    ):
        with self._lock:
            if len(self._ejecuciones) >= self.MAX_CONCURRENTES:
                return None
            if any(e.tipo == tipo for e in self._ejecuciones.values()):
                return None

            self._contador += 1
            id_ejec = f"{tipo}-{self._contador}"
            etiqueta = ETIQUETAS[tipo].upper()
            if origen == "automático":
                etiqueta += " · AUTO"

            cola = multiprocessing.Queue()
            proceso = multiprocessing.Process(
                target=ejecutar_worker,
                args=(tipo, etiqueta, origen, cola),
                name=f"trabajador-{id_ejec}",
                daemon=True,
            )
            ejecucion = _Ejecucion(
                id=id_ejec, tipo=tipo, origen=origen, proceso=proceso, cola=cola,
                on_finalizar=on_finalizar,
            )
            self._ejecuciones[id_ejec] = ejecucion

        proceso.start()
        hilo_lector = threading.Thread(target=self._leer_cola, args=(ejecucion,), daemon=True)
        hilo_lector.start()
        return id_ejec

    def _notificar_fin(self, ejecucion: "_Ejecucion", resultado: str):
        if not ejecucion.on_finalizar:
            return
        try:
            ejecucion.on_finalizar(resultado)
        except Exception as error:
            self.consola.imprimir(
                f"❌ Error en el callback de finalización de '{ejecucion.tipo}': {error}"
            )

    def _leer_cola(self, ejecucion: "_Ejecucion"):
        while True:
            try:
                mensaje = ejecucion.cola.get(timeout=1)
            except Exception:
                if not ejecucion.proceso.is_alive():
                    self.consola.imprimir(
                        f"❌ '{ETIQUETAS[ejecucion.tipo]}' terminó sin avisar (proceso caído)."
                    )
                    self._finalizar(ejecucion.id)
                    self._notificar_fin(ejecucion, "error")
                    self.consola.pedir_volver_si_esta_esperando()
                    return
                continue

            tipo_msj = mensaje.get("tipo")

            if tipo_msj == "log":
                self.consola.imprimir(mensaje["texto"])

            elif tipo_msj == "archivo":
                ejecucion.archivos_creados.append(mensaje["ruta"])

            elif tipo_msj == "completado":
                self._finalizar(ejecucion.id)
                self._notificar_fin(ejecucion, "completado")
                self.consola.pedir_volver_si_esta_esperando()
                return

            elif tipo_msj == "cancelado":
                self._finalizar(ejecucion.id)
                self._notificar_fin(ejecucion, "cancelado")
                self.consola.pedir_volver_si_esta_esperando()
                return

            elif tipo_msj == "error":
                detalle = mensaje.get("archivo_log")
                if detalle:
                    self.consola.imprimir(f"❌ Detalle técnico completo en: {detalle}")
                else:
                    # Política de logs: si no se llegó a subir ningún
                    # archivo a un sistema externo, el log detallado de
                    # esta corrida no se conserva en disco (ver
                    # orquestador/trabajador.py). Lo que se vio acá en
                    # vivo por consola es todo lo que queda de esta corrida.
                    self.consola.imprimir(
                        "ℹ El log detallado de esta corrida no se conservó en disco "
                        "(no se llegó a subir ningún archivo)."
                    )
                self._finalizar(ejecucion.id)
                self._notificar_fin(ejecucion, "error")
                self.consola.pedir_volver_si_esta_esperando()
                return

    def _finalizar(self, id_ejec: str):
        with self._lock:
            self._ejecuciones.pop(id_ejec, None)

    def cancelar(self, id_ejec: str) -> bool:
        with self._lock:
            ejecucion = self._ejecuciones.get(id_ejec)
        if not ejecucion:
            self.consola.imprimir("❌ Esa ejecución ya no está activa.")
            return False

        etiqueta = ETIQUETAS[ejecucion.tipo]
        self.consola.imprimir(f"🛑 Cancelando '{etiqueta}'... (le damos unos segundos para que limpie solo)")
        ejecucion.proceso.terminate()
        ejecucion.proceso.join(timeout=SEGUNDOS_ESPERA_CANCELACION)

        if ejecucion.proceso.is_alive():
            self.consola.imprimir(f"❌ '{etiqueta}' no respondió a tiempo; forzando el cierre.")
            ejecucion.proceso.kill()
            ejecucion.proceso.join(timeout=3)
            self._limpiar_archivos(ejecucion)

        self._finalizar(id_ejec)
        self.consola.imprimir(f"✅ '{etiqueta}' cancelado.")
        return True

    def cancelar_todos(self):
        with self._lock:
            ids = list(self._ejecuciones.keys())
        for id_ejec in ids:
            self.cancelar(id_ejec)

    def _limpiar_archivos(self, ejecucion: "_Ejecucion"):
        for ruta in ejecucion.archivos_creados:
            try:
                if ruta and os.path.exists(ruta):
                    os.remove(ruta)
                    self.consola.imprimir(f"🧹 Archivo eliminado: {ruta}")
            except OSError as e:
                self.consola.imprimir(f"❌ No se pudo eliminar {ruta}: {e}")