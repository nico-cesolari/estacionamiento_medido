"""
Modelos SQLAlchemy para Estacionamiento Medido.

Tabla principal: Registro -> une Expediente / Acta / Causa con los tres
estados que vienen de sistemas distintos (SIGEMI, SEMyT, SIGI).
"""
from datetime import datetime
import enum
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum as SAEnum, ForeignKey, Index, event
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

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
    expediente = Column(String, index=True, nullable=True)
    acta = Column(String, index=True, nullable=False)
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
    estado_sigi = Column(SAEnum(EstadoSigi), nullable=False, default=EstadoSigi.no_cargada, index=True)
    motivo_archivo_sigi = Column(SAEnum(MotivoArchivoSigi), nullable=True, default=None, index=True)

    # Fecha de cobro: sólo tiene sentido cuando el estado correspondiente
    # implica pago (SIGEMI "Pagada", SEMyT "Pagada en Juzgado").
    fecha_cobro_sigemi = Column(DateTime, nullable=True)
    fecha_cobro_sigi = Column(DateTime, nullable=True)
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

    historial = relationship(
        "HistorialEstado",
        back_populates="registro",
        order_by="HistorialEstado.fecha_inicio",
        cascade="all, delete-orphan",
    )


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