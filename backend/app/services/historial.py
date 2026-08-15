from typing import List
from sqlalchemy.orm import Session
from app.models import models

def historial_de_registro(
    db: Session,
    registro_id: int,
):
    """
    Devuelve el historial completo de un registro,
    ordenado cronológicamente.
    """
    return (
        db.query(models.HistorialEstado)
        .filter(
            models.HistorialEstado.registro_id == registro_id
        )
        .order_by(
            models.HistorialEstado.fecha_inicio.asc(),
            models.HistorialEstado.id.asc(),
        )
        .all()
    )

def historial_de_registros(
    db: Session,
    registro_ids: List[int],
):
    """
    Devuelve el historial de varios registros en una sola consulta.
    """
    if not registro_ids:
        return []

    return (
        db.query(models.HistorialEstado)
        .filter(
            models.HistorialEstado.registro_id.in_(registro_ids)
        )
        .order_by(
            models.HistorialEstado.registro_id.asc(),
            models.HistorialEstado.fecha_inicio.asc(),
            models.HistorialEstado.id.asc(),
        )
        .all()
    )