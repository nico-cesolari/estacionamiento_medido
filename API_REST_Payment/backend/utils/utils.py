# utils/utils.py
# -----------------------------------------------------------------------------
# Utilidades chicas y genéricas usadas por varios módulos: manejo de
# carpetas por día, borrado seguro de archivos de un solo uso, limpieza de
# logs viejos y logs temporales huérfanos.
# -----------------------------------------------------------------------------

import os
import shutil
from datetime import datetime, timedelta


class Utilidades:

    @staticmethod
    def asegurar_carpeta(ruta_archivo):
        carpeta = os.path.dirname(ruta_archivo)
        if carpeta and not os.path.exists(carpeta):
            os.makedirs(carpeta)

    @staticmethod
    def eliminar_archivo_si_existe(ruta_archivo: str, motivo: str = ""):
        """Borra un archivo que ya cumplió su función y no debe persistir.
        Punto único usado por los pasos que descargan archivos "de un solo
        uso" (TXT/Excel intermedios), para no repetir el mismo try/except
        en cada uno de ellos."""
        try:
            if ruta_archivo and os.path.exists(ruta_archivo):
                os.remove(ruta_archivo)
                print(f"🧹 {motivo}: {Utilidades.ruta_para_log(ruta_archivo)}" if motivo else f"🧹 Archivo eliminado: {Utilidades.ruta_para_log(ruta_archivo)}")
        except OSError as error:
            print(f"❌ No se pudo eliminar {ruta_archivo}: {error}")

    @staticmethod
    def ruta_para_log(ruta: str) -> str:
        partes = ruta.split(os.sep)

        if "backend" in partes:
            return os.sep.join(partes[partes.index("backend"):])
        if "datos" in partes:
            return os.sep.join(partes[partes.index("datos"):])

        return os.path.basename(ruta)

    @staticmethod
    def carpeta_del_dia(carpeta_raiz: str, fecha: "datetime | None" = None) -> str:
        """Devuelve (creándola si hace falta) la subcarpeta del DÍA dentro
        de 'carpeta_raiz' (carpeta_raiz/AAAA-MM-DD/).

        Genérico a propósito: lo usan tanto los logs (datos/logs/AAAA-MM-DD/)
        como las descargas de pagos (datos/descargas/pagos/AAAA-MM-DD/), así
        todo lo que se genera en una corrida de "pagos" un día puntual
        queda junto en una sola carpeta con esa fecha.
        """
        fecha = fecha or datetime.now()
        carpeta = os.path.join(carpeta_raiz, fecha.strftime("%Y-%m-%d"))
        os.makedirs(carpeta, exist_ok=True)
        return carpeta

    @staticmethod
    def carpeta_logs_del_dia(carpeta_logs_raiz: str, fecha: "datetime | None" = None) -> str:
        """Alias histórico de carpeta_del_dia, específico para logs."""
        return Utilidades.carpeta_del_dia(carpeta_logs_raiz, fecha)

    @staticmethod
    def limpiar_carpetas_antiguas_por_fecha(carpeta_logs_raiz: str, dias: int):
        """Con los logs organizados por día (datos/logs/AAAA-MM-DD/...),
        borra carpetas COMPLETAS de días más viejos que 'dias'.

        Por seguridad, solo toca subcarpetas cuyo nombre matchea exactamente
        el formato AAAA-MM-DD: si hay otra cosa ahí adentro no la toca.
        Pensado para llamarse al arrancar cada ejecución: así, con el
        automático corriendo indefinidamente, la carpeta de logs no crece
        para siempre. Si algo falla al borrar una carpeta puntual (permisos,
        etc.), no corta la ejecución: solo lo avisa por consola y sigue.
        """
        if not os.path.isdir(carpeta_logs_raiz) or dias <= 0:
            return

        limite = (datetime.now() - timedelta(days=dias)).date()
        try:
            nombres = os.listdir(carpeta_logs_raiz)
        except OSError:
            return

        for nombre in nombres:
            ruta = os.path.join(carpeta_logs_raiz, nombre)
            if not os.path.isdir(ruta):
                continue
            try:
                fecha_carpeta = datetime.strptime(nombre, "%Y-%m-%d").date()
            except ValueError:
                continue  # no es una carpeta de fecha: no tocar
            if fecha_carpeta < limite:
                try:
                    shutil.rmtree(ruta)
                except OSError as error:
                    print(f"❌ No se pudo borrar la carpeta de logs vieja {ruta}: {error}")

    @staticmethod
    def limpiar_logs_temporales_huerfanos(carpeta_logs_raiz: str):
        """Barre datos/logs/AAAA-MM-DD/*.log.tmp y los borra.

        Estos ".tmp" son logs de una corrida que todavía no había
        terminado de decidir si se conservaba o no (ver
        orquestador/trabajador.py: el log se escribe primero en un ".tmp"
        y recién al final se renombra al ".log" definitivo, o se borra).
        Si el proceso se cae de una forma muy abrupta (kill -9, corte de
        luz, etc.) ese ".tmp" puede quedar huérfano en disco para
        siempre. Se llama una vez al arrancar el panel/servicio para
        limpiar lo que haya quedado de una corrida anterior interrumpida
        así.
        """
        if not os.path.isdir(carpeta_logs_raiz):
            return
        try:
            nombres_carpetas = os.listdir(carpeta_logs_raiz)
        except OSError:
            return

        for nombre_carpeta in nombres_carpetas:
            ruta_carpeta = os.path.join(carpeta_logs_raiz, nombre_carpeta)
            if not os.path.isdir(ruta_carpeta):
                continue
            try:
                archivos = os.listdir(ruta_carpeta)
            except OSError:
                continue
            for nombre_archivo in archivos:
                if nombre_archivo.endswith(".log.tmp"):
                    try:
                        os.remove(os.path.join(ruta_carpeta, nombre_archivo))
                    except OSError:
                        pass

    @staticmethod
    def limpiar_sesiones_guardadas(motivo: str = "") -> None:
        """Borra los 3 archivos de sesión guardada (sesion_general.json,
        sesion_semyt.json, sesion_sigi.json) para forzar un login nuevo en
        la próxima corrida.

        Punto único usado tanto por la limpieza diaria programada como por
        la auto-recuperación ante error (ver orquestador/trabajador.py):
        antes esto se resolvía a mano, borrando esos archivos por fuera
        del programa cada vez que una sesión se quedaba "colgada" (con
        token vencido pero sin ningún error visible hasta que algo
        dependiente de esa sesión fallaba). Ahora se dispara solo.
        """
        from backend.configs import sesiones

        for ruta in sesiones.TODAS:
            Utilidades.eliminar_archivo_si_existe(ruta, motivo=motivo or "Sesión guardada eliminada")
