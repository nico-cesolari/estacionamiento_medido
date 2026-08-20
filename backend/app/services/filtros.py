from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import models
from app.services.duplicados import (
    aplicar_filtro_duplicadas,
    aplicar_filtro_reescritas,
    aplicar_filtro_patente,
)


VALORES_MOTIVO_SIGEMI = {
    e.value for e in models.MotivoArchivoSigemi
}

VALORES_MOTIVO_SIGI = {
    e.value for e in models.MotivoArchivoSigi
}

def _normalizar_numero_con_puntos(texto: str) -> str:
    """
    Interpreta '.' como separador de miles: '1.324' -> '1324',
    '1.234.567' -> '1234567'. La ',' NO se soporta a propósito: si el
    texto la trae se devuelve tal cual (nunca va a matchear un acta real,
    que sólo tiene dígitos), en vez de adivinar qué quiso decir el usuario.
    """
    texto = texto.strip()
    if "," in texto:
        return texto
    return texto.replace(".", "")


def aplicar_filtros_registros(
    query,
    db: Session,
    *,
    estado_sigemi: Optional[str] = None,
    estado_semyt: Optional[str] = None,
    estado_sigi= None,
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
        query = query.filter(models.Registro.estado_sigemi == estado_sigemi)

    if estado_semyt:
        query = query.filter(models.Registro.estado_semyt == estado_semyt)

    if estado_sigi:
        query = query.filter(
            models.Registro.vinculos_sigi.any(models.VinculoSigi.estado_sigi == estado_sigi)
        )

    if motivo_archivo:
        condiciones = []
        if motivo_archivo in VALORES_MOTIVO_SIGEMI:
            condiciones.append(
                models.Registro.motivo_archivo_sigemi == models.MotivoArchivoSigemi(motivo_archivo)
            )
        if motivo_archivo in VALORES_MOTIVO_SIGI:
            condiciones.append(
                models.Registro.vinculos_sigi.any(
                    models.VinculoSigi.motivo_archivo_sigi == models.MotivoArchivoSigi(motivo_archivo)
                )
            )
        if condiciones:
            query = query.filter(or_(*condiciones))

    if juzgado:
        query = query.filter(models.Registro.juzgado == juzgado)

    # --- Coincidencia EXACTA (no "contiene") ---
    if expediente:
        query = query.filter(
            models.Registro.vinculos_sigi.any(
                models.VinculoSigi.expediente.ilike(expediente.strip())
            )
        )

    if acta:
        query = query.filter(models.Registro.acta == _normalizar_numero_con_puntos(acta))

    if causa:
        query = query.filter(models.Registro.causa.ilike(causa.strip()))

    if patente:
        query = aplicar_filtro_patente(query, patente, exacto=True)

    if solo_duplicadas:
        query = aplicar_filtro_duplicadas(query, db)

    if solo_reescritas:
        query = aplicar_filtro_reescritas(query)

    if consistencia == "SI":
        query = query.filter(models.Registro.consistente.is_(True))
    elif consistencia == "NO":
        query = query.filter(models.Registro.consistente.is_(False))
    elif consistencia == "PENDIENTE":
        query = query.filter(models.Registro.consistente.is_(None))

    return query