# orquestador/programador.py
# -----------------------------------------------------------------------------
# Modo "Automático": dispara solo, sin que el usuario tenga que elegir nada
# más.
#
#   - Pagos: arranca de inmediato apenas se activa el automático (no espera
#     un intervalo entero), y después se repite cada 1 hora
#     (INTERVALO_PAGOS_MINUTOS), sola.
#   - Limpieza de sesiones: todos los días a las 00:00 se borran los 3
#     archivos de sesión guardada (sesion_general.json, sesion_semyt.json,
#     sesion_sigi.json), para forzar un login nuevo desde cero. Corre
#     ANTES de la siguiente actualización de pagos: si a las 00:00 hay una
#     ejecución en curso, la limpieza espera a que termine (no pisa una
#     sesión que se está usando/escribiendo en ese momento) y se hace en
#     el primer chequeo libre después, siempre antes de que pagos vuelva
#     a arrancar.
#
# El horario de pagos se lee de backend/configs/config.py
# (INTERVALO_PAGOS_MINUTOS), que a su vez lo toma de backend/.env si está,
# con el valor de producción como default. Sirve para PROBAR sin esperar
# una hora: poner algo como INTERVALO_PAGOS_MINUTOS=2 en el .env, ver que
# dispare en 2 minutos, y sacarlo (o volver a 60) para producción.
#
# SIN LUGAR PARA ARRANCAR (ej. ya hay algo corriendo):
# No se pierde el turno: se reintenta en el próximo chequeo (cada
# SEGUNDOS_ENTRE_CHEQUEOS) hasta que se libere un lugar. Para no spamear el
# mismo aviso todo el tiempo mientras espera, solo lo informa la primera vez
# que queda bloqueado (y de nuevo si cambia el motivo).
#
# SI LA EJECUCIÓN ARRANCA PERO NO TERMINA BIEN (error, o se cae sin avisar):
# esto es justamente "no se llega a ejecutar perfectamente en el horario
# programado". Acá SÍ hay reintentos con backoff creciente, hasta
# MAX_REINTENTOS veces (ver BACKOFF_REINTENTOS): 5, 15 y 30 minutos después
# del intento anterior. Si se agotan y sigue fallando, se deja de insistir
# por esta vez y se retoma en el próximo horario normal (la próxima hora),
# para no quedar reintentando en bucle para siempre si el sitio está caído.
# Si el usuario cancela a mano una corrida disparada por el automático, NO
# se reintenta sola (se asume que fue algo deliberado): se retoma en el
# horario normal directamente. La limpieza de sesiones no tiene reintentos
# con backoff: si se bloquea, simplemente reintenta en el próximo chequeo
# (cada SEGUNDOS_ENTRE_CHEQUEOS) sin gastar ningún "intento".
#
# Además de imprimir en la consola en vivo (que se pierde si se cierra la
# terminal), cada decisión del automático (activar/desactivar/disparar/
# reintentar/error/limpieza) queda anotada en
# backend/logs/AAAA-MM-DD/automatico.log (la carpeta del día en curso,
# junto con los demás logs de ese mismo día), para poder revisar después
# qué pasó aunque la terminal ya no esté.
#
# El bucle en sí corre en un hilo daemon (_bucle). Todo su cuerpo está
# envuelto en try/except: si algo inesperado tirara una excepción ahí
# adentro y no se atajara, el hilo moriría en silencio y el panel seguiría
# mostrando "ACTIVO" para siempre sin que nada vuelva a dispararse. Con el
# try/except, un error puntual se loguea y el bucle sigue vivo.
# -----------------------------------------------------------------------------

import os
import threading
import traceback
from datetime import datetime, timedelta

from backend.configs import config

INTERVALO_PAGOS = timedelta(minutes=config.INTERVALO_PAGOS_MINUTOS)
SEGUNDOS_ENTRE_CHEQUEOS = 15

# Reintentos cuando una corrida automática arranca pero termina en error (o
# se cae sin avisar). No aplica a "no había lugar para arrancar": eso ya se
# reintenta solo, sin límite, porque no gastó ningún intento real.
MAX_REINTENTOS = 3
BACKOFF_REINTENTOS = [timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=30)]

_ETIQUETAS_CLAVE = {
    "pagos": "actualizar pagos",
}


class ProgramadorAutomatico:
    def __init__(self, gestor, consola):
        self.gestor = gestor
        self.consola = consola
        self.activo = False
        self.proxima_pagos = None
        self.proxima_limpieza_sesiones = None
        self._detener = threading.Event()
        self._hilo = None
        # Evita repetir el mismo aviso de "bloqueado" en cada chequeo
        # mientras se espera lugar para correr.
        self._ultimo_bloqueo_avisado = {"pagos": None, "sesiones": None}
        # Cuántos intentos fallidos consecutivos lleva "pagos" (se resetea
        # apenas termina bien, o al agotar MAX_REINTENTOS). La limpieza de
        # sesiones no usa reintentos con backoff: si está bloqueada, vuelve
        # a intentar en el próximo chequeo nomás.
        self._intentos = {"pagos": 0}

    def activar(self):
        if self.activo:
            return
        self._limpiar_logs_propios_viejos()
        ahora = datetime.now()
        # Pagos arranca YA (en el próximo chequeo, a los pocos segundos),
        # no espera un intervalo entero desde que se activa. Es seguro
        # hacerlo así en cada activación (incluso después de un reinicio
        # del servicio): si no hay pagos nuevos para subir, esa corrida
        # simplemente no hace nada (ver CargarPagosPaso).
        self.proxima_pagos = ahora
        self.proxima_limpieza_sesiones = self._proxima_medianoche(ahora)
        self.activo = True
        self._detener.clear()
        self._ultimo_bloqueo_avisado = {"pagos": None, "sesiones": None}
        self._intentos = {"pagos": 0}
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._hilo.start()

        self._registrar(
            "🤖 Modo automático activado.\n"
            "   Actualización de pagos: arrancando ya mismo.\n"
            f"   Limpieza diaria de sesiones: {self.proxima_limpieza_sesiones:%d/%m/%Y %H:%M}"
        )

    def desactivar(self):
        if not self.activo:
            return
        self._detener.set()
        self.activo = False
        self._registrar(
            "🤖 Modo automático desactivado. (Si había una ejecución en curso disparada por el "
            "automático, no se cancela sola: cancelala desde su propia opción si querés detenerla.)"
        )

    def resumen_proximas_ejecuciones(self) -> str:
        if self.activo:
            prefijo = "ACTIVO"
            proxima_pagos = self.proxima_pagos
            proxima_limpieza = self.proxima_limpieza_sesiones
        else:
            prefijo = "INACTIVO"
            ahora = datetime.now()
            proxima_pagos = ahora + INTERVALO_PAGOS
            proxima_limpieza = self._proxima_medianoche(ahora)

        return (
            f"{prefijo} — próx. pagos {proxima_pagos:%d/%m %H:%M}"
            f", próx. limpieza de sesiones {proxima_limpieza:%d/%m %H:%M}"
        )

    def _bucle(self):
        while not self._detener.is_set():
            try:
                # La limpieza de sesiones va ANTES del chequeo de pagos en
                # cada vuelta: así, si a las 00:00 no hay nada corriendo,
                # los archivos de sesión ya están borrados antes de que
                # pagos pueda volver a arrancar en esta misma vuelta o en
                # cualquiera de las siguientes.
                self._chequear_limpieza_sesiones()
                self._chequear("pagos")
            except Exception as error:
                # Nunca dejar morir el hilo en silencio: se loguea el error
                # y se sigue intentando en el próximo chequeo.
                self._registrar(
                    f"❌ [Automático] Error inesperado en el bucle del programador: "
                    f"{type(error).__name__}: {error}. Se sigue intentando."
                )
                self._registrar(traceback.format_exc(), tambien_consola=False)

            self._detener.wait(timeout=SEGUNDOS_ENTRE_CHEQUEOS)

    # --- limpieza diaria de sesiones --------------------------------------

    def _chequear_limpieza_sesiones(self):
        ahora = datetime.now()
        if ahora < self.proxima_limpieza_sesiones:
            return

        if not self.gestor.hay_lugar():
            # Hay una ejecución en curso (pagos o una manual desde el
            # menú): no se tocan los archivos de sesión mientras algo los
            # puede estar usando. Se reintenta en el próximo chequeo, sin
            # perder el turno ni reprogramar la hora.
            self._avisar_bloqueo(
                "sesiones",
                "hay una ejecución en curso; la limpieza de sesiones espera a que termine",
            )
            return

        self._ultimo_bloqueo_avisado["sesiones"] = None
        self._limpiar_archivos_sesion()
        self.proxima_limpieza_sesiones = self._proxima_medianoche(ahora)

    def _limpiar_archivos_sesion(self):
        from backend.utils.utils import Utilidades

        self._registrar(
            "🔐 [Automático] Limpieza diaria de sesiones (00:00): se fuerza login nuevo "
            "en la próxima corrida."
        )
        Utilidades.limpiar_sesiones_guardadas(motivo="Sesión guardada eliminada (limpieza diaria 00:00)")

    @staticmethod
    def _proxima_medianoche(ahora: datetime) -> datetime:
        """Siempre devuelve la próxima medianoche estrictamente futura
        (si 'ahora' ya son las 00:00:01, da la de mañana, no la de hoy)."""
        return (ahora + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    # --- disparo de pagos ---------------------------------------------

    def _chequear(self, clave: str):
        ahora = datetime.now()
        proxima = self.proxima_pagos
        if ahora < proxima:
            return

        motivo = self.gestor.motivo_bloqueo(clave)
        if motivo is not None or self.gestor.esta_en_ejecucion(clave):
            self._avisar_bloqueo(clave, motivo or f"'{clave}' ya está en ejecución")
            return

        self._ultimo_bloqueo_avisado[clave] = None
        intento_previo = self._intentos[clave]
        sufijo = f" (reintento {intento_previo} de {MAX_REINTENTOS})" if intento_previo else ""
        self._registrar(f"🤖 [Automático] Es hora de {_ETIQUETAS_CLAVE[clave]}{sufijo}. Arrancando...")

        id_ejec = self.gestor.iniciar(
            clave,
            origen="automático",
            on_finalizar=lambda resultado, clave=clave: self._al_finalizar(clave, resultado),
        )
        if id_ejec is None:
            # Muy poco probable (recién chequeamos motivo_bloqueo), pero por
            # las dudas no dejamos el turno colgado: se reintenta solo en el
            # próximo chequeo, sin gastar un intento real.
            self._avisar_bloqueo(clave, "no se pudo iniciar por un motivo inesperado")

    def _al_finalizar(self, clave: str, resultado: str):
        """Callback que dispara GestorProcesos cuando termina una corrida
        automática. Acá se decide si se reintenta o se retoma el horario
        normal."""
        ahora = datetime.now()

        if resultado == "completado":
            if self._intentos[clave]:
                self._registrar(
                    f"✅ [Automático] '{clave}' terminó bien en el reintento {self._intentos[clave]}."
                )
            self._intentos[clave] = 0
            self.proxima_pagos = ahora + INTERVALO_PAGOS
            return

        if resultado == "cancelado":
            self._registrar(
                f"🤖 [Automático] '{clave}' fue cancelado manualmente durante una corrida "
                "automática; no se reintenta solo, se retoma en el horario normal."
            )
            self._intentos[clave] = 0
            self.proxima_pagos = ahora + INTERVALO_PAGOS
            return

        # resultado == "error" (incluye el caso "se cayó sin avisar")
        self._intentos[clave] += 1
        if self._intentos[clave] <= MAX_REINTENTOS:
            espera = BACKOFF_REINTENTOS[self._intentos[clave] - 1]
            proxima = ahora + espera
            self._registrar(
                f"❌ [Automático] '{clave}' falló (intento {self._intentos[clave]} de "
                f"{MAX_REINTENTOS + 1}). Se reintenta a las {proxima:%H:%M}."
            )
            self.proxima_pagos = proxima
        else:
            self._registrar(
                f"❌ [Automático] '{clave}' falló {MAX_REINTENTOS + 1} veces seguidas "
                f"({_ETIQUETAS_CLAVE[clave]}). Se dejan de reintentar por ahora; revisá el log de "
                f"esa ejecución para ver el detalle del error. Se retoma en el horario normal."
            )
            self._intentos[clave] = 0
            self.proxima_pagos = ahora + INTERVALO_PAGOS

    def _avisar_bloqueo(self, clave: str, motivo: str):
        """Loguea el bloqueo solo la primera vez (o si cambió el motivo),
        para no repetir el mismo mensaje en cada chequeo mientras se espera
        lugar."""
        if self._ultimo_bloqueo_avisado.get(clave) == motivo:
            return
        self._ultimo_bloqueo_avisado[clave] = motivo
        self._registrar(
            f"⏳ [Automático] Tocaba disparar '{clave}' pero {motivo}; "
            "se reintenta en breve sin perder el turno."
        )

    # --- log propio del automático, además de la consola en vivo ---------

    def _limpiar_logs_propios_viejos(self):
        try:
            from backend.configs import config
            from backend.utils.utils import Utilidades

            Utilidades.limpiar_carpetas_antiguas_por_fecha(config.CARPETA_LOGS, config.RETENCION_LOGS_DIAS)
        except OSError:
            pass

    def _registrar(self, texto: str, tambien_consola: bool = True):
        if tambien_consola:
            self.consola.imprimir(texto)
        try:
            from backend.configs import config
            from backend.utils.utils import Utilidades

            carpeta = Utilidades.carpeta_logs_del_dia(config.CARPETA_LOGS)
            ruta = os.path.join(carpeta, "automatico.log")
            with open(ruta, "a", encoding="utf-8") as archivo:
                marca = datetime.now().strftime("%H:%M:%S")
                for linea in texto.splitlines() or [""]:
                    archivo.write(f"[{marca}] {linea}\n")
        except OSError:
            pass