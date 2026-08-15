from datetime import date
from typing import Optional, List

from sqlalchemy.orm import Session

from .models import models

from app.services.duplicados import anotar_duplicadas
from app.services.filtros import (
    aplicar_filtros_registros,
    aplicar_rango_fechas,
)

def buscar_registros(
    db: Session,
    page: int = 1,
    page_size: int = 10,
    estado_sigemi: Optional[str] = None,
    estado_semyt: Optional[str] = None,
    estado_sigi: Optional[str] = None,
    motivo_archivo: Optional[str] = None,
    juzgado: Optional[int] = None,
    expediente: Optional[str] = None,
    acta: Optional[str] = None,
    causa: Optional[str] = None,
    patente: Optional[str] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    consistencia: Optional[str] = None,
    solo_duplicadas: Optional[bool] = None,
    solo_reescritas: Optional[bool] = None,
):
    query = db.query(models.Registro)

    query = aplicar_filtros_registros(
        query,
        estado_sigemi=estado_sigemi,
        estado_semyt=estado_semyt,
        estado_sigi=estado_sigi,
        motivo_archivo=motivo_archivo,
        juzgado=juzgado,
        expediente=expediente,
        acta=acta,
        causa=causa,
        patente=patente,
        consistencia=consistencia,
        solo_duplicadas=solo_duplicadas,
        solo_reescritas=solo_reescritas,
    )

    query = aplicar_rango_fechas(
        query,
        models.Registro.fecha_hora,
        fecha_desde,
        fecha_hasta,
    )

    query = query.order_by(
        models.Registro.fecha_hora.desc().nullslast(),
        models.Registro.id.desc(),
    )

    total = query.count()

    total_pages = max(
        1,
        (total + page_size - 1) // page_size,
    )

    inicio = (page - 1) * page_size

    resultados = (
        query
        .offset(inicio)
        .limit(page_size)
        .all()
    )

    anotar_duplicadas(db, resultados)

    return resultados, total, total_pages