# orquestador/consola.py
# -----------------------------------------------------------------------------
# El panel necesita poder:
#   1) mostrar en vivo los logs de procesos que corren en segundo plano, y
#   2) al mismo tiempo, seguir esperando que el usuario elija una opción,
# sin que una cosa rompa visualmente a la otra (líneas que se cortan a la
# mitad, prompt duplicado, etc.).
#
# Esta clase centraliza toda impresión por consola detrás de un lock, y
# sabe "borrar" el prompt actual antes de imprimir un mensaje asíncrono
# para volver a mostrarlo después, prolijo.
# -----------------------------------------------------------------------------

import sys
import threading


class Console:
    def __init__(self):
        self._lock = threading.Lock()
        self._prompt_actual = ""
        self._volver_pendiente = False

    def imprimir(self, texto: str):
        with self._lock:
            if self._prompt_actual and sys.stdout.isatty():
                sys.stdout.write("\r" + " " * (len(self._prompt_actual) + 1) + "\r")

            print(texto)

            if self._prompt_actual and sys.stdout.isatty():
                sys.stdout.write(self._prompt_actual)
                sys.stdout.flush()

    def preguntar(self, prompt: str) -> str:
        with self._lock:
            self._volver_pendiente = False
            self._prompt_actual = prompt
            sys.stdout.write(prompt)
            sys.stdout.flush()
        try:
            respuesta = input()
        finally:
            with self._lock:
                self._prompt_actual = ""
        return respuesta

    def pedir_volver_si_esta_esperando(self):
        with self._lock:
            if not self._prompt_actual:
                return
            sys.stdout.write("\r" + " " * (len(self._prompt_actual) + 1) + "\r")
            self._prompt_actual = "Volver > "
            self._volver_pendiente = True
            sys.stdout.write(self._prompt_actual)
            sys.stdout.flush()

    def consumir_volver_pendiente(self) -> bool:
        with self._lock:
            volver_pendiente = self._volver_pendiente
            self._volver_pendiente = False
        return volver_pendiente
