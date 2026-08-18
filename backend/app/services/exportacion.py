from datetime import datetime, date, timedelta
from typing import Optional, List

from sqlalchemy import or_
from sqlalchemy.orm import Query

from app.models import models
from app.services.duplicados import aplicar_filtro_patente
from app.services.query_helpers import aplicar_rango_fechas

# ---------------------------------------------------------------------------
# Campos disponibles para "Exportar Actas"
# ---------------------------------------------------------------------------

CAMPOS_EXPORTABLES = {
    "juzgado": {
        "tipo": "numero",
        "columna": models.Registro.juzgado,
        "etiqueta": "Juzgado",
    },
    "expediente": {
        "tipo": "texto",
        "columna": models.Registro.expediente,
        "etiqueta": "Nº Expediente",
    },
    "acta": {
        "tipo": "texto",
        "columna": models.Registro.acta,
        "etiqueta": "Nº Acta",
    },
    "causa": {
        "tipo": "texto",
        "columna": models.Registro.causa,
        "etiqueta": "Nº Causa",
    },
    "patente": {
        "tipo": "texto",
        "columna": models.Registro.patente,
        "etiqueta": "Patente",
    },
    "direccion": {
        "tipo": "texto",
        "columna": models.Registro.direccion,
        "etiqueta": "Dirección",
    },
    "estado_sigemi": {
        "tipo": "estado",
        "columna": models.Registro.estado_sigemi,
        "etiqueta": "Estado SIGEMI",
    },
    "motivo_archivo_sigemi": {
        "tipo": "estado",
        "columna": models.Registro.motivo_archivo_sigemi,
        "etiqueta": "Motivo de archivo (SIGEMI)",
    },
    "estado_semyt": {
        "tipo": "estado",
        "columna": models.Registro.estado_semyt,
        "etiqueta": "Estado SEMyT",
    },
    "estado_sigi": {
        "tipo": "estado",
        "columna": models.Registro.estado_sigi,
        "etiqueta": "Estado SIGI",
    },
    "motivo_archivo_sigi": {
        "tipo": "estado",
        "columna": models.Registro.motivo_archivo_sigi,
        "etiqueta": "Motivo de archivo (SIGI)",
    },
    "fecha_hora": {
        "tipo": "fecha",
        "columna": models.Registro.fecha_hora,
        "etiqueta": "Fecha y hora del acta",
    },
    "fecha_cobro_sigi": {
        "tipo": "fecha",
        "columna": models.Registro.fecha_cobro_sigi,
        "etiqueta": "Fecha de cobro SIGI",
    },
    "fecha_cobro_sigemi": {
        "tipo": "fecha",
        "columna": models.Registro.fecha_cobro_sigemi,
        "etiqueta": "Fecha de cobro SIGEMI",
    },
}


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
# aplicar_rango_fechas ahora vive en app/services/query_helpers.py (antes
# había una copia idéntica acá y otra en services/filtros.py -- un solo
# lugar previene que se corrija en uno y se olviden del otro).


def aplicar_filtros_avanzados(
    query,
    filtros: List[dict],
):
    """
    Aplica los filtros libres utilizados por "Exportar Actas".

    Cada filtro tiene:
        {
            "campo": ...,
            "modo": "coincide" | "no_coincide",
            "valor": ...
        }

    Texto:
        coincide     -> contiene, case-insensitive
        no_coincide  -> no contiene

    Patente:
        utiliza la normalización centralizada de duplicados.py.

    Estado:
        comparación exacta.

    Fecha:
        comparación por día completo.

    Número:
        comparación numérica.
    """
    for filtro in filtros:
        campo = filtro.get("campo")
        modo = filtro.get("modo") or "coincide"
        valor = (filtro.get("valor") or "").strip()

        info = CAMPOS_EXPORTABLES.get(campo)

        if not info or not valor:
            continue

        columna = info["columna"]
        tipo = info["tipo"]
        negar = modo == "no_coincide"

        if tipo == "texto":

            if campo == "patente":
                query = aplicar_filtro_patente(
                    query,
                    valor,
                    negar=negar,
                )
                continue

            positiva = columna.ilike(f"%{valor}%")

            condicion = (
                or_(columna.is_(None), ~positiva)
                if negar
                else positiva
            )

        elif tipo == "estado":

            condicion = (
                columna != valor
                if negar
                else columna == valor
            )

        elif tipo == "fecha":

            try:
                dia = datetime.strptime(
                    valor,
                    "%Y-%m-%d",
                )
            except ValueError:
                continue

            siguiente = dia + timedelta(days=1)

            positiva = (
                (columna >= dia)
                & (columna < siguiente)
            )

            condicion = (
                or_(columna.is_(None), ~positiva)
                if negar
                else positiva
            )

        elif tipo == "numero":

            try:
                valor_num = int(valor)
            except ValueError:
                continue

            condicion = (
                columna != valor_num
                if negar
                else columna == valor_num
            )

        else:
            continue

        query = query.filter(condicion)

    return query


# ---------------------------------------------------------------------------
# Consultas para exportación
# ---------------------------------------------------------------------------

def _query_para_exportar(
    db,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
):
    """Arma la query base compartida por contar_para_exportar y
    buscar_para_exportar/iterar_para_exportar -- antes esta construcción
    (mismo filtro, mismo rango de fechas) estaba repetida en las dos
    funciones públicas."""
    query = db.query(models.Registro)
    query = aplicar_filtros_avanzados(query, filtros)
    query = aplicar_rango_fechas(
        query, models.Registro.fecha_hora, fecha_desde, fecha_hasta
    )
    return query


def contar_para_exportar(
    db,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
) -> int:
    """Cuenta cuántas actas cumplen los filtros sin traerlas desde la DB."""
    return _query_para_exportar(db, filtros, fecha_desde, fecha_hasta).count()


def buscar_para_exportar(
    db,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
):
    """
    Trae todas las actas que cumplen los filtros para generar el reporte.

    OJO CON EL VOLUMEN: esto trae TODO el resultado a memoria con .all().
    Con filtros amplios y una tabla de 160k+ filas (ver nota en
    models.Registro) esto puede tardar y consumir bastante RAM. Si en
    algún momento /exportar/txt se pone lento, usar
    iterar_para_exportar() de acá abajo en vez de esta función, y
    generar_reporte_txt_streaming() en vez de generar_reporte_txt().
    """
    return (
        _query_para_exportar(db, filtros, fecha_desde, fecha_hasta)
        .order_by(
            models.Registro.fecha_hora.desc().nullslast(),
            models.Registro.id.desc(),
        )
        .all()
    )


def iterar_para_exportar(
    db,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    tamano_lote: int = 1000,
):
    """
    Versión en streaming de buscar_para_exportar: recorre los resultados
    en lotes de `tamano_lote` en vez de traer todo a memoria de una.
    Usar junto con generar_reporte_txt_streaming() para exportaciones
    grandes sin picos de RAM.

    No aplica order_by por fecha (ORDER BY + yield_per en un rango grande
    puede forzar un sort completo antes de poder iterar) -- si el reporte
    necesita orden estable, ordenar el archivo resultante aparte o pedir
    ORDER BY id, que sí usa el índice primario.
    """
    return (
        _query_para_exportar(db, filtros, fecha_desde, fecha_hasta)
        .order_by(models.Registro.id.asc())
        .yield_per(tamano_lote)
    )


# ---------------------------------------------------------------------------
# Generación del TXT
# ---------------------------------------------------------------------------

COLUMNAS_REPORTE = [
    "JUZGADO",
    "EXPEDIENTE",
    "ACTA_NUMERO",
    "CAUSA_NUMERO",
    "PATENTE",
    "DIRECCION",
    "FECHA_LABRADA",
    "ESTADO_SIGEMI",
    "MOTIVO_ARCHIVO_SIGEMI",
    "ESTADO_SEMYT",
    "ESTADO_SIGI",
    "MOTIVO_ARCHIVO_SIGI",
    "CONSISTENCIA",
    "FECHA_COBRO_SIGI",
    "FECHA_COBRO_SIGEMI",
]


def _fmt_fecha(fecha):
    if not fecha:
        return ""
    return fecha.strftime("%d/%m/%Y %H:%M")


def _limpiar(valor):
    if valor is None:
        return ""
    texto = str(valor).strip()
    if texto in ("None", "null", "nan", "NaN", "-"):
        return ""
    return texto.replace("|", "/").replace("\n", " ").replace("\r", " ")


def _estado(valor):
    return valor.value if hasattr(valor, "value") else (valor or "")


def _fila_reporte(registro) -> str:
    consistente = registro.consistente
    if consistente is True:
        consistencia = "CONSISTENTE"
    elif consistente is False:
        consistencia = "INCONSISTENTE"
    else:
        consistencia = "PENDIENTE"

    fila = [
        registro.juzgado,
        registro.expediente,
        registro.acta,
        registro.causa,
        registro.patente,
        registro.direccion,
        _fmt_fecha(registro.fecha_hora),
        _estado(registro.estado_sigemi),
        _estado(registro.motivo_archivo_sigemi),
        _estado(registro.estado_semyt),
        _estado(registro.estado_sigi),
        _estado(registro.motivo_archivo_sigi),
        consistencia,
        _fmt_fecha(registro.fecha_cobro_sigi),
        _fmt_fecha(registro.fecha_cobro_sigemi),
    ]
    return "|".join(_limpiar(valor) for valor in fila)


def generar_reporte_txt(
    registros,
    filtros: List[dict] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
) -> str:
    """
    Genera el reporte delimitado por |, con una fila por acta, a partir de
    una lista/iterable YA TRAÍDA (ver buscar_para_exportar). `filtros`,
    `fecha_desde`, `fecha_hasta` no se usan acá -- se mantienen en la
    firma por compatibilidad con el caller existente en el router.
    """
    lineas = ["|".join(COLUMNAS_REPORTE)]
    for registro in registros:
        lineas.append(_fila_reporte(registro))
    return "\n".join(lineas) + "\n"


def generar_reporte_txt_streaming(
    db,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    tamano_lote: int = 1000,
):
    """
    Generador línea por línea del reporte, para usar con una respuesta
    streaming de FastAPI (StreamingResponse) en vez de armar el string
    completo en memoria. Pensado para exportaciones grandes.

    Uso en el router (cuando haga falta):
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            generar_reporte_txt_streaming(db, filtros, fecha_desde, fecha_hasta),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
        )
    """
    yield "|".join(COLUMNAS_REPORTE) + "\n"
    for registro in iterar_para_exportar(db, filtros, fecha_desde, fecha_hasta, tamano_lote):
        yield _fila_reporte(registro) + "\n"
        
from app.services.consistencia import debe_archivar_sigi

COLUMNAS_CONSISTENCIA_SIGI = [
    "EXPEDIENTE",
    "NUMERO_ACTA",
    "ESTADO_SEMYT",
    "ESTADO_SIGEMI",
    "ESTADO_SIGI",
    "CONSISTENCIA",
    "DETERMINACION_FINAL",
]


def buscar_para_consistencia_sigi(db):
    """Sólo actas que SIGI ya está siguiendo activamente (estado_sigi
    distinto de 'No Cargada') Y para las que corresponde archivar en SIGI
    (DETERMINACION_FINAL = 'Archivar', ver debe_archivar_sigi). Las que
    no tienen una determinación clara (null) quedan afuera del reporte."""
    registros = (
        db.query(models.Registro)
        .filter(models.Registro.estado_sigi != models.EstadoSigi.no_cargada)
        .order_by(models.Registro.fecha_hora.desc().nullslast(), models.Registro.id.desc())
        .all()
    )
    return [r for r in registros if debe_archivar_sigi(r)]

def _fila_consistencia_sigi(registro) -> str:
    consistente = registro.consistente
    if consistente is True:
        consistencia = "CONSISTENTE"
    elif consistente is False:
        consistencia = "INCONSISTENTE"
    else:
        consistencia = "PENDIENTE"

    determinacion = "Archivar" if debe_archivar_sigi(registro) else ""

    fila = [
        registro.expediente,
        registro.acta,
        _estado(registro.estado_semyt),
        _estado(registro.estado_sigemi),
        _estado(registro.estado_sigi),
        consistencia,
        determinacion,
    ]
    return "|".join(_limpiar(valor) for valor in fila)


def generar_reporte_consistencia_sigi(registros) -> str:
    lineas = ["|".join(COLUMNAS_CONSISTENCIA_SIGI)]
    for registro in registros:
        lineas.append(_fila_consistencia_sigi(registro))
    return "\n".join(lineas) + "\n"
# ---------------------------------------------------------------------------
# Helper común
# ---------------------------------------------------------------------------
def _formatear_expediente(expediente, anio=None) -> str:
    """Devuelve el expediente con formato EXP-AAAA-NUMERO, o un placeholder si no está cargado."""
    if expediente is None or not str(expediente).strip():
        return "Sin Expediente"
    texto = str(expediente).strip()
    if texto.upper().startswith("EXP-"):
        return texto.upper()
    if "-" in texto:
        return f"EXP-{texto}"
    anio = anio or datetime.now().year
    return f"EXP-{anio}-{texto}"


# ---------------------------------------------------------------------------
# Reporte Reescritas SIGI
# ---------------------------------------------------------------------------
COLUMNAS_REESCRITAS_SIGI = [
    "EXPEDIENTE",
    "NUMERO_ACTA",
    "ESTADO_SEMYT",
    "ESTADO_SIGEMI",
    "ESTADO_SIGI",
    "CONSISTENCIA",
    "REESCRITA",
    "PROCEDENCIA",
    "IDX_EXPEDIENTE",
    "DETERMINACION_FINAL",
]


def buscar_para_reescritas_sigi(db):
    """Actas reescritas (mismo vehículo/día/dirección, acta distinta) que
    además están INCONSISTENTES (consistente = False)."""
    return (
        db.query(models.Registro)
        .filter(
            models.Registro.reescrita.is_(True),
            models.Registro.consistente.is_(False),
        )
        .order_by(
            models.Registro.grupo_reescritura.asc(),
            models.Registro.fecha_hora.asc(),
            models.Registro.id.asc(),
        )
        .all()
    )

def _fila_reescrita_sigi(registro, procedencia: str, idx_expediente: str) -> str:
    consistente = registro.consistente
    if consistente is True:
        consistencia = "CONSISTENTE"
    elif consistente is False:
        consistencia = "INCONSISTENTE"
    else:
        consistencia = "PENDIENTE"

    anio = registro.fecha_hora.year if getattr(registro, "fecha_hora", None) else None
    fila = [
        _formatear_expediente(registro.expediente, anio),
        registro.acta,
        _estado(registro.estado_semyt),
        _estado(registro.estado_sigemi),
        _estado(registro.estado_sigi),
        consistencia,
        "True",
        procedencia,
        idx_expediente,
        "Archivar",
    ]
    return "|".join(_limpiar(valor) for valor in fila)


def generar_reporte_reescritas_sigi(registros) -> str:
    lineas = ["|".join(COLUMNAS_REESCRITAS_SIGI)]

    # Agrupamos respetando el orden cronológico en que ya vienen los registros
    grupos = {}
    for registro in registros:
        grupos.setdefault(registro.grupo_reescritura, []).append(registro)

    for grupo in grupos.values():
        original = grupo[0]
        reemplazos = grupo[1:]

        anio_original = original.fecha_hora.year if getattr(original, "fecha_hora", None) else None
        exp_original = _formatear_expediente(original.expediente, anio_original)

        exps_reemplazos = []
        for reemplazo in reemplazos:
            anio_r = reemplazo.fecha_hora.year if getattr(reemplazo, "fecha_hora", None) else None
            exps_reemplazos.append(_formatear_expediente(reemplazo.expediente, anio_r))

        # Fila de la Original -> apunta al/los expediente/s que la reescribieron
        lineas.append(_fila_reescrita_sigi(original, "ORIGINAL", ", ".join(exps_reemplazos)))

        # Filas de los Reemplazos -> apuntan a la Original
        for reemplazo in reemplazos:
            lineas.append(_fila_reescrita_sigi(reemplazo, "REEMPLAZO", exp_original))

    return "\n".join(lineas) + "\n"


# ---------------------------------------------------------------------------
# Reporte Duplicadas SIGI
# ---------------------------------------------------------------------------
COLUMNAS_DUPLICADAS_SIGI = [
    "EXPEDIENTE",
    "NUMERO_ACTA",
    "ESTADO_SEMYT",
    "ESTADO_SIGEMI",
    "ESTADO_SIGI",
    "CONSISTENCIA",
    "DUPLICADA",
    "PROCEDENCIA",
    "IDX_EXPEDIENTE",
    "DETERMINACION_FINAL",
]


def buscar_para_duplicadas_sigi(db):
    """Actas duplicadas (mismo acta en >1 registro) que además están
    INCONSISTENTES (consistente = False) -- las que ya están resueltas
    no necesitan aparecer en el reporte para Ronald."""
    return (
        db.query(models.Registro)
        .filter(
            models.Registro.duplicada.is_(True),
            models.Registro.consistente.is_(False),
        )
        .order_by(
            models.Registro.grupo_duplicada.asc(),
            models.Registro.id.asc(),
        )
        .all()
    )

def _fila_duplicada_sigi(registro, procedencia: str, idx_expediente: str) -> str:
    consistente = registro.consistente
    if consistente is True:
        consistencia = "CONSISTENTE"
    elif consistente is False:
        consistencia = "INCONSISTENTE"
    else:
        consistencia = "PENDIENTE"

    anio = registro.fecha_hora.year if getattr(registro, "fecha_hora", None) else None
    fila = [
        _formatear_expediente(registro.expediente, anio),
        registro.acta,
        _estado(registro.estado_semyt),
        _estado(registro.estado_sigemi),
        _estado(registro.estado_sigi),
        consistencia,
        "True",
        procedencia,
        idx_expediente,
        "Archivar",
    ]
    return "|".join(_limpiar(valor) for valor in fila)


def generar_reporte_duplicadas_sigi(registros) -> str:
    lineas = ["|".join(COLUMNAS_DUPLICADAS_SIGI)]

    grupos = {}
    for registro in registros:
        grupos.setdefault(registro.grupo_duplicada, []).append(registro)

    for grupo in grupos.values():
        original = grupo[0]
        duplicadas = grupo[1:]

        anio_original = original.fecha_hora.year if getattr(original, "fecha_hora", None) else None
        exp_original = _formatear_expediente(original.expediente, anio_original)

        exps_duplicadas = []
        for duplicada in duplicadas:
            anio_d = duplicada.fecha_hora.year if getattr(duplicada, "fecha_hora", None) else None
            exps_duplicadas.append(_formatear_expediente(duplicada.expediente, anio_d))

        lineas.append(_fila_duplicada_sigi(original, "ORIGINAL", ", ".join(exps_duplicadas)))
        for duplicada in duplicadas:
            lineas.append(_fila_duplicada_sigi(duplicada, "DUPLICADA", exp_original))

    return "\n".join(lineas) + "\n"