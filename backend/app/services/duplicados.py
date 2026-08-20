import re
from typing import List

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import models
from app.services.sistemas.comun.texto import limpiar_patente as _normalizar_patente_texto
from sqlalchemy.orm import selectinload

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
            # Ver nota en models.py::_condiciones_grupo_reescritura: una
            # acta Rechazada en SEMyT no es una reescritura real -- es
            # una carga repetida a mano, no el mismo trámite con otro
            # número. No debe ni formar grupo ni arrastrar a otras.
            or_(
                models.Registro.estado_semyt.is_(None),
                models.Registro.estado_semyt != models.EstadoSemyt.rechazada,
            ),
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
                # Misma exclusión que _query_grupos_reescritos: una acta
                # Rechazada no es reescritura real, no se marca ni se
                # cuenta como parte del grupo.
                or_(
                    models.Registro.estado_semyt.is_(None),
                    models.Registro.estado_semyt != models.EstadoSemyt.rechazada,
                ),
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
    """
    Duplicada en CUALQUIERA de los dos sentidos:
      - "SEMyT": el mismo Nº de acta aparece en más de un Registro.
      - "SIGI": el mismo acta ya existía en la base con OTRO expediente
        SIGI (VinculoSigi.origen == 'duplicada', ver
        llenar_actas_sigi.py::_procesar_expediente_encontrado).
    Se devuelven consistentes E inconsistentes (a diferencia del reporte
    "Exportar Actas > Duplicadas SIGI", que sólo trae inconsistentes).
    Se excluyen las actas Rechazadas en SEMyT.
    """
    actas_duplicadas = _query_actas_duplicadas(db)
    return query.filter(
        or_(
            models.Registro.acta.in_(actas_duplicadas),
            models.Registro.vinculos_sigi.any(models.VinculoSigi.origen == "duplicada"),
        ),
        or_(
            models.Registro.estado_semyt.is_(None),
            models.Registro.estado_semyt != models.EstadoSemyt.rechazada,
        ),
    )

def aplicar_filtro_reescritas(query):
    """Mismo criterio que aplicar_filtro_duplicadas, para reescrituras:
    combina Registro.reescrita ("SEMyT", ver calcular_actas_reescritas)
    con VinculoSigi.origen == 'reescrita' ("SIGI"). Consistentes e
    inconsistentes, salvo Rechazada en SEMyT."""
    return query.filter(
        or_(
            models.Registro.reescrita.is_(True),
            models.Registro.vinculos_sigi.any(models.VinculoSigi.origen == "reescrita"),
        ),
        or_(
            models.Registro.estado_semyt.is_(None),
            models.Registro.estado_semyt != models.EstadoSemyt.rechazada,
        ),
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

def anotar_info_relaciones(db: Session, registros: List["models.Registro"]):
    """Agrega, en memoria (no son columnas), otros_expedientes_duplicada y
    otros_expedientes_reescritura: lista de expedientes de las filas
    hermanas del mismo grupo, para mostrar en la misma fila del frontend.

    NOTA: expediente ya no es una columna SQL (vive en vinculos_sigi,
    ver Parte 1-4) -- por eso acá se traen los Registro completos (con
    sus vinculos_sigi precargados) en vez de pedir Registro.expediente
    como columna suelta dentro de un db.query(...)."""
    grupos_dup = {r.grupo_duplicada for r in registros if r.grupo_duplicada}
    grupos_re = {r.grupo_reescritura for r in registros if r.grupo_reescritura}

    hermanos_dup = {}
    if grupos_dup:
        filas = (
            db.query(models.Registro)
            .filter(models.Registro.grupo_duplicada.in_(grupos_dup))
            .options(selectinload(models.Registro.vinculos_sigi))
            .all()
        )
        for r in filas:
            hermanos_dup.setdefault(r.grupo_duplicada, []).append((r.id, r.expediente))

    hermanos_re = {}
    if grupos_re:
        filas = (
            db.query(models.Registro)
            .filter(models.Registro.grupo_reescritura.in_(grupos_re))
            .options(selectinload(models.Registro.vinculos_sigi))
            .all()
        )
        for r in filas:
            hermanos_re.setdefault(r.grupo_reescritura, []).append((r.id, r.expediente, r.acta, r.estado_semyt))

    for r in registros:
        r.otros_expedientes_duplicada = [
            (exp or "Sin expediente") for (id_, exp) in hermanos_dup.get(r.grupo_duplicada, []) if id_ != r.id
        ] if r.grupo_duplicada else None

        r.otros_expedientes_reescritura = [
            f"{exp or 'Sin expediente'} (ACT-{acta})"
            for (id_, exp, acta, _estado_semyt) in hermanos_re.get(r.grupo_reescritura, []) if id_ != r.id
        ] if r.grupo_reescritura else None

        # -----------------------------------------------------------------
        # SEMyT "Eliminada por reescritura": no es un estado nuevo -- sigue
        # siendo estado_semyt == 'Eliminada'. Lo que faltaba mostrar es A
        # QUÉ acta se reescribió y en qué estado_semyt está esa otra acta
        # HOY. Se resuelve con el mismo grupo_reescritura que ya arma
        # otros_expedientes_reescritura -- no hace falta guardar nada nuevo
        # en la tabla, ni un valor de enum nuevo: se recalcula solo cada
        # vez que se lista (así nunca queda desactualizado si la acta
        # asociada cambia de estado más adelante).
        # AJUSTAR: si algún acta Eliminada tiene MÁS de un hermano vivo en
        # el mismo grupo (caso raro -- 3+ actas para el mismo
        # patente+día+dirección), se muestra el primero que NO esté
        # también eliminado; si no hay ninguno así, no se puede saber cuál
        # es "la vigente" y se deja sin estado (sólo el número de acta).
        # -----------------------------------------------------------------
        r.acta_semyt_asociada = None
        r.estado_semyt_asociado = None
        if r.estado_semyt == models.EstadoSemyt.eliminada and r.grupo_reescritura:
            hermanos = [h for h in hermanos_re.get(r.grupo_reescritura, []) if h[0] != r.id]
            candidato = next(
                (h for h in hermanos if h[3] != models.EstadoSemyt.eliminada),
                hermanos[0] if hermanos else None,
            )
            if candidato is not None:
                r.acta_semyt_asociada = candidato[2]
                r.estado_semyt_asociado = candidato[3]