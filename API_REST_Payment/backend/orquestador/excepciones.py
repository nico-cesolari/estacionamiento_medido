# orquestador/excepciones.py
# -----------------------------------------------------------------------------
# Excepción usada para "traducir" una cancelación pedida por el usuario en
# algo que el código Python pueda atrapar con un try/except normal, en
# cualquier punto de la ejecución (ver trabajador.py, que la dispara desde
# un manejador de señal SIGTERM).
# -----------------------------------------------------------------------------


class EjecucionCancelada(Exception):
    """Se lanza cuando el usuario cancela una ejecución desde el panel."""
    pass

class TipoProcesoDesconocidoError(Exception):
    """Se lanza cuando el orquestador recibe un tipo de proceso no soportado."""
    pass

class NoHayActasParaActualizar(Exception):
    """Se lanza cuando SEMyT exporta un Excel sin actas utiles para procesar."""
    pass

class RangoDeFechasInvalido(Exception):
    """Se lanza cuando SEMyT rechaza el rango de fechas pedido (fecha de
    inicio posterior a la de fin). A diferencia de NoHayActasParaActualizar,
    esto normalmente indica un bug en el cálculo de fechas, no algo
    esperado: no debería pasar en uso normal."""
    pass