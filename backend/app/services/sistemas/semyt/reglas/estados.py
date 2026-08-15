# sistemas/semyt/reglas/estados.py
# -----------------------------------------------------------------------------
# Mapeo de estados y parsing de texto/fecha de la grilla de SEMyT
# (https://ciudad.villamaria.gob.ar/#/actas). Movido desde
# "backend/app/reglas/reglas_semyt.py", separado en dos módulos:
#   - estados.py (este archivo): mapeo de estados, parseo de fecha/texto.
#   - foto.py: lógica de "bajar la foto de una fila" (selectores propios,
#     bastante más pesada y con su propio manejo de popups/modales).
#
# Usado antes solo por backend/ (procesar_actas_semyt.py,
# cargar_actas_semyt.py, actualizar_actas_semyt.py). Ahora también
# disponible para "API-REST Payment" si en algún momento necesita
# interpretar el mismo texto de estado que muestra la grilla (hoy ese
# proyecto no lee estados de SEMyT, solo sube/descarga archivos -- pero
# si mañana lo necesita, ya no hay que reinventar el mapeo).
#
# NOTA: EstadoSemyt (el Enum de destino) vive en el modelo de la webapp
# (backend/app/models.py) porque es un concepto de esa base de datos, no
# de SEMyT en sí. Por eso `MAPA_ESTADO_SEMYT` se arma con una función que
# recibe el módulo `models`, en vez de importarlo directo acá -- así este
# módulo compartido no queda atado a la webapp ni a su ORM.
# -----------------------------------------------------------------------------
import re
import unicodedata
from datetime import datetime
from typing import Optional

COLUMNAS_TABLA = ["nro", "fecha", "dominio", "cuadra", "estado", "vencimiento", "importe", "acciones"]
INDICE_COLUMNA_ESTADO = COLUMNAS_TABLA.index("estado")

SELECTOR_FILAS_RESULTADO = "table tbody tr"

# Estados que NUNCA se procesan (ni se crean, ni se actualizan):
#   - IMPAGA: todavía no fue pagada, no hay nada que cargar.
#   - PAGADA: pago voluntario de estacionamiento (no confundir con "Pagada
#             en Juzgado", ese sí se carga).
#   - EN REVISION: estado transitorio, se espera a que se resuelva.
ESTADOS_IGNORADOS_SEMYT = {"IMPAGA", "PAGADA", "EN REVISION"}

ESTADO_PAGADA_EN_JUZGADO = "PAGADA EN JUZGADO"

# nombre de estado (texto de la grilla) -> nombre del atributo esperado
# en el Enum EstadoSemyt del caller (ver mapa_estado_semyt() más abajo).
_NOMBRES_ESTADO_SEMYT = {
    "NO CARGADA": "no_cargada",
    "VENCIDA": "vencida",
    ESTADO_PAGADA_EN_JUZGADO: "pagada_en_juzgado",
    "RESUELTA EN JUZGADO": "resuelta_en_juzgado",
    "RECHAZADA": "rechazada",
    "ELIMINADA": "eliminada",
}


def mapa_estado_semyt(EstadoSemyt) -> dict:
    """Recibe el Enum EstadoSemyt (de models.py) y arma el dict de mapeo
    texto-de-grilla -> valor de Enum. Reemplaza a la constante fija
    MAPA_ESTADO_SEMYT que antes vivía acoplada a `from .. import models`."""
    return {
        texto: getattr(EstadoSemyt, nombre_atributo)
        for texto, nombre_atributo in _NOMBRES_ESTADO_SEMYT.items()
    }


def pagada_en_juzgado_con_datos(vencimiento_texto: str, importe_texto: str) -> bool:
    """
    True si vencimiento_texto y/o importe_texto tienen contenido real
    (no vacío, no '-'). Se usa para decidir si una fila en estado
    'PAGADA EN JUZGADO' se ignora (como IMPAGA/PAGADA/EN REVISION) o se
    carga/actualiza normal. Estos valores NO se persisten en la DB, solo
    se usan para esta decisión.
    """
    def _tiene_contenido(texto: str) -> bool:
        return bool(texto and texto.strip() not in ("", "-"))
    return _tiene_contenido(vencimiento_texto) or _tiene_contenido(importe_texto)


def normalizar_estado(texto: str) -> str:
    """'EN REVISIÓN' -> 'EN REVISION'. Saca tildes y pasa a mayúscula, para
    no depender de si el sitio muestra el texto con o sin acento."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return sin_tildes.strip().upper()


def parsear_fecha_hora(texto: str) -> Optional[datetime]:
    """'jueves 27/06/24 08:38' o '27/06/2024 08:38' -> datetime. None si no matchea."""
    if not texto:
        return None
    match = re.search(r"(\d{2}/\d{2}/\d{2,4})\s+(\d{2}:\d{2})", texto)
    if not match:
        return None
    fecha_str, hora_str = match.groups()
    formato = "%d/%m/%Y %H:%M" if len(fecha_str.split("/")[-1]) == 4 else "%d/%m/%y %H:%M"
    try:
        return datetime.strptime(f"{fecha_str} {hora_str}", formato)
    except ValueError:
        return None