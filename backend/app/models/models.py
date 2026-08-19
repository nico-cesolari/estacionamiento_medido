"""
Modelos SQLAlchemy para Estacionamiento Medido.

Tabla principal: Registro -> une Expediente / Acta / Causa con los tres
estados que vienen de sistemas distintos (SIGEMI, SEMyT, SIGI).
"""
from datetime import datetime
import re
import enum
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, Enum as SAEnum, ForeignKey,
    Index, event, func, select, update,
)
from sqlalchemy.orm import relationship
from sqlalchemy.orm.attributes import get_history
from typing import Optional
from ..database import Base

class EstadoSigemi(str, enum.Enum):
    sin_resolucion = "Sin Resolución"
    pendiente_procuracion = "Pasar a Procuración"  
    en_procuracion = "En Procuración"
    pagada = "Pago Voluntario"
    archivado = "Archivado"
    archivado_sin_resolucion = "Archivado Sin Resolución"
    resuelta_sin_archivo = "Resuelta sin Archivar"
    no_cargada = "No Cargada"

class MotivoArchivoSigemi(str, enum.Enum):
    """
    Aclara el motivo de resolución de la causa. Aplica cuando
    estado_sigemi == 'Archivado' (motivo del archivo) y también cuando
    estado_sigemi == 'Resuelta sin Archivar' (motivo de la resolución
    que no pasó por archivo). Para cualquier otro estado va en None.
    """
    por_pago = "Pago voluntario"
    por_pago_procuracion = "Pago en Procuración"  
    por_desestimacion = "Desestimación"
    por_amonestacion = "Amonestación"
    por_sobreseimiento = "Sobreseimiento"
    suspendida = "Suspensión"

class EstadoSemyt(str, enum.Enum):
    no_cargada = "No Cargada"
    vencida = "Vencida"
    pagada_en_juzgado = "Pagada en Juzgado"
    resuelta_en_juzgado = "Resuelta en Juzgado"
    rechazada = "Rechazada"
    eliminada = "Eliminada"

class EstadoSigi(str, enum.Enum):
    no_cargada = "No Cargada"
    sin_notificar = "Sin Notificar"
    notificada = "citado"
    resolucion_pendiente = "Resolución Pendiente"
    pago_pendiente_con_resolucion = "Pago Pendiente con Resolución"
    descargo_presentado = "Descargo presentado"
    pre_judicial = "Prejudicial"
    archivado = "Archivado"

class MotivoArchivoSigi(str, enum.Enum):
    por_pago = "Pagada"
    por_desestimacion = "Desestimación"
    por_amonestacion = "Amonestación"
    por_sobreseimiento = "Sobreseimiento"
    suspendida = "Suspensión"

class Registro(Base):
    __tablename__ = "registros"
    __table_args__ = (
        Index("ix_registros_fecha_hora_id", "fecha_hora", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    juzgado = Column(Integer, nullable=True, index=True)
    # Identificadores (vienen de los 3 sistemas de origen)
    acta = Column(String, index=True, unique=True, nullable=False)
    causa = Column(String, index=True, nullable=True)
    patente = Column(String, index=True, nullable=False)
    direccion = Column(String, nullable=True)
    # index=True: es la columna de ORDER BY en TODAS las consultas paginadas
    # (buscar_registros, buscar_para_exportar) y además se filtra por rango
    # de fechas en casi todas las pantallas. Sin índice, con 160k+ filas,
    # cada página ordenada implica un sort completo de la tabla filtrada.
    fecha_hora = Column(DateTime, nullable=True, index=True)
    foto_url = Column(String, nullable=True)

    # estado_sigemi/estado_sigi SÍ tienen un valor "neutro" real
    # ('No Cargada' -- ver EstadoSigemi/EstadoSigi más arriba), así que se
    # completan con ese default en vez de quedar en NULL: así el filtro de
    # la grilla (crud.buscar_registros) los encuentra con un simple
    # `== 'No Cargada'`, sin tener que contemplar NULL en cada query.
    # OJO: este `default=` es del lado de Python/SQLAlchemy -- se aplica
    # en cualquier INSERT hecho por el ORM que no lo especifique explícito,
    # pero no es un constraint real de la base (para eso hace falta además
    # el ALTER TABLE ... SET NOT NULL, después de correr el backfill).
    # index=True en los 5: son los filtros más usados de la grilla
    # (listar_registros) y del reporte (aplicar_filtros_avanzados), y
    # motivo_archivo_sigemi/sigi encima son la base de calcular_consistencia
    # -- aunque esa parte se calcula en Python, el filtro "consistencia"
    # sigue trayendo TODAS las filas que matchean estado_sigemi/semyt/sigi
    # primero, así que conviene que esos WHERE sean rápidos.
    estado_sigemi = Column(SAEnum(EstadoSigemi), nullable=False, default=EstadoSigemi.no_cargada, index=True)
    motivo_archivo_sigemi = Column(SAEnum(MotivoArchivoSigemi), nullable=True, default=None, index=True)
    # EstadoSemyt no tiene un valor 'No Cargada' en la lista de estados
    # reales -- se deja como estaba (nullable, sin default) hasta que
    # SEMyT sea la fuente que decida si hace falta agregarlo.
    estado_semyt = Column(SAEnum(EstadoSemyt), nullable=True, default=None, index=True)

    # Fecha de cobro: sólo tiene sentido cuando el estado correspondiente
    # implica pago (SIGEMI "Pagada", SEMyT "Pagada en Juzgado").
    fecha_cobro_sigemi = Column(DateTime, nullable=True)
    # Consistencia entre SEMyT/SIGEMI/SIGI: antes se calculaba 100% en Python
    # en cada consulta (ver crud.calcular_consistencia), lo que obligaba a
    # traer TODA la tabla filtrada a memoria para poder filtrar por este
    # campo. Ahora se guarda como columna real, recalculada automáticamente
    # cada vez que cambia algún estado (ver crud.aplicar_cambios_estado),
    # así el filtro "consistencia" se resuelve en SQL con índice, como
    # cualquier otro filtro. None = todavía no se puede determinar (falta
    # completar algún estado/motivo).
    consistente = Column(Boolean, nullable=True, default=None, index=True)
    reescrita = Column(Boolean, nullable=True, default=False, index=True)
    grupo_reescritura = Column(String, nullable=True, index=True)

    # NUEVO: faltaban en el modelo aunque ya existen en la tabla
    duplicada = Column(Boolean, nullable=True, default=False, index=True)
    grupo_duplicada = Column(String, nullable=True, index=True)

    historial = relationship(
        "HistorialEstado",
        back_populates="registro",
        order_by="HistorialEstado.fecha_inicio",
        cascade="all, delete-orphan",
    )
    
    vinculos_sigi = relationship(
        "VinculoSigi",
        back_populates="registro",
        order_by="VinculoSigi.expediente",  
        cascade="all, delete-orphan",
    )

    @property
    def vinculo_sigi_principal(self) -> Optional["VinculoSigi"]:
        """El vínculo con expediente más chico -- lo que se muestra como
        'el' expediente/estado_sigi de la fila en la grilla principal
        cuando no hay más de uno. Ordenar por expediente string es
        aproximado (EXP-2026-0080 < EXP-2026-176985 alfabéticamente
        coincide con el orden numérico gracias al padding), pero para
        casos raros con distinto año conviene ordenar en Python por el
        número real -- ver sigi_vinculos.ordenar_vinculos."""
        from app.services.sigi_vinculos import ordenar_vinculos
        vinculos = ordenar_vinculos(self.vinculos_sigi)
        return vinculos[0] if vinculos else None

    @property
    def tiene_multiples_vinculos_sigi(self) -> bool:
        return len(self.vinculos_sigi) > 1
    
    @property
    def expediente(self):
        v = self.vinculo_sigi_principal
        return v.expediente if v else None

    @property
    def estado_sigi(self):
        v = self.vinculo_sigi_principal
        return v.estado_sigi if v else EstadoSigi.no_cargada

    @property
    def motivo_archivo_sigi(self):
        v = self.vinculo_sigi_principal
        return v.motivo_archivo_sigi if v else None

    @property
    def fecha_cobro_sigi(self):
        v = self.vinculo_sigi_principal
        return v.fecha_cobro_sigi if v else None

class VinculoSigi(Base):
    __tablename__ = "vinculos_sigi"
    __table_args__ = (
        Index("ix_vinculos_sigi_registro_id", "registro_id"),
        Index("ix_vinculos_sigi_expediente", "expediente"),
    )

    id = Column(Integer, primary_key=True, index=True)
    registro_id = Column(Integer, ForeignKey("registros.id", ondelete="CASCADE"), nullable=False)
    expediente = Column(String, nullable=False)
    estado_sigi = Column(SAEnum(EstadoSigi), nullable=False, default=EstadoSigi.no_cargada, index=True)
    motivo_archivo_sigi = Column(SAEnum(MotivoArchivoSigi), nullable=True, default=None)
    fecha_cobro_sigi = Column(DateTime, nullable=True)
    acta_sigi = Column(String, nullable=True)
    # 'directo'  : se cargó buscando este expediente puntual (flujo normal)
    # 'duplicada': el Nº de acta ya existía en la base con OTRO expediente
    # 'reescrita': patente+día+dirección matcheaban otra acta ya cargada
    origen = Column(String, nullable=False, default="directo")
    consistente = Column(Boolean, nullable=True, default=None, index=True)
    creado_en = Column(DateTime, nullable=False, default=datetime.now)

    registro = relationship("Registro", back_populates="vinculos_sigi")
    
class SistemaEstado(str, enum.Enum):
    """Los 3 sistemas de origen cuyo estado seguimos en el historial."""
    sigemi = "SIGEMI"
    semyt = "SEMyT"
    sigi = "SIGI"


class HistorialEstado(Base):
    """
    Un renglón por cada período que un registro pasó en un estado dado,
    para uno de los 3 sistemas (SIGEMI / SEMyT / SIGI).

    fecha_inicio: cuándo empezó a regir `estado_nuevo` (+ `motivo_archivo_nuevo`
                  si aplica).
    fecha_fin:    cuándo dejó de regir (se completa automáticamente al
                  registrarse el cambio siguiente). None = es el estado
                  vigente hoy para ese sistema.

    Se guarda tanto el estado anterior como el nuevo (y sus motivos de
    archivo, cuando corresponde) para no perder de dónde venía el cambio
    -- esto es lo que permite, por ejemplo, detectar específicamente el
    caso SEMyT "Vencida" -> "Rechazada".

    Estado/motivo se guardan como texto (no como FK al Enum) porque un
    registro histórico no debería dejar de tener sentido si en el futuro
    se agrega/renombra un valor del enum.
    """
    __tablename__ = "historial_estados"
    __table_args__ = (
        Index("ix_historial_registro_sistema_abierto", "registro_id", "sistema", "fecha_fin"),
    )

    id = Column(Integer, primary_key=True, index=True)
    registro_id = Column(Integer, ForeignKey("registros.id"), nullable=False, index=True)
    sistema = Column(SAEnum(SistemaEstado), nullable=False, index=True)

    estado_anterior = Column(String, nullable=True)  # None = primer estado que tuvo (alta)
    estado_nuevo = Column(String, nullable=True)
    motivo_archivo_anterior = Column(String, nullable=True)
    motivo_archivo_nuevo = Column(String, nullable=True)

    fecha_inicio = Column(DateTime, nullable=False, server_default=func.now())
    fecha_fin = Column(DateTime, nullable=True)

    registro = relationship("Registro", back_populates="historial")

def calcular_juzgado(fecha_hora):
    if not fecha_hora:
        return None
    return 1 if fecha_hora.day <= 15 else 2

@event.listens_for(Registro, "before_insert")
@event.listens_for(Registro, "before_update")
def _autocompletar_juzgado(mapper, connection, target):
    target.juzgado = calcular_juzgado(target.fecha_hora)
    
# ---------------------------------------------------------------------------
# Duplicadas / Reescritas: se recalculan solas en cada insert/update.
#
# IMPORTANTE: estos listeners usan `connection` (Core), NO el Session del
# ORM. Es el patrón recomendado por SQLAlchemy para tocar otras filas desde
# adentro de un evento before_insert/before_update -- usar el Session acá
# adentro dispararía un flush recursivo y rompería todo.
#
# La normalización de patente/dirección está duplicada a propósito acá
# (en vez de importar app.services.duplicados) para evitar un import
# circular: duplicados.py ya importa models.py.
# ---------------------------------------------------------------------------

def _normalizar_patente_py(valor):
    if not valor:
        return ""
    texto = valor.upper()
    for ch in ("-", " ", ".", "_"):
        texto = texto.replace(ch, "")
    return texto


def _normalizar_direccion_py(valor):
    if not valor:
        return ""
    return re.sub(r"\s+", " ", valor.strip()).upper()


def _clave_grupo_reescritura(patente_norm, dia, direccion_norm):
    dia_texto = dia.isoformat() if hasattr(dia, "isoformat") else str(dia)
    return f"{patente_norm}|{dia_texto}|{direccion_norm}"


def _clave_grupo_duplicada(acta):
    return f"ACTA|{acta}"


def _condiciones_grupo_reescritura(tabla, patente_norm, dia, direccion_norm, excluir_id=None):
    patente_norm_col = func.upper(
        func.replace(
            func.replace(
                func.replace(
                    func.replace(tabla.c.patente, "-", ""),
                    " ", ""),
                ".", ""),
            "_", "")
    )
    dia_col = func.date(tabla.c.fecha_hora)
    direccion_norm_col = func.upper(func.trim(tabla.c.direccion))

    condiciones = [
        patente_norm_col == patente_norm,
        dia_col == dia,
        direccion_norm_col == direccion_norm,
    ]
    if excluir_id is not None:
        condiciones.append(tabla.c.id != excluir_id)
    return condiciones


# --------------------------- DUPLICADAS ---------------------------

def _recalcular_duplicada(connection, target):
    tabla = Registro.__table__

    if not target.acta:
        target.duplicada = False
        target.grupo_duplicada = None
        return

    condiciones = [tabla.c.acta == target.acta]
    if target.id is not None:
        condiciones.append(tabla.c.id != target.id)

    cantidad_otras = connection.execute(
        select(func.count()).select_from(tabla).where(*condiciones)
    ).scalar_one()

    clave = _clave_grupo_duplicada(target.acta)

    if cantidad_otras >= 1:
        target.duplicada = True
        target.grupo_duplicada = clave
        connection.execute(
            update(tabla).where(*condiciones).values(duplicada=True, grupo_duplicada=clave)
        )
    else:
        target.duplicada = False
        target.grupo_duplicada = None


def _limpiar_grupo_anterior_duplicada(connection, target):
    """En un update: si el acta cambió, la fila que compartía la acta
    VIEJA puede haber quedado sola -> hay que desmarcarla."""
    historial = get_history(target, "acta")
    if not historial.deleted:
        return

    acta_anterior = historial.deleted[0]
    if not acta_anterior:
        return

    tabla = Registro.__table__
    condiciones = [tabla.c.acta == acta_anterior]
    if target.id is not None:
        condiciones.append(tabla.c.id != target.id)

    cantidad_restante = connection.execute(
        select(func.count()).select_from(tabla).where(*condiciones)
    ).scalar_one()

    if cantidad_restante <= 1:
        connection.execute(
            update(tabla).where(*condiciones).values(duplicada=False, grupo_duplicada=None)
        )


# --------------------------- REESCRITAS ---------------------------

def _recalcular_reescritura(connection, target):
    tabla = Registro.__table__
    patente_norm = _normalizar_patente_py(target.patente)
    direccion_norm = _normalizar_direccion_py(target.direccion)
    dia = target.fecha_hora.date() if target.fecha_hora else None

    if not patente_norm or not direccion_norm or not dia:
        target.reescrita = False
        target.grupo_reescritura = None
        return

    condiciones = _condiciones_grupo_reescritura(
        tabla, patente_norm, dia, direccion_norm, excluir_id=target.id
    )

    filas_relacionadas = connection.execute(
        select(tabla.c.id, tabla.c.acta).where(*condiciones)
    ).all()

    actas_relacionadas = {fila.acta for fila in filas_relacionadas}
    actas_relacionadas.add(target.acta)

    clave = _clave_grupo_reescritura(patente_norm, dia, direccion_norm)

    if filas_relacionadas and len(actas_relacionadas) > 1:
        target.reescrita = True
        target.grupo_reescritura = clave
        connection.execute(
            update(tabla).where(*condiciones).values(reescrita=True, grupo_reescritura=clave)
        )
    else:
        target.reescrita = False
        target.grupo_reescritura = None


def _limpiar_grupo_anterior_reescritura(connection, target):
    """En un update: si patente/dirección/fecha cambiaron, el grupo VIEJO
    puede haber quedado sin sentido (0 o 1 fila, o todas con la misma
    acta) -> hay que desmarcarlo."""
    hist_patente = get_history(target, "patente")
    hist_direccion = get_history(target, "direccion")
    hist_fecha = get_history(target, "fecha_hora")

    if not (hist_patente.deleted or hist_direccion.deleted or hist_fecha.deleted):
        return  # nada de esto cambió, no hay grupo viejo que limpiar

    patente_anterior = hist_patente.deleted[0] if hist_patente.deleted else target.patente
    direccion_anterior = hist_direccion.deleted[0] if hist_direccion.deleted else target.direccion
    fecha_anterior = hist_fecha.deleted[0] if hist_fecha.deleted else target.fecha_hora

    tabla = Registro.__table__
    patente_norm = _normalizar_patente_py(patente_anterior)
    direccion_norm = _normalizar_direccion_py(direccion_anterior)
    dia = fecha_anterior.date() if fecha_anterior else None

    if not patente_norm or not direccion_norm or not dia:
        return

    condiciones = _condiciones_grupo_reescritura(
        tabla, patente_norm, dia, direccion_norm, excluir_id=target.id
    )

    filas = connection.execute(select(tabla.c.id, tabla.c.acta).where(*condiciones)).all()
    actas = {f.acta for f in filas}

    if len(filas) == 0 or len(actas) <= 1:
        connection.execute(
            update(tabla).where(*condiciones).values(reescrita=False, grupo_reescritura=None)
        )


@event.listens_for(Registro, "before_insert")
def _relaciones_al_insertar(mapper, connection, target):
    _recalcular_duplicada(connection, target)
    _recalcular_reescritura(connection, target)


@event.listens_for(Registro, "before_update")
def _relaciones_al_actualizar(mapper, connection, target):
    _limpiar_grupo_anterior_duplicada(connection, target)
    _limpiar_grupo_anterior_reescritura(connection, target)
    _recalcular_duplicada(connection, target)
    _recalcular_reescritura(connection, target)