from app.models import models
from datetime import datetime
from sqlalchemy.orm import Session
from app.services.consistencia import calcular_consistencia
from typing import List, Optional
from typing import Any

CAMPO_A_SISTEMA = {
    "estado_sigemi": models.SistemaEstado.sigemi,
    "estado_semyt": models.SistemaEstado.semyt,
    "estado_sigi": models.SistemaEstado.sigi,
}

# Para SIGEMI y SIGI el motivo de archivo viaja junto con el estado en el
# mismo renglón de historial; SEMyT no tiene motivo.

def _valor_enum(valor):
    """
    Devuelve el valor real de un Enum o el valor recibido si no es Enum.
    """
    if valor is None:
        return None
    return valor.value if hasattr(valor, "value") else valor

def precargar_historial_abierto(db: Session, registro_ids: List[int]):
    """
    Trae en UNA sola query todos los renglones de historial "abiertos"
    (fecha_fin IS NULL) de los registros indicados, y los devuelve como
    dict {(registro_id, sistema): HistorialEstado}.

    Pensado para cargas masivas (import de SIGEMI/SEMyT/SIGI): pasarle
    este dict a `aplicar_cambios_estado` vía `historial_abierto_cache`
    evita que cada registro dispare su propio SELECT para buscar el
    renglón abierto -- con miles de actas por corrida, eso es la
    diferencia entre 1 query y miles.
    """
    if not registro_ids:
        return {}
    abiertos = (
        db.query(models.HistorialEstado)
        .filter(
            models.HistorialEstado.registro_id.in_(registro_ids),
            models.HistorialEstado.fecha_fin.is_(None),
        )
        .all()
    )
    return {(h.registro_id, h.sistema): h for h in abiertos}


def _registrar_historial_si_cambio(db, registro, campo_estado, estado_antes, estado_despues,
                                    motivo_antes, motivo_despues, momento,
                                    historial_abierto_cache: Optional[dict] = None):
    """
    Si el estado y/o el motivo de archivo de `campo_estado` realmente
    cambiaron, cierra el renglón de historial que estaba abierto para ese
    sistema (fecha_fin = momento) y abre uno nuevo (fecha_inicio = momento,
    fecha_fin = None).

    Esto es lo que permite reconstruir, para cualquiera de los 3 sistemas,
    "estuvo en estado X desde tal fecha hasta tal fecha" -- y en particular
    detectar en SEMyT el pasaje puntual Vencida -> Rechazada, con sus
    fechas exactas.

    `historial_abierto_cache`: dict opcional {(registro_id, sistema): fila},
    típicamente salido de `precargar_historial_abierto`. Si se pasa, se usa
    en vez de consultar la DB -- esto es lo que evita el N+1 en cargas
    masivas (ver `aplicar_cambios_estado_bulk`). Si no se pasa, se
    consulta individualmente (comportamiento de siempre, para el PATCH de
    un solo registro).
    """
    sistema = CAMPO_A_SISTEMA[campo_estado]

    ea, ed = _valor_enum(estado_antes), _valor_enum(estado_despues)
    ma, md = _valor_enum(motivo_antes), _valor_enum(motivo_despues)

    if ea == ed and ma == md:
        return  # no hubo cambio real (ej: mandaron el mismo valor que ya tenía)

    if historial_abierto_cache is not None:
        abierto = historial_abierto_cache.get((registro.id, sistema))
    else:
        abierto = (
            db.query(models.HistorialEstado)
            .filter(
                models.HistorialEstado.registro_id == registro.id,
                models.HistorialEstado.sistema == sistema,
                models.HistorialEstado.fecha_fin.is_(None),
            )
            .first()
        )
    if abierto is not None:
        abierto.fecha_fin = momento

    nuevo = models.HistorialEstado(
        registro_id=registro.id,
        sistema=sistema,
        estado_anterior=ea,
        estado_nuevo=ed,
        motivo_archivo_anterior=ma,
        motivo_archivo_nuevo=md,
        fecha_inicio=momento,
        fecha_fin=None,
    )
    db.add(nuevo)
    if historial_abierto_cache is not None:
        # Deja el cache consistente por si el mismo registro se vuelve a
        # tocar más adelante en la misma corrida (ej: dos pasadas sobre el
        # mismo acta dentro del mismo import).
        historial_abierto_cache[(registro.id, sistema)] = nuevo
        
def aplicar_cambios_estado(
    db: Session,
    registro: "models.Registro",
    cambios: dict,
    momento: Optional[datetime] = None,
    historial_abierto_cache: Optional[dict] = None,
):
    """
    Aplica `cambios` (mismo shape que RegistroUpdate: cualquier subconjunto
    de estado_sigemi/motivo_archivo_sigemi/estado_semyt/estado_sigi/
    motivo_archivo_sigi) sobre `registro`, con todos los efectos de negocio:
      - fecha de cobro (se completa sola al pasar a Pagada / se limpia al
        salir de Pagada), para SIGEMI, SEMyT y SIGI (en SIGI, "Pagada" es
        el motivo_archivo_sigi, ya que el sistema no tiene un estado de
        pago propio -- se archiva con ese motivo).
      - limpieza de motivo_archivo_* cuando el estado deja de ser
        "Archivada".
      - historial de estado (fecha_inicio/fecha_fin) por sistema, para
        CUALQUIER cambio real de estado o de motivo.

    No hace commit: eso queda a cargo del caller, para poder aplicar varios
    registros dentro de una misma transacción (ej: import masivo).
    """
    if momento is None:
        momento = datetime.now()

    # Snapshot de "antes", para el historial y para saber qué campos
    # realmente cambiaron (si mandan el mismo valor que ya tenía, no
    # generamos un renglón de historial de más).
    antes = {
        "estado_sigemi": registro.estado_sigemi,
        "motivo_archivo_sigemi": registro.motivo_archivo_sigemi,
        "estado_semyt": registro.estado_semyt,
        "estado_sigi": registro.estado_sigi,
        "motivo_archivo_sigi": registro.motivo_archivo_sigi,
    }

    for campo, valor in cambios.items():
        setattr(registro, campo, valor)

        if campo == "estado_sigemi":
            if valor == models.EstadoSigemi.pagada and registro.fecha_cobro_sigemi is None:
                registro.fecha_cobro_sigemi = momento
            elif valor != models.EstadoSigemi.pagada:
                registro.fecha_cobro_sigemi = None
                
            if valor not in (models.EstadoSigemi.archivada, models.EstadoSigemi.resuelta_sin_archivo):
                registro.motivo_archivo_sigemi = None
        if campo == "motivo_archivo_sigemi":
            if valor == models.MotivoArchivoSigemi.por_pago and registro.fecha_cobro_sigemi is None:
                registro.fecha_cobro_sigemi = momento
            elif valor != models.MotivoArchivoSigemi.por_pago:
                registro.fecha_cobro_sigemi = None
        if campo == "estado_sigi" and valor != models.EstadoSigi.archivada:
            registro.motivo_archivo_sigi = None
            registro.fecha_cobro_sigi = None

        if campo == "motivo_archivo_sigi":
            if valor == models.MotivoArchivoSigi.por_pago and registro.fecha_cobro_sigi is None:
                registro.fecha_cobro_sigi = momento
            elif valor != models.MotivoArchivoSigi.por_pago:
                registro.fecha_cobro_sigi = None

    # Historial: uno por sistema, comparando el snapshot de "antes" contra
    # el estado final ya con todos los efectos de arriba aplicados (así, si
    # por ejemplo cambiás estado_sigemi y eso limpia motivo_archivo_sigemi,
    # ese motivo limpiado también queda reflejado en el renglón nuevo).
    for campo_estado, campo_motivo in (
        ("estado_sigemi", "motivo_archivo_sigemi"),
        ("estado_semyt", None),
        ("estado_sigi", "motivo_archivo_sigi"),
    ):
        motivo_antes = antes[campo_motivo] if campo_motivo else None
        motivo_despues = getattr(registro, campo_motivo) if campo_motivo else None
        _registrar_historial_si_cambio(
            db, registro, campo_estado,
            antes[campo_estado], getattr(registro, campo_estado),
            motivo_antes, motivo_despues,
            momento,
            historial_abierto_cache=historial_abierto_cache,
        )

    # Recalculamos y persistimos "consistente" acá mismo, en el único punto
    # que centraliza cualquier cambio de estado -- así la columna real
    # nunca queda desincronizada, sin importar si el cambio vino del PATCH
    # manual, de un import masivo o de una migración futura.
    registro.consistente = calcular_consistencia(registro)

    return registro


def aplicar_cambios_estado_bulk(
    db: Session,
    registros_y_cambios: list[tuple[models.Registro, dict[str, Any]]],
    momento: Optional[datetime] = None,
):
    """
    Versión para cargas masivas (cargar_actas_semyt.py, llenar_actas_sigi.py,
    etc.) de `aplicar_cambios_estado`: recibe una lista de
    (registro, cambios) y aplica todos los cambios con UNA sola query de
    precarga de historial abierto, en vez de hasta 3 SELECTs por registro.

    Ejemplo:
        pares = [(registro, {"estado_semyt": nuevo_estado}) for registro, nuevo_estado in ...]
        aplicar_cambios_estado_bulk(db, pares)
        db.commit()

    No hace commit (igual que aplicar_cambios_estado): el caller decide
    cuándo comitear -- normalmente una vez cada tantos cientos de actas,
    no una por una.
    """
    if momento is None:
        momento = datetime.now()

    registro_ids = [registro.id for registro, _ in registros_y_cambios]
    cache = precargar_historial_abierto(db, registro_ids)

    for registro, cambios in registros_y_cambios:
        aplicar_cambios_estado(db, registro, cambios, momento=momento, historial_abierto_cache=cache)

    return [registro for registro, _ in registros_y_cambios]