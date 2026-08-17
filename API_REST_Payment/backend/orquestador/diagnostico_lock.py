# orquestador/diagnostico_lock.py
# -----------------------------------------------------------------------------
# Uso manual: python3 -m backend.orquestador.diagnostico_lock
#
# Lee backend/.ejecucion.lock, te dice si el proceso dueño sigue vivo,
# qué tipo de corrida es y hace cuánto arrancó. Si está vivo y confirmás,
# lo mata (SIGTERM y, si no responde, SIGKILL) para liberar el lock.
# No hace nada automático sin tu confirmación explícita: los datos que
# toca este proyecto son reales (pagos/multas judiciales), así que un
# kill silencioso puede pisar una corrida legítima en curso.
# -----------------------------------------------------------------------------

import os
import signal
import time

from backend.configs import config


def _proceso_vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _leer_lock(ruta: str) -> dict:
    datos = {}
    with open(ruta, "r") as f:
        for linea in f:
            if "=" in linea:
                clave, valor = linea.strip().split("=", 1)
                datos[clave] = valor
    return datos


def main():
    ruta = config.ARCHIVO_LOCK_EJECUCION

    if not os.path.exists(ruta):
        print("✅ No hay lock activo. No hay ninguna ejecución en curso.")
        return

    datos = _leer_lock(ruta)
    pid = int(datos.get("pid", 0)) if datos.get("pid", "").isdigit() else None
    tipo = datos.get("tipo", "desconocido")
    inicio = datos.get("inicio")

    if not pid:
        print(f"⚠ El archivo de lock existe pero no se pudo leer el PID. Contenido: {datos}")
        return

    vivo = _proceso_vivo(pid)

    print(f"Lock: {ruta}")
    print(f"  Tipo de corrida: {tipo}")
    print(f"  PID dueño: {pid}")
    if inicio:
        segundos = time.time() - float(inicio)
        minutos = int(segundos // 60)
        print(f"  Iniciado hace: {minutos} min ({int(segundos)}s)")
    print(f"  ¿Sigue vivo?: {'sí' if vivo else 'no'}")

    if not vivo:
        print("ℹ El proceso ya no existe, pero el archivo de lock quedó en disco "
              "(no debería pasar con flock, pero por las dudas). Podés borrarlo a mano si molesta.")
        return

    print("\n⚠ El proceso está vivo. Si estás seguro de que quedó colgado")
    print("  (y no es una corrida legítima en curso), podés matarlo acá.")
    respuesta = input(f"¿Matar el PID {pid}? Escribí 'si' para confirmar > ").strip().lower()

    if respuesta != "si":
        print("Cancelado. No se tocó nada.")
        return

    print(f"Enviando SIGTERM a {pid}...")
    os.kill(pid, signal.SIGTERM)
    time.sleep(5)

    if _proceso_vivo(pid):
        print(f"No respondió. Enviando SIGKILL a {pid}...")
        os.kill(pid, signal.SIGKILL)
        time.sleep(1)

    if _proceso_vivo(pid):
        print("❌ El proceso sigue vivo. Puede tener permisos distintos (otro usuario) o estar en zombie state.")
    else:
        print("✅ Proceso terminado. El lock debería estar liberado (el kernel lo libera solo al morir el proceso).")


if __name__ == "__main__":
    main()
# python3 -m backend.orquestador.diagnostico_lock