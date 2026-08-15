# services/excel_service.py
# -----------------------------------------------------------------------------
# Responsable únicamente de leer archivos Excel y extraer información de
# ellos. No sabe nada de Playwright, ni de estado, ni de fechas de negocio
# más allá de calcular la "próxima fecha" a partir de los datos.
# -----------------------------------------------------------------------------

import pandas as pd
from datetime import timedelta
from common.normalizacion.fechas import FORMATO_EXCEL_FECHA_HORA as FORMATO_FECHA_EXCEL

NOMBRE_COLUMNA_FECHA = "fecha hora"

class ExcelService:
    def tiene_actas_para_actualizar(self, ruta_excel: str) -> bool:
        tabla = pd.read_excel(ruta_excel, dtype=str)
        if tabla.empty:
            return False

        try:
            columna_fecha = self._buscar_columna_fecha(tabla)
            self._extraer_fechas_validas(tabla, columna_fecha)
            return True
        except ValueError:
            return False

    def obtener_proxima_fecha_desde(self, ruta_excel: str):
        tabla = pd.read_excel(ruta_excel, dtype=str)

        columna_fecha = self._buscar_columna_fecha(tabla)
        fechas_validas = self._extraer_fechas_validas(tabla, columna_fecha)

        fecha_mas_reciente = fechas_validas.max()
        return (fecha_mas_reciente + timedelta(days=1)).normalize()

    def obtener_rango_fechas_actas(self, ruta_excel: str):
        """Devuelve la primera y la última fecha real de acta del Excel.

        Se usa para nombrar el archivo descargado con información útil:
        no el rango consultado, sino lo que efectivamente vino en el Excel.
        """
        tabla = pd.read_excel(ruta_excel, dtype=str)
        columna_fecha = self._buscar_columna_fecha(tabla)
        fechas_validas = self._extraer_fechas_validas(tabla, columna_fecha)
        return fechas_validas.min(), fechas_validas.max()
    
    def validar_que_haya_actas(self, ruta_excel: str):
        tabla = pd.read_excel(ruta_excel, dtype=str)
        columna = self._buscar_columna_fecha(tabla)
        self._extraer_fechas_validas(tabla, columna)
        
    def _buscar_columna_fecha(self, tabla: pd.DataFrame) -> str:
        for columna in tabla.columns:
            if NOMBRE_COLUMNA_FECHA in columna.strip().lower():
                return columna
        raise ValueError(
            f"No se encontró la columna 'Fecha Hora' en el Excel. "
            f"Columnas disponibles: {list(tabla.columns)}"
        )

    def _extraer_fechas_validas(self, tabla: pd.DataFrame, columna_fecha: str):
        fechas = pd.to_datetime(
            tabla[columna_fecha].str.strip(),
            format=FORMATO_FECHA_EXCEL,
            errors="coerce",
        )
        fechas_validas = fechas.dropna()

        if fechas_validas.empty:
            raise ValueError("No se encontraron fechas válidas en el Excel descargado.")

        return fechas_validas
