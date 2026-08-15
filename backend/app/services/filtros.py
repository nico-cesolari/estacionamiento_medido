from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import models
from app.services.duplicados import (
    aplicar_filtro_duplicadas,
    aplicar_filtro_patente,
)


VALORES_MOTIVO_SIGEMI = {
    e.value for e in models.MotivoArchivoSigemi
}

VALORES_MOTIVO_SIGI = {
    e.value for e in models.MotivoArchivoSigi
}


def aplicar_filtros_registros(
    query,
    db: Session,
    *,
    estado_sigemi: Optional[str] = None,
    estado_semyt: Optional[str] = None,
    estado_sigi: Optional[str] = None,
    motivo_archivo: Optional[str] = None,
    juzgado: Optional[int] = None,
    expediente: Optional[str] = None,
    acta: Optional[str] = None,
    causa: Optional[str] = None,
    patente: Optional[str] = None,
    consistencia: Optional[str] = None,
    solo_duplicadas: Optional[bool] = None,
    solo_reescritas: Optional[bool] = None,
):
    if estado_sigemi:
        query = query.filter(
            models.Registro.estado_sigemi == estado_sigemi
        )

    if estado_semyt:
        query = query.filter(
            models.Registro.estado_semyt == estado_semyt
        )

    if estado_sigi:
        query = query.filter(
            models.Registro.estado_sigi == estado_sigi
        )

    if motivo_archivo:
        condiciones = []

        if motivo_archivo in VALORES_MOTIVO_SIGEMI:
            condiciones.append(
                models.Registro.motivo_archivo_sigemi
                == models.MotivoArchivoSigemi(motivo_archivo)
            )

        if motivo_archivo in VALORES_MOTIVO_SIGI:
            condiciones.append(
                models.Registro.motivo_archivo_sigi
                == models.MotivoArchivoSigi(motivo_archivo)
            )

        if condiciones:
            query = query.filter(or_(*condiciones))

    if juzgado:
        query = query.filter(
            models.Registro.juzgado == juzgado
        )

    if expediente:
        query = query.filter(models.Registro.expediente.ilike(f"%{expediente}%"))

    if acta:
        query = query.filter(models.Registro.acta.ilike(f"%{acta}%"))

    if causa:
        query = query.filter(models.Registro.causa.ilike(f"%{causa}%"))

    if patente:
        query = aplicar_filtro_patente(query, patente)

    if solo_duplicadas:
        query = aplicar_filtro_duplicadas(query, db)

    if solo_reescritas:
        query = query.filter(
            models.Registro.reescrita.is_(True)
        )

    if consistencia == "SI":
        query = query.filter(
            models.Registro.consistente.is_(True)
        )
    elif consistencia == "NO":
        query = query.filter(
            models.Registro.consistente.is_(False)
        )
    elif consistencia == "PENDIENTE":
        query = query.filter(
            models.Registro.consistente.is_(None)
        )

    return query