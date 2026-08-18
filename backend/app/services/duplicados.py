import re
from typing import List

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import models
from app.services.sistemas.comun.texto import limpiar_patente as _normalizar_patente_texto

# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

def _columna_patente_normalizada():
    """Misma normalización que _normalizar_patente_texto, pero en SQL."""
    col = models.Registro.patente

    for caracter in ("-", " ", ".", "_"):
        col = func.replace(col, caracter, "")

    return func.upper(col)


def _normalizar_direccion_texto(valor: str) -> str:
    """Normaliza una dirección para comparaciones."""
    return re.sub(r"\s+", " ", (valor or "").strip()).upper()


def _columna_direccion_normalizada():
    """Misma normalización que _normalizar_direccion_texto, pero en SQL."""
    return func.upper(func.trim(models.Registro.direccion))


# ---------------------------------------------------------------------------
# Actas duplicadas
#
# Una acta es duplicada cuando el mismo número de acta aparece en más
# de un Registro.
#
# No es un estado persistido ni un valor de ningún Enum.
# Se calcula al vuelo.
# ---------------------------------------------------------------------------

def _query_actas_duplicadas(db: Session):
    """
    Devuelve una query con los valores de `acta` que aparecen
    en más de una fila.
    """
    return (
        db.query(models.Registro.acta)
        .filter(models.Registro.acta.isnot(None))
        .group_by(models.Registro.acta)
        .having(func.count(models.Registro.id) > 1)
    )


def anotar_duplicadas(
    db: Session,
    registros: List["models.Registro"],
):
    """
    Agrega a cada registro el atributo en memoria:

        es_duplicada = True / False

    según si existe otra fila con la misma acta.

    `es_duplicada` no es una columna de la base de datos.
    """
    actas = {r.acta for r in registros if r.acta}

    conteos = {}

    if actas:
        conteos = dict(
            db.query(
                models.Registro.acta,
                func.count(models.Registro.id),
            )
            .filter(models.Registro.acta.in_(actas))
            .group_by(models.Registro.acta)
            .all()
        )

    for registro in registros:
        registro.es_duplicada = bool(
            registro.acta
            and conteos.get(registro.acta, 0) > 1
        )


# ---------------------------------------------------------------------------
# Actas reescritas
#
# Una reescritura es un grupo de registros que comparte:
#
#   patente + día de labrado + dirección
#
# pero tiene al menos dos números de acta diferentes.
#
# Esto la diferencia de un duplicado literal, donde el número de acta
# también es el mismo.
# ---------------------------------------------------------------------------

TAMANO_LOTE_REESCRITURAS = 1000


def _query_grupos_reescritos(db: Session):
    """
    Devuelve una fila por cada grupo de:
        patente normalizada + día + dirección normalizada

    que contiene más de un registro y más de un número de acta distinto.
    """
    patente_norm = _columna_patente_normalizada()
    dia = func.date(models.Registro.fecha_hora)
    direccion_norm = _columna_direccion_normalizada()

    return (
        db.query(
            patente_norm.label("patente_norm"),
            dia.label("dia"),
            direccion_norm.label("direccion_norm"),
        )
        .filter(
            models.Registro.patente.isnot(None),
            models.Registro.fecha_hora.isnot(None),
            models.Registro.direccion.isnot(None),
            models.Registro.direccion != "",
        )
        .group_by(
            patente_norm,
            dia,
            direccion_norm,
        )
        .having(func.count(models.Registro.id) > 1)
        .having(func.count(func.distinct(models.Registro.acta)) > 1)
    )


def _clave_grupo_reescritura(
    patente_norm: str,
    dia,
    direccion_norm: str,
) -> str:
    """
    Genera una clave legible y estable para identificar un grupo
    de reescritura.
    """
    dia_texto = (
        dia.isoformat()
        if hasattr(dia, "isoformat")
        else dia
    )

    return f"{patente_norm}|{dia_texto}|{direccion_norm}"


def calcular_actas_reescritas(
    db: Session,
    tamano_lote: int = TAMANO_LOTE_REESCRITURAS,
) -> dict:
    """
    Recalcula `reescrita` y `grupo_reescritura` para toda la tabla.

    Proceso:

    1. Obtiene los grupos con una reescritura real.
    2. Busca las filas pertenecientes a cada grupo.
    3. Marca esas filas como reescritas.
    4. Limpia las filas que estaban marcadas anteriormente pero
       ya no pertenecen a ningún grupo vigente.

    Hace commits por lotes para evitar mantener una única transacción
    gigante.

    Devuelve un resumen para el script ejecutable.
    """

    grupos = _query_grupos_reescritos(db).all()

    patente_norm_col = _columna_patente_normalizada()
    dia_col = func.date(models.Registro.fecha_hora)
    direccion_norm_col = _columna_direccion_normalizada()

    total_marcadas = 0
    detalle_grupos = []
    ids_afectados = set()

    pendientes = 0

    for patente_norm, dia, direccion_norm in grupos:

        filas = (
            db.query(models.Registro)
            .filter(
                patente_norm_col == patente_norm,
                dia_col == dia,
                direccion_norm_col == direccion_norm,
            )
            .order_by(
                models.Registro.fecha_hora,
                models.Registro.id,
            )
            .all()
        )

        clave = _clave_grupo_reescritura(
            patente_norm,
            dia,
            direccion_norm,
        )

        for fila in filas:
            fila.reescrita = True
            fila.grupo_reescritura = clave

            ids_afectados.add(fila.id)
            total_marcadas += 1
            pendientes += 1

        detalle_grupos.append(
            {
                "patente": patente_norm,
                "dia": dia,
                "direccion": direccion_norm,
                "actas": [f.acta for f in filas],
                "expedientes": [f.expediente for f in filas],
            }
        )

        if pendientes >= tamano_lote:
            db.commit()
            pendientes = 0

    db.commit()

    # -----------------------------------------------------------------------
    # Limpieza
    #
    # Cualquier fila que actualmente figura como reescrita pero que no
    # pertenece a ningún grupo vigente debe dejar de estar marcada.
    # -----------------------------------------------------------------------

    query_desactualizadas = (
        db.query(models.Registro)
        .filter(models.Registro.reescrita.is_(True))
    )

    if ids_afectados:
        query_desactualizadas = query_desactualizadas.filter(
            models.Registro.id.notin_(ids_afectados)
        )

    total_limpiadas = 0
    pendientes = 0

    for fila in query_desactualizadas.yield_per(tamano_lote):
        fila.reescrita = False
        fila.grupo_reescritura = None

        total_limpiadas += 1
        pendientes += 1

        if pendientes >= tamano_lote:
            db.commit()
            pendientes = 0

    db.commit()

    return {
        "grupos_encontrados": len(grupos),
        "actas_marcadas": total_marcadas,
        "actas_desmarcadas": total_limpiadas,
        "detalle_grupos": detalle_grupos,
    }
    
def aplicar_filtro_patente(query, patente: str, negar: bool = False, exacto: bool = False):
    patente_norm = _normalizar_patente_texto(patente)
    columna_norm = _columna_patente_normalizada()

    positiva = (columna_norm == patente_norm) if exacto else columna_norm.ilike(f"%{patente_norm}%")

    if negar:
        return query.filter(
            or_(
                models.Registro.patente.is_(None),
                ~positiva,
            )
        )

    return query.filter(positiva)

def aplicar_filtro_duplicadas(query, db: Session):
    actas_duplicadas = _query_actas_duplicadas(db)
    return query.filter(
        models.Registro.acta.in_(actas_duplicadas)
    )

TAMANO_LOTE_DUPLICADAS = 1000

def calcular_actas_duplicadas(db: Session, tamano_lote: int = TAMANO_LOTE_DUPLICADAS) -> dict:
    """
    Recalcula `duplicada` y `grupo_duplicada` para TODA la tabla de una vez.
    Pensado para el backfill inicial (los registros que ya existen en la
    base) o para un re-sync manual. Para altas/ediciones nuevas, esto ya
    se hace solo -- ver los event listeners en models.py.
    """
    actas_duplicadas = [a for (a,) in _query_actas_duplicadas(db).all()]

    total_marcadas = 0
    ids_afectados = set()
    pendientes = 0

    if actas_duplicadas:
        for fila in (
            db.query(models.Registro)
            .filter(models.Registro.acta.in_(actas_duplicadas))
            .yield_per(tamano_lote)
        ):
            fila.duplicada = True
            fila.grupo_duplicada = f"ACTA|{fila.acta}"
            ids_afectados.add(fila.id)
            total_marcadas += 1
            pendientes += 1
            if pendientes >= tamano_lote:
                db.commit()
                pendientes = 0
    db.commit()

    query_desactualizadas = db.query(models.Registro).filter(models.Registro.duplicada.is_(True))
    if ids_afectados:
        query_desactualizadas = query_desactualizadas.filter(models.Registro.id.notin_(ids_afectados))

    total_limpiadas = 0
    pendientes = 0
    for fila in query_desactualizadas.yield_per(tamano_lote):
        fila.duplicada = False
        fila.grupo_duplicada = None
        total_limpiadas += 1
        pendientes += 1
        if pendientes >= tamano_lote:
            db.commit()
            pendientes = 0
    db.commit()

    return {
        "actas_duplicadas_encontradas": len(actas_duplicadas),
        "filas_marcadas": total_marcadas,
        "filas_desmarcadas": total_limpiadas,
    }