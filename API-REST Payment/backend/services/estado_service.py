# services/estado_service.py
# -----------------------------------------------------------------------------
# Responsable únicamente de leer/escribir el archivo de estado en disco.
# No sabe nada de fechas de negocio, ni de Playwright, ni de Excel.
# (Single Responsibility Principle)
# -----------------------------------------------------------------------------

import json
import os

from backend.models.estado import EstadoDescarga


class EstadoService:
    def __init__(self, ruta_archivo: str):
        self.ruta_archivo = ruta_archivo

    def leer(self) -> EstadoDescarga:
        if not os.path.exists(self.ruta_archivo):
            return EstadoDescarga(ultima_fecha_procesada=None)

        with open(self.ruta_archivo, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)

        return EstadoDescarga(ultima_fecha_procesada=datos.get("ultima_fecha_procesada"))

    def guardar(self, fecha_texto: str):
        """Guarda la próxima fecha desde donde consultar multas vencidas.

        Esta escritura solo debería ocurrir en una corrida automática que
        haya descargado actas y las haya subido al SIGI correctamente.
        """
        estado = EstadoDescarga(ultima_fecha_procesada=fecha_texto)
        with open(self.ruta_archivo, "w", encoding="utf-8") as archivo:
            json.dump(
                {"ultima_fecha_procesada": estado.ultima_fecha_procesada},
                archivo,
                ensure_ascii=False,
                indent=2,
            )
