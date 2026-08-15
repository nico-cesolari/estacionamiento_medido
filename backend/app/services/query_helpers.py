# app/services/query_helpers.py
"""
Utilidades de query reutilizadas por más de un service. Antes
aplicar_rango_fechas estaba copiada tal cual en filtros.py y en
exportacion.py -- un solo lugar ahora.
"""
from datetime import date, datetime, timedelta
from typing import Optional


def aplicar_rango_fechas(query, columna, fecha_desde: Optional[date], fecha_hasta: Optional[date]):
    """Filtra una columna DateTime entre fecha_desde y fecha_hasta, ambos
    inclusive. fecha_hasta incluye todo ese día (comparación < día siguiente,
    no <=, para no depender de la resolución de la columna)."""
    if fecha_desde:
        query = query.filter(
            columna >= datetime.combine(fecha_desde, datetime.min.time())
        )
    if fecha_hasta:
        siguiente = datetime.combine(fecha_hasta, datetime.min.time()) + timedelta(days=1)
        query = query.filter(columna < siguiente)
    return query