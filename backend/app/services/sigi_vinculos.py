"""
Núcleo de la relación 1 acta -> N expedientes SIGI (vinculos_sigi).

Reemplaza, para todo lo relacionado a SIGI, la vieja suposición de
"1 registro = 1 expediente = 1 estado_sigi". Un Registro puede tener:
  - 0 vínculos: todavía no pasó por SIGI.
  - 1 vínculo (origen='directo'): caso normal.
  - 2+ vínculos: acta con más de un expediente en SIGI -- puede ser
    porque el MISMO Nº de acta se cargó con otro expediente
    (origen='duplicada'), o porque otra acta con datos parecidos
    (patente+día+dirección) fue reescrita bajo un expediente nuevo
    (origen='reescrita').
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import models
from app.services.sistemas.comun.texto import limpiar_patente as _normalizar_patente_texto
from app.services.consistencia import categoria_sigi, categoria_semyt, categoria_sigemi, _mismo_grupo


# ---------------------------------------------------------------------------
# Orden estable de vínculos (por número real de expediente, no por string)
# ---------------------------------------------------------------------------
_REGEX_EXP = re.compile(r"(\d{4})-(\d+)")


def _clave_orden_expediente(expediente: str):
    m = _REGEX_EXP.search(expediente or "")
    if not m:
        return (9999, 0, expediente or "")
    anio, numero = m.groups()
    return (int(anio), int(numero), expediente)


def ordenar_vinculos(vinculos: List["models.VinculoSigi"]) -> List["models.VinculoSigi"]:
    return sorted(vinculos, key=lambda v: _clave_orden_expediente(v.expediente))


# ---------------------------------------------------------------------------
# Normalización (reutiliza la misma que duplicados.py, para no divergir)
# ---------------------------------------------------------------------------
def _normalizar_direccion(valor) -> str:
    if not valor:
        return ""
    return re.sub(r"\s+", " ", valor.strip()).upper()


def _normalizar_patente(valor) -> str:
    return _normalizar_patente_texto(valor or "")


# ---------------------------------------------------------------------------
# Búsqueda de registro por acta / por datos parecidos
# ---------------------------------------------------------------------------
def buscar_registro_por_acta(db: Session, acta: str) -> Optional["models.Registro"]:
    from app.services.sistemas.sigi.reglas.reglas_sigi import normalizar_acta
    acta_norm = normalizar_acta(acta)
    if not acta_norm:
        return None
    # acta es unique en registros, pero se guarda tal cual vino de SEMyT
    # (no normalizada) -- comparamos normalizando de los dos lados.
    for registro in db.query(models.Registro).filter(models.Registro.acta.isnot(None)).all():
        if normalizar_acta(registro.acta) == acta_norm:
            return registro
    return None


def buscar_registro_reescrito(
    db: Session,
    patente: Optional[str],
    direccion: Optional[str],
    fecha_hora: Optional[datetime],
    excluir_acta: Optional[str] = None,
) -> Optional["models.Registro"]:
    """
    Busca un Registro YA EXISTENTE cuya (patente, día, dirección)
    normalizados coincidan -- mismo criterio que
    app.services.duplicados._query_grupos_reescritos, pero acá se busca
    UN match puntual contra el dato que acabamos de leer en SIGI, no se
    recalculan grupos masivos.

    None si falta algún dato (patente/dirección/fecha) -- no se puede
    determinar reescritura sin los 3.
    """
    if not patente or not direccion or not fecha_hora:
        return None

    patente_norm = _normalizar_patente(patente)
    direccion_norm = _normalizar_direccion(direccion)
    dia = fecha_hora.date()

    if not patente_norm or not direccion_norm:
        return None

    candidatos = (
        db.query(models.Registro)
        .filter(
            models.Registro.patente.isnot(None),
            models.Registro.direccion.isnot(None),
            models.Registro.fecha_hora.isnot(None),
            # Mismo criterio que models.py::_condiciones_grupo_reescritura
            # y duplicados.py::_query_grupos_reescritos: una acta Rechazada
            # en SEMyT no es una reescritura real -- es una carga repetida
            # a mano, no debe matchear como candidata.
            or_(
                models.Registro.estado_semyt.is_(None),
                models.Registro.estado_semyt != models.EstadoSemyt.rechazada,
            ),
        )
        .all()
    )
    for candidato in candidatos:
        if excluir_acta and candidato.acta == excluir_acta:
            continue
        if _normalizar_patente(candidato.patente) != patente_norm:
            continue
        if _normalizar_direccion(candidato.direccion) != direccion_norm:
            continue
        if candidato.fecha_hora.date() != dia:
            continue
        return candidato
    return None


# ---------------------------------------------------------------------------
# Alta / actualización de vínculos
# ---------------------------------------------------------------------------
def crear_vinculo(
    db: Session,
    registro: "models.Registro",
    expediente: str,
    estado_sigi: Optional["models.EstadoSigi"],
    motivo_archivo_sigi: Optional["models.MotivoArchivoSigi"] = None,
    origen: str = "directo",
) -> "models.VinculoSigi":
    """Crea un VinculoSigi nuevo. No verifica duplicados de expediente
    (eso lo hace el caller antes -- ver llenar_actas_sigi.py, que ya
    sabe si el expediente venía de 'ya conocido' o no)."""
    vinculo = models.VinculoSigi(
        registro_id=registro.id,
        expediente=expediente,
        estado_sigi=estado_sigi or models.EstadoSigi.no_cargada,
        motivo_archivo_sigi=motivo_archivo_sigi,
    )
    if motivo_archivo_sigi == models.MotivoArchivoSigi.por_pago:
        vinculo.fecha_cobro_sigi = datetime.now()
    vinculo.origen = origen
    db.add(vinculo)
    db.flush()  # necesitamos vinculo.id / consistencia calculable
    recalcular_consistencia_vinculo(registro, vinculo)
    return vinculo


def actualizar_vinculo(
    db: Session,
    vinculo: "models.VinculoSigi",
    estado_sigi: Optional["models.EstadoSigi"] = None,
    motivo_archivo_sigi: Optional["models.MotivoArchivoSigi"] = None,
) -> bool:
    """True si hubo cambio real."""
    cambio = False
    if estado_sigi is not None and vinculo.estado_sigi != estado_sigi:
        vinculo.estado_sigi = estado_sigi
        cambio = True
        if estado_sigi != models.EstadoSigi.archivado:
            vinculo.motivo_archivo_sigi = None
            vinculo.fecha_cobro_sigi = None
    if motivo_archivo_sigi is not None and vinculo.motivo_archivo_sigi != motivo_archivo_sigi:
        vinculo.motivo_archivo_sigi = motivo_archivo_sigi
        cambio = True
        if motivo_archivo_sigi == models.MotivoArchivoSigi.por_pago and vinculo.fecha_cobro_sigi is None:
            vinculo.fecha_cobro_sigi = datetime.now()
        elif motivo_archivo_sigi != models.MotivoArchivoSigi.por_pago:
            vinculo.fecha_cobro_sigi = None
    if cambio:
        recalcular_consistencia_vinculo(vinculo.registro, vinculo)
    return cambio


def eliminar_vinculo_no_encontrado(db: Session, vinculo: "models.VinculoSigi") -> None:
    """Expediente que dejó de existir del lado de SIGI (ver
    desvincular_expediente_no_encontrado en reglas_sigi.py)."""
    db.delete(vinculo)


# ---------------------------------------------------------------------------
# Consistencia POR VÍNCULO (punto 4/5 del pedido: cada expediente tiene
# la suya propia, no una sola para toda el acta)
# ---------------------------------------------------------------------------
def recalcular_consistencia_vinculo(registro: "models.Registro", vinculo: "models.VinculoSigi") -> None:
    """
    Combina el estado de ESTE vínculo SIGI puntual con el estado de
    SEMyT/SIGEMI del registro (que sí son únicos por acta). Mismo
    criterio de categorías/equivalencias que
    app.services.consistencia.calcular_consistencia, pero evaluando un
    solo expediente SIGI a la vez.

    Si SEMyT o SIGEMI todavía no tienen info suficiente (ver
    _sigemi_ignorable/lo que corresponda), el criterio es más laxo: sólo
    hace falta que SIGI + lo que SÍ está disponible coincidan.
    """
    from app.services.consistencia import _sigemi_ignorable, _sigi_ignorable

    cat_sigi = categoria_sigi(vinculo.estado_sigi, vinculo.motivo_archivo_sigi)
    cat_semyt = categoria_semyt(registro.estado_semyt)

    categorias = {}
    faltantes = []

    if cat_semyt is None:
        faltantes.append("SEMyT")
    else:
        categorias["SEMyT"] = cat_semyt

    sigemi_ignorable = _sigemi_ignorable(registro)
    if not sigemi_ignorable:
        cat_sigemi = categoria_sigemi(registro.estado_sigemi, registro.motivo_archivo_sigemi)
        if cat_sigemi is None:
            faltantes.append("SIGEMI")
        else:
            categorias["SIGEMI"] = cat_sigemi

    if cat_sigi is None:
        if vinculo.estado_sigi != models.EstadoSigi.no_cargada:
            faltantes.append("SIGI")
        # 'No Cargada' en un vínculo recién creado: se ignora, igual que
        # _sigi_ignorable para el caso de registros.consistente.
    else:
        categorias["SIGI"] = cat_sigi

    if faltantes:
        vinculo.consistente = None
        return

    vinculo.consistente = _mismo_grupo(set(categorias.values()))
    
def anotar_info_sigi(registros: List["models.Registro"]) -> None:
    """Flags en memoria (no son columnas): True si el registro tiene
    algún vínculo con ese origen. Sólo puede haber >1 vínculo si ya se
    cargó expediente+estado_sigi dos veces (ver crear_vinculo), así que
    esto respeta la regla 'sin 2 expedientes no-null, no se marca'."""
    for r in registros:
        r.sigi_duplicada = any(v.origen == "duplicada" for v in r.vinculos_sigi)
        r.sigi_reescrita = any(v.origen == "reescrita" for v in r.vinculos_sigi)