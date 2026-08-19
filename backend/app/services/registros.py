# app/services/registros.py
"""
Capa de negocio para el router /api/registros. Reemplaza a crud.py:
filtros -> services/filtros.py, duplicadas/reescritas -> services/duplicados.py,
cambios de estado -> services/estados.py, exportación -> services/exportacion.py.
Este módulo sólo orquesta esas piezas para lo que el router necesita.
"""
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models import models
from app.services.duplicados import anotar_duplicadas
from app.services.estados import aplicar_cambios_estado
from app.services.filtros import aplicar_filtros_registros
from app.services.query_helpers import aplicar_rango_fechas
from app.services.duplicados import anotar_duplicadas, anotar_info_relaciones
from sqlalchemy.orm import selectinload
from app.services.sigi_vinculos import anotar_info_sigi, actualizar_vinculo

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
    query = db.query(models.Registro).options(selectinload(models.Registro.vinculos_sigi))

    query = aplicar_filtros_registros(
        query, db,
        estado_sigemi=estado_sigemi, estado_semyt=estado_semyt, estado_sigi=estado_sigi,
        motivo_archivo=motivo_archivo, juzgado=juzgado, expediente=expediente,
        acta=acta, causa=causa, patente=patente, consistencia=consistencia,
        solo_duplicadas=solo_duplicadas, solo_reescritas=solo_reescritas,
    )
    query = aplicar_rango_fechas(query, models.Registro.fecha_hora, fecha_desde, fecha_hasta)
    query = query.order_by(models.Registro.fecha_hora.desc().nullslast(), models.Registro.id.desc())

    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    resultados = query.offset((page - 1) * page_size).limit(page_size).all()

    anotar_duplicadas(db, resultados)
    anotar_info_relaciones(db, resultados)
    anotar_info_sigi(resultados)
    return resultados, total, total_pages


def obtener_registro(db: Session, registro_id: int) -> Optional["models.Registro"]:
    return db.query(models.Registro).filter(models.Registro.id == registro_id).first()

def actualizar_vinculo_sigi(db: Session, registro_id: int, vinculo_id: int, cambios: dict):
    """PATCH puntual de UN vínculo SIGI (no del registro entero)."""
    registro = obtener_registro(db, registro_id)
    if registro is None:
        return None
    vinculo = next((v for v in registro.vinculos_sigi if v.id == vinculo_id), None)
    if vinculo is None:
        return None
    actualizar_vinculo(
        db, vinculo,
        estado_sigi=cambios.get("estado_sigi"),
        motivo_archivo_sigi=cambios.get("motivo_archivo_sigi"),
    )
    db.commit()
    db.refresh(registro)
    anotar_duplicadas(db, [registro])
    anotar_info_relaciones(db, [registro])
    anotar_info_sigi([registro])
    return registro

def actualizar_estados(db: Session, registro_id: int, cambios: dict) -> Optional["models.Registro"]:
    """PATCH de un registro puntual: usa el mismo camino que las cargas
    masivas (aplicar_cambios_estado), así historial y `consistente`
    quedan siempre bien, sin importar de dónde vino el cambio."""
    registro = obtener_registro(db, registro_id)
    if registro is None:
        return None
    aplicar_cambios_estado(db, registro, cambios)
    db.commit()
    db.refresh(registro)
    anotar_duplicadas(db, [registro])
    return registro