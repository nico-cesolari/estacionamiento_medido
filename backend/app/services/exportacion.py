from datetime import datetime, date, timedelta
from typing import Optional, List

from sqlalchemy import or_, and_
from sqlalchemy.orm import Query

from app.models import models
from app.services.duplicados import aplicar_filtro_patente
from app.services.query_helpers import aplicar_rango_fechas
from sqlalchemy.orm import selectinload
from app.services.sigi_vinculos import _clave_orden_expediente
from app.services.consistencia import debe_archivar_sigi_vinculo

ORDEN_ESTADO_SIGI = {estado: idx for idx, estado in enumerate(models.EstadoSigi)}
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
        "columna": None,
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
        "tipo": "estado_sigi_vinculo",
        "columna": None,
        "etiqueta": "Estado SIGI",
    },
    "motivo_archivo_sigi": {
        "tipo": "estado_sigi_vinculo",
        "columna": None,
        "etiqueta": "Motivo de archivo (SIGI)",
    },
    "fecha_hora": {
        "tipo": "fecha",
        "columna": models.Registro.fecha_hora,
        "etiqueta": "Fecha y hora del acta",
    },
    "fecha_cobro_sigi": {
        "tipo": "fecha",
        "columna": None,
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
def aplicar_filtros_avanzados(query, filtros: List[dict]):
    """
    (docstring igual)
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
            if campo == "expediente":
                positiva = models.Registro.vinculos_sigi.any(
                    models.VinculoSigi.expediente.ilike(f"%{valor}%")
                )
                condicion = ~positiva if negar else positiva
                query = query.filter(condicion)
                continue

            if campo == "patente":
                query = aplicar_filtro_patente(query, valor, negar=negar)
                continue

            positiva = columna.ilike(f"%{valor}%")
            condicion = or_(columna.is_(None), ~positiva) if negar else positiva

        elif tipo == "estado":
            condicion = (columna != valor) if negar else (columna == valor)

        elif tipo == "estado_sigi_vinculo":
            atributo = "estado_sigi" if campo == "estado_sigi" else "motivo_archivo_sigi"
            condicion_vinculo = getattr(models.VinculoSigi, atributo) == valor
            positiva = models.Registro.vinculos_sigi.any(condicion_vinculo)
            condicion = ~positiva if negar else positiva

        elif tipo == "fecha":
            try:
                dia = datetime.strptime(valor, "%Y-%m-%d")
            except ValueError:
                continue
            siguiente = dia + timedelta(days=1)

            if campo == "fecha_cobro_sigi":
                condicion_vinculo = and_(
                    models.VinculoSigi.fecha_cobro_sigi >= dia,
                    models.VinculoSigi.fecha_cobro_sigi < siguiente,
                )
                positiva = models.Registro.vinculos_sigi.any(condicion_vinculo)
            else:
                positiva = (columna >= dia) & (columna < siguiente)

            condicion = or_(columna.is_(None), ~positiva) if negar and columna is not None else (
                ~positiva if negar else positiva
            )

        elif tipo == "numero":
            try:
                valor_num = int(valor)
            except ValueError:
                continue
            condicion = (columna != valor_num) if negar else (columna == valor_num)

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
    """Devuelve pares (registro, vinculo) -- uno por CADA vínculo SIGI
    activo (distinto de 'No Cargada') para el que corresponde archivar
    (ver debe_archivar_sigi_vinculo, evaluado por vínculo individual)."""
    registros = (
        db.query(models.Registro)
        .filter(
            models.Registro.vinculos_sigi.any(
                models.VinculoSigi.estado_sigi != models.EstadoSigi.no_cargada
            )
        )
        .options(selectinload(models.Registro.vinculos_sigi))
        .order_by(models.Registro.fecha_hora.desc().nullslast(), models.Registro.id.desc())
        .all()
    )
    pares = []
    for r in registros:
        for v in r.vinculos_sigi:
            if v.estado_sigi != models.EstadoSigi.no_cargada and debe_archivar_sigi_vinculo(r, v):
                pares.append((r, v))
    return pares

def _fila_consistencia_sigi(registro, vinculo) -> str:
    consistencia = (
        "INCONSISTENTE" if vinculo.consistente is False
        else "CONSISTENTE" if vinculo.consistente is True
        else "PENDIENTE"
    )
    fila = [
        vinculo.expediente,
        vinculo.acta_sigi or registro.acta,
        _estado(registro.estado_semyt),
        _estado(registro.estado_sigemi),
        _estado(vinculo.estado_sigi),
        consistencia,
        "Archivar",
    ]
    return "|".join(_limpiar(v) for v in fila)
 
 
def generar_reporte_consistencia_sigi(pares) -> str:
    lineas = ["|".join(COLUMNAS_CONSISTENCIA_SIGI)]
    for registro, vinculo in pares:
        lineas.append(_fila_consistencia_sigi(registro, vinculo))
    return "\n".join(lineas) + "\n"
 
# ---------------------------------------------------------------------------
# Helper común
# ---------------------------------------------------------------------------
 
def _registros_con_vinculo_origen(db, origen: str):
    """Registros que tienen AL MENOS un vínculo SIGI del origen pedido
    ('duplicada' o 'reescrita'). Trae TODOS los vínculos de cada registro
    (no sólo el del origen buscado): el reporte necesita ver el grupo
    completo para poder numerar IDX_EXPEDIENTE 1, 2, 3... sin importar
    cuántos expedientes tenga."""
    return (
        db.query(models.Registro)
        .filter(models.Registro.vinculos_sigi.any(models.VinculoSigi.origen == origen))
        .options(selectinload(models.Registro.vinculos_sigi))
        .order_by(models.Registro.acta.asc(), models.Registro.id.asc())
        .all()
    )
 
 
def buscar_para_reescritas_sigi(db):
    return _registros_con_vinculo_origen(db, "reescrita")
 
 
def buscar_para_duplicadas_sigi(db):
    return _registros_con_vinculo_origen(db, "duplicada")
 
 
COLUMNAS_REESCRITAS_SIGI = [
    "EXPEDIENTE", "NUMERO_ACTA", "ESTADO_SEMYT", "ESTADO_SIGEMI", "ESTADO_SIGI",
    "CONSISTENCIA", "REESCRITA", "IDX_EXPEDIENTE", "IDX_ESTADO_SIGI", "DETERMINACION_FINAL",
]
 
COLUMNAS_DUPLICADAS_SIGI = [
    "EXPEDIENTE", "NUMERO_ACTA", "ESTADO_SEMYT", "ESTADO_SIGEMI", "ESTADO_SIGI",
    "CONSISTENCIA", "DUPLICADA", "IDX_EXPEDIENTE", "IDX_ESTADO_SIGI", "DETERMINACION_FINAL",
]
 
 
def _fila_vinculo_sigi(registro, vinculo, otro_expediente: str, otro_estado_sigi: str) -> str:
    consistencia = (
        "INCONSISTENTE" if vinculo.consistente is False
        else "CONSISTENTE" if vinculo.consistente is True
        else "PENDIENTE"
    )
    numero_acta = vinculo.acta_sigi or registro.acta
 
    fila = [
        vinculo.expediente,
        numero_acta,
        _estado(registro.estado_semyt),
        _estado(registro.estado_sigemi),
        _estado(vinculo.estado_sigi),
        consistencia,
        "True",  # REESCRITA o DUPLICADA, según flag_nombre -- el valor va siempre en la misma columna
        otro_expediente,     # IDX_EXPEDIENTE: el/los OTRO(S) expediente(s) vinculado(s) a la misma acta
        otro_estado_sigi,    # IDX_ESTADO_SIGI: estado SIGI de ese/esos otro(s) expediente(s)
        "Archivar",
    ]
    return "|".join(_limpiar(v) for v in fila)
 
 
def _generar_reporte_vinculos_sigi(registros, columnas):
    """
    Una fila por VÍNCULO (no por registro): si un acta tiene varios
    expedientes en SIGI, aparece una fila por cada vínculo que pasa los
    filtros. IDX_EXPEDIENTE / IDX_ESTADO_SIGI muestran el/los OTRO(S)
    expediente(s) de la misma acta (y su estado SIGI) -- no el propio,
    que ya está en las columnas EXPEDIENTE/ESTADO_SIGI. Si un vínculo no
    tiene ningún otro expediente vinculado en la misma acta, no hay nada
    que archivar/reescribir en relación a otro, así que esa fila se
    saltea directamente.
 
    Filtros aplicados fila por fila:
      - expediente vacío -> se saltea (no debería pasar, expediente es
        NOT NULL en vinculos_sigi, pero se deja como defensa).
      - estado_sigi == 'No Cargada' -> se saltea (pedido explícito).
      - consistente distinto de False -> se saltea (sólo interesa lo
        inconsistente, que es lo que hay que archivar).
      - sin otro expediente vinculado en la misma acta -> se saltea.
 
    Devuelve None si no quedó ninguna fila de datos (sólo encabezado):
    en ese caso no hay nada para descargar.
    """
    lineas = ["|".join(columnas)]
    for registro in registros:
        vinculos_ordenados = sorted(
            registro.vinculos_sigi, key=lambda v: _clave_orden_expediente(v.expediente)
        )
        for vinculo in vinculos_ordenados:
            if not vinculo.expediente:
                continue
            if vinculo.estado_sigi == models.EstadoSigi.no_cargada:
                continue
            if vinculo.consistente is not False:
                continue
 
            otros = [
                v for v in vinculos_ordenados
                if v is not vinculo and v.expediente
            ]
            if not otros:
                continue
 
            otro_expediente = ", ".join(v.expediente for v in otros)
            otro_estado_sigi = ", ".join(_estado(v.estado_sigi) for v in otros)
 
            lineas.append(_fila_vinculo_sigi(registro, vinculo, otro_expediente, otro_estado_sigi))
 
    if len(lineas) == 1:
        return None
 
    return "\n".join(lineas) + "\n"
 
 
def generar_reporte_reescritas_sigi(registros):
    return _generar_reporte_vinculos_sigi(registros, COLUMNAS_REESCRITAS_SIGI)
 
 
def generar_reporte_duplicadas_sigi(registros):
    return _generar_reporte_vinculos_sigi(registros, COLUMNAS_DUPLICADAS_SIGI)