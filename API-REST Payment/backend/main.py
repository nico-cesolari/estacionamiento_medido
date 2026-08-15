# Punto de entrada del programa. Se corre de dos formas:
#
#   python3 -m backend.main
#       Panel interactivo (lo que usa init.command). En cada arranque:
#         1) valida las sesiones de SEMyT y SIGI (rápido si ya estaban
#            activas),
#         2) muestra el panel con las opciones:
#              1 - Actualizar pagos
#            y una opción de "Cancelar" por cada cosa que esté corriendo.
#         Este modo NO tiene opción de Automático ni de "Actualizar todo":
#         es solo para disparar pagos o multas por separado, a mano.
#
#   python3 -m backend.main --servicio
#       Modo servicio: sin panel, sin input(). Valida sesiones (reintenta
#       solo si falla, indefinidamente) y activa el AUTOMÁTICO de una,
#       quedando corriendo en segundo plano hasta recibir SIGTERM/SIGINT.
#       Es el ÚNICO lugar donde corre el automático. Es lo que usa el
#       servicio de launchd (ver instalar_servicio.command); no hace falta
#       correrlo así a mano salvo para probarlo.
#
# Este archivo NO contiene lógica de negocio: arma el arranque de
# multiprocessing (necesario para poder correr procesos en paralelo al del
# panel/servicio) y delega todo en backend/orquestador/app.py.
# -----------------------------------------------------------------------------

import multiprocessing
import sys

from backend.orquestador.app import Aplicacion


def main():
    if "--servicio" in sys.argv:
        Aplicacion().iniciar_servicio()
    else:
        Aplicacion().iniciar()


if __name__ == "__main__":
    # "spawn" en vez del "fork" por defecto de Linux: cada proceso hijo
    # arranca limpio (sin heredar el navegador/Playwright del padre), que
    # es justamente lo que necesitamos para poder correr ejecuciones sin
    # que se pisen entre sí. Además es obligatorio en Windows y es lo que
    # ya usa macOS por default.
    multiprocessing.set_start_method("spawn", force=True)
    main()