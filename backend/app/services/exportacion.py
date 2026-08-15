from datetime import datetime, date, timedelta
from typing import Optional, List

from sqlalchemy import or_
from sqlalchemy.orm import Query

from app.models import models
from app.services.duplicados import aplicar_filtro_patente
from app.services.filtros import aplicar_rango_fechas

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

def aplicar_rango_fechas(
    query,
    columna,
    fecha_desde: Optional[date],
    fecha_hasta: Optional[date],
):
    """
    Filtra una columna DateTime entre fecha_desde y fecha_hasta,
    ambos inclusive.

    fecha_hasta incluye todo ese día.
    """
    if fecha_desde:
        query = query.filter(
            columna >= datetime.combine(
                fecha_desde,
                datetime.min.time(),
            )
        )

    if fecha_hasta:
        siguiente = (
            datetime.combine(fecha_hasta, datetime.min.time())
            + timedelta(days=1)
        )
        query = query.filter(columna < siguiente)

    return query


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

def contar_para_exportar(
    db,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
) -> int:
    """
    Cuenta cuántas actas cumplen los filtros sin traerlas desde la DB.
    """
    query = db.query(models.Registro)

    query = aplicar_filtros_avanzados(
        query,
        filtros,
    )

    query = aplicar_rango_fechas(
        query,
        models.Registro.fecha_hora,
        fecha_desde,
        fecha_hasta,
    )

    return query.count()


def buscar_para_exportar(
    db,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
):
    """
    Trae todas las actas que cumplen los filtros para generar el reporte.
    """
    query = db.query(models.Registro)

    query = aplicar_filtros_avanzados(
        query,
        filtros,
    )

    query = aplicar_rango_fechas(
        query,
        models.Registro.fecha_hora,
        fecha_desde,
        fecha_hasta,
    )

    return (
        query
        .order_by(
            models.Registro.fecha_hora.desc().nullslast(),
            models.Registro.id.desc(),
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Generación del TXT
# ---------------------------------------------------------------------------

def _fmt_fecha(fecha):
    if not fecha:
        return ""

    return fecha.strftime("%d/%m/%Y %H:%M")


def generar_reporte_txt(
    registros,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
) -> str:
    """
    Genera el reporte delimitado por |, con una fila por acta.
    """
    columnas = [
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

    def limpiar(valor):
        if valor is None:
            return ""

        texto = str(valor).strip()

        if texto in ("None", "null", "nan", "NaN", "-"):
            return ""

        return (
            texto
            .replace("|", "/")
            .replace("\n", " ")
            .replace("\r", " ")
        )

    def estado(valor):
        return (
            valor.value
            if hasattr(valor, "value")
            else (valor or "")
        )

    lineas = ["|".join(columnas)]

    for registro in registros:

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
            estado(registro.estado_sigemi),
            estado(registro.motivo_archivo_sigemi),
            estado(registro.estado_semyt),
            estado(registro.estado_sigi),
            estado(registro.motivo_archivo_sigi),
            consistencia,
            _fmt_fecha(registro.fecha_cobro_sigi),
            _fmt_fecha(registro.fecha_cobro_sigemi),
        ]

        lineas.append(
            "|".join(limpiar(valor) for valor in fila)
        )

    return "\n".join(lineas) + "\n"