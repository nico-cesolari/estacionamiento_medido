import re
from datetime import datetime, date, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

from . import models


# ---------------------------------------------------------------------------
# Patente: la gente la escribe de formas muy distintas (QHL790, QHL-790,
# AA000AA, AA-000-AA, con espacios, minúsculas, etc). Para que el filtro
# encuentre la misma patente sin importar el formato, comparamos versiones
# "normalizadas" (sólo letras y números, en mayúscula) tanto del dato
# guardado como de lo que escribió el usuario.
# ---------------------------------------------------------------------------

def _normalizar_patente_texto(valor: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", valor or "").upper()


def _columna_patente_normalizada():
    """Misma normalización que _normalizar_patente_texto, pero en SQL."""
    col = models.Registro.patente
    for caracter in ("-", " ", ".", "_"):
        col = func.replace(col, caracter, "")
    return func.upper(col)

def _normalizar_direccion_texto(valor: str) -> str:
    return re.sub(r"\s+", " ", (valor or "").strip()).upper()

def _columna_direccion_normalizada():
    """Misma normalización que _normalizar_direccion_texto, pero en SQL."""
    return func.upper(func.trim(models.Registro.direccion))


# ---------------------------------------------------------------------------
# Exportación de actas a reporte .txt (pantalla "Exportar Actas")
# ---------------------------------------------------------------------------
# Diccionario central: qué campos del acta se pueden usar como filtro del
# reporte, de qué tipo son (para saber cómo comparar el valor) y cómo se
# llaman de cara al usuario. Si mañana se agrega un campo nuevo al modelo,
# alcanza con sumarlo acá para que aparezca en "Exportar Actas".
CAMPOS_EXPORTABLES = {
    "juzgado": {"tipo": "numero", "columna": models.Registro.juzgado, "etiqueta": "Juzgado"},
    "expediente": {"tipo": "texto", "columna": models.Registro.expediente, "etiqueta": "Nº Expediente"},
    "acta": {"tipo": "texto", "columna": models.Registro.acta, "etiqueta": "Nº Acta"},
    "causa": {"tipo": "texto", "columna": models.Registro.causa, "etiqueta": "Nº Causa"},
    "patente": {"tipo": "texto", "columna": models.Registro.patente, "etiqueta": "Patente"},
    "direccion": {"tipo": "texto", "columna": models.Registro.direccion, "etiqueta": "Dirección"},
    "estado_sigemi": {"tipo": "estado", "columna": models.Registro.estado_sigemi, "etiqueta": "Estado SIGEMI"},
    "motivo_archivo_sigemi": {"tipo": "estado", "columna": models.Registro.motivo_archivo_sigemi, "etiqueta": "Motivo de archivo (SIGEMI)"},
    "estado_semyt": {"tipo": "estado", "columna": models.Registro.estado_semyt, "etiqueta": "Estado SEMyT"},
    "estado_sigi": {"tipo": "estado", "columna": models.Registro.estado_sigi, "etiqueta": "Estado SIGI"},
    "motivo_archivo_sigi": {"tipo": "estado", "columna": models.Registro.motivo_archivo_sigi, "etiqueta": "Motivo de archivo (SIGI)"},
    "fecha_hora": {"tipo": "fecha", "columna": models.Registro.fecha_hora, "etiqueta": "Fecha y hora del acta"},
    "fecha_cobro_sigi": {"tipo": "fecha", "columna": models.Registro.fecha_cobro_sigi, "etiqueta": "Fecha de cobro SIGI"},
    "fecha_cobro_sigemi": {"tipo": "fecha", "columna": models.Registro.fecha_cobro_sigemi, "etiqueta": "Fecha de cobro SIGEMI"},
}

# Campos de texto donde conviene comparar la versión normalizada (sin
# guiones/espacios) en lugar de la cadena literal. Por ahora sólo patente,
# que es la que tiene formatos variables (QHL-790, AA-000-AA, etc).
CAMPOS_TEXTO_NORMALIZADOS = {"patente"}

# MOTIVO DE ARCHIVO
_VALORES_MOTIVO_SIGEMI = {e.value for e in models.MotivoArchivoSigemi}
_VALORES_MOTIVO_SIGI = {e.value for e in models.MotivoArchivoSigi}

def _aplicar_rango_fechas(query, columna, fecha_desde: Optional[date], fecha_hasta: Optional[date]):
    """
    Filtra `columna` (un DateTime) entre fecha_desde y fecha_hasta, ambos
    inclusive, tomando el día completo (00:00 a 23:59:59) en fecha_hasta.
    Cualquiera de los dos puede venir solo.
    """
    if fecha_desde:
        query = query.filter(columna >= datetime.combine(fecha_desde, datetime.min.time()))
    if fecha_hasta:
        siguiente = datetime.combine(fecha_hasta, datetime.min.time()) + timedelta(days=1)
        query = query.filter(columna < siguiente)
    return query


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
    query = db.query(models.Registro)

    if estado_sigemi:
        query = query.filter(models.Registro.estado_sigemi == estado_sigemi)
    if estado_semyt:
        query = query.filter(models.Registro.estado_semyt == estado_semyt)
    if estado_sigi:
        query = query.filter(models.Registro.estado_sigi == estado_sigi)
    if motivo_archivo:
        condiciones_motivo = []
        if motivo_archivo in _VALORES_MOTIVO_SIGEMI:
            condiciones_motivo.append(
                models.Registro.motivo_archivo_sigemi == models.MotivoArchivoSigemi(motivo_archivo)
            )
        if motivo_archivo in _VALORES_MOTIVO_SIGI:
            condiciones_motivo.append(
                models.Registro.motivo_archivo_sigi == models.MotivoArchivoSigi(motivo_archivo)
            )
        if condiciones_motivo:
            query = query.filter(or_(*condiciones_motivo))
    if juzgado:
        query = query.filter(models.Registro.juzgado == juzgado)
    if expediente:
        query = query.filter(models.Registro.expediente.ilike(expediente))
    if acta:
        query = query.filter(models.Registro.acta.ilike(acta))
    if causa:
        query = query.filter(models.Registro.causa.ilike(causa))
    if patente:
        patente_norm = _normalizar_patente_texto(patente)
        query = query.filter(_columna_patente_normalizada().ilike(f"%{patente_norm}%"))
    if solo_duplicadas:
        query = query.filter(models.Registro.acta.in_(_query_actas_duplicadas(db)))
    if solo_reescritas:
        query = query.filter(models.Registro.reescrita.is_(True))
    if consistencia == "SI":
        query = query.filter(models.Registro.consistente.is_(True))
    elif consistencia == "NO":
        query = query.filter(models.Registro.consistente.is_(False))
    elif consistencia == "PENDIENTE":
        query = query.filter(models.Registro.consistente.is_(None))

    query = _aplicar_rango_fechas(query, models.Registro.fecha_hora, fecha_desde, fecha_hasta)

    query = query.order_by(
        models.Registro.fecha_hora.desc().nullslast(),
        models.Registro.id.desc(),
    )

    # Contamos y paginamos en SQL. Python sólo procesa las `page_size` filas
    # de la página pedida, no el dataset filtrado completo -- así una tabla
    # de 161k filas responde en el mismo tiempo estés en la página 1 o en
    # la última, sea cual sea el filtro (incluida consistencia).
    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    inicio = (page - 1) * page_size
    resultados = query.offset(inicio).limit(page_size).all()
    anotar_duplicadas(db, resultados)
    # anotar_consistencia queda como red de seguridad: recalcula sobre las
    # `page_size` filas de esta página (barato) por si la columna quedó
    # desactualizada en algún registro (ej. importado por un camino viejo
    # que no pasó por aplicar_cambios_estado). No vuelve a tocar toda la tabla.
    anotar_consistencia(resultados)
    return resultados, total, total_pages


# ---------------------------------------------------------------------------
# Punto único para aplicar cambios de estado sobre un Registro.
#
# Antes había DOS lugares separados tocando estado_sigemi/estado_semyt/
# estado_sigi cada uno con su propia lógica: el PATCH del frontend (acá,
# manejaba fecha_cobro y limpieza de motivo_archivo) y el import de SIGEMI
# vía archivo (ActasService.actualizar_actas_desde_sigemi, que pisaba
# `estado_sigemi` directo con setattr sin ninguno de esos efectos y sin
# dejar ningún rastro de cuándo había cambiado el estado).
#
# `aplicar_cambios_estado` reemplaza a los dos: es la ÚNICA función que
# escribe estado_sigemi/estado_semyt/estado_sigi/motivo_archivo_* sobre un
# Registro, la use quien la use (PATCH manual, import de archivo SIGEMI,
# migración inicial). Así los efectos (fecha de cobro, limpieza de motivo,
# y ahora también el historial con fecha de inicio/fin) son siempre los
# mismos sin importar el origen del cambio.
# ---------------------------------------------------------------------------

CAMPO_A_SISTEMA = {
    "estado_sigemi": models.SistemaEstado.sigemi,
    "estado_semyt": models.SistemaEstado.semyt,
    "estado_sigi": models.SistemaEstado.sigi,
}

# Para SIGEMI y SIGI el motivo de archivo viaja junto con el estado en el
# mismo renglón de historial; SEMyT no tiene motivo.
CAMPO_MOTIVO_DE = {
    "estado_sigemi": "motivo_archivo_sigemi",
    "estado_sigi": "motivo_archivo_sigi",
}


def _valor_enum(v):
    """Un Enum -> su .value (texto); cualquier otra cosa (o None) se devuelve tal cual."""
    return v.value if hasattr(v, "value") else v


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

        # Si se marca como Pagada manualmente, y todavía no tiene fecha de
        # cobro (ej: no vino de una migración real), le ponemos la fecha
        # de hoy. Si se desmarca, limpiamos la fecha.
        if campo == "estado_sigemi":
            if valor == models.EstadoSigemi.pagada and registro.fecha_cobro_sigemi is None:
                registro.fecha_cobro_sigemi = momento
            elif valor != models.EstadoSigemi.pagada:
                registro.fecha_cobro_sigemi = None
                
            if valor not in (models.EstadoSigemi.archivada, models.EstadoSigemi.resuelta_sin_archivo):
                registro.motivo_archivo_sigemi = None

        if campo == "estado_sigi" and valor != models.EstadoSigi.archivada:
            registro.motivo_archivo_sigi = None
            registro.fecha_cobro_sigi = None

        # Igual que con estado_sigemi/estado_semyt: si se marca el motivo de
        # archivo SIGI como "Pagada" y todavía no tiene fecha de cobro, se
        # completa con la fecha de hoy; si se cambia a otro motivo (o se
        # limpia), se borra la fecha.
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
    registros_y_cambios: List[tuple],
    momento: Optional[datetime] = None,
):
    """
    Versión para cargas masivas (cargar_actas_semyt.py, llenar_actas_sigi.py,
    etc.) de `aplicar_cambios_estado`: recibe una lista de
    (registro, cambios) y aplica todos los cambios con UNA sola query de
    precarga de historial abierto, en vez de hasta 3 SELECTs por registro.

    Ejemplo:
        pares = [(registro, {"estado_semyt": nuevo_estado}) for registro, nuevo_estado in ...]
        crud.aplicar_cambios_estado_bulk(db, pares)
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

# ---------------------------------------------------------------------------
# Actas duplicadas: NO es un estado persistido ni un valor de ningún Enum.
# Se calcula al vuelo contando cuántas filas de `Registro` comparten la
# misma `acta`. Esto permite detectar y filtrar el caso "misma acta, dos
# expedientes distintos" sin tocar EstadoSemyt/EstadoSigi ni el modelo.
# ---------------------------------------------------------------------------

def _query_actas_duplicadas(db: Session):
    """
    Query (no subquery ya "cerrada") de los valores de `acta` que aparecen
    en más de una fila. Se pasa tal cual a `.in_()`, que la usa como
    subquery escalar con un solo nivel de anidamiento -- llamar acá
    `.subquery()` de más termina generando un SELECT envolviendo a otro
    SELECT sin necesidad.
    """
    return (
        db.query(models.Registro.acta)
        .filter(models.Registro.acta.isnot(None))
        .group_by(models.Registro.acta)
        .having(func.count(models.Registro.id) > 1)
    )


def anotar_duplicadas(db: Session, registros: List["models.Registro"]):
    """
    Agrega a cada registro (atributo en memoria, no columna de DB) el flag
    `es_duplicada = True/False` según si existe otra fila con la misma acta.
    """
    actas = {r.acta for r in registros if r.acta}
    conteos = {}
    if actas:
        conteos = dict(
            db.query(models.Registro.acta, func.count(models.Registro.id))
            .filter(models.Registro.acta.in_(actas))
            .group_by(models.Registro.acta)
            .all()
        )
    for r in registros:
        r.es_duplicada = bool(r.acta and conteos.get(r.acta, 0) > 1)

# ---------------------------------------------------------------------------
# Actas "reescritas": mismo vehículo + mismo día de labrado + misma
# dirección, pero con distinto número de acta (y, normalmente, distinto
# expediente) -- a diferencia de `es_duplicada` arriba, que detecta el
# mismo número de acta repetido (duplicado literal).
#
# Se calcula en batch (ver calcular_actas_reescritas.py) y se persiste en
# `Registro.reescrita` / `Registro.grupo_reescritura`, en vez de calcularse
# por página como `anotar_duplicadas`: agrupar por patente+día+dirección
# sobre 160k+ filas en cada request de la grilla sería mucho más caro que
# comparar por `acta` (que ya está indexada 1 a 1). Al persistirlo, filtrar
# por "reescritas" en la grilla es tan rápido como filtrar por
# `consistente`.
# ---------------------------------------------------------------------------

TAMANO_LOTE_REESCRITURAS = 1000

def _query_grupos_reescritos(db: Session):
    """
    Query (fila por grupo) de patente normalizada + día + dirección
    normalizada que aparecen en más de una fila CON al menos dos números
    de acta distintos entre sí. Esa segunda condición es la que separa
    "reescrita" de "duplicado literal": si las dos filas del grupo tienen
    exactamente la misma acta, es el caso que ya cubre `es_duplicada`/
    `solo_duplicadas`, no una reescritura.
    """
    patente_norm = _columna_patente_normalizada()
    dia = func.date(models.Registro.fecha_hora)
    direccion_norm = _columna_direccion_normalizada()

    return (
        db.query(patente_norm.label("patente_norm"), dia.label("dia"), direccion_norm.label("direccion_norm"))
        .filter(
            models.Registro.patente.isnot(None),
            models.Registro.fecha_hora.isnot(None),
            models.Registro.direccion.isnot(None),
            models.Registro.direccion != "",
        )
        .group_by(patente_norm, dia, direccion_norm)
        .having(func.count(models.Registro.id) > 1)
        .having(func.count(func.distinct(models.Registro.acta)) > 1)
    )


def _clave_grupo_reescritura(patente_norm: str, dia, direccion_norm: str) -> str:
    """Clave legible y estable para `Registro.grupo_reescritura` (no hace
    falta un hash: patente/día/dirección normalizados ya identifican al
    grupo sin ambigüedad, y de paso queda legible si se mira la tabla
    directo)."""
    return f"{patente_norm}|{dia.isoformat() if hasattr(dia, 'isoformat') else dia}|{direccion_norm}"


def calcular_actas_reescritas(db: Session, tamano_lote: int = TAMANO_LOTE_REESCRITURAS) -> dict:
    """
    Recalcula `reescrita`/`grupo_reescritura` para TODA la tabla:
      1) obtiene los grupos (patente+día+dirección) con reescritura real,
      2) trae todas las filas que pertenecen a esos grupos,
      3) marca reescrita=True + grupo_reescritura en esas filas,
      4) limpia (vuelve a None) las filas que estaban marcadas como
         reescritas antes pero ya no clasifican (ej: se borró una de las
         dos actas duplicadas).

    Hace commit en lotes (no todo en una transacción) para no tener un
    UPDATE gigante bloqueando la tabla en producción. Devuelve un resumen
    para el reporte del script ejecutable.
    """
    grupos = _query_grupos_reescritos(db).all()

    patente_norm_col = _columna_patente_normalizada()
    dia_col = func.date(models.Registro.fecha_hora)
    direccion_norm_col = _columna_direccion_normalizada()

    total_marcadas = 0
    detalle_grupos = []

    ids_afectados = set()

    for patente_norm, dia, direccion_norm in grupos:
        filas = (
            db.query(models.Registro)
            .filter(
                patente_norm_col == patente_norm,
                dia_col == dia,
                direccion_norm_col == direccion_norm,
            )
            .order_by(models.Registro.fecha_hora, models.Registro.id)
            .all()
        )
        clave = _clave_grupo_reescritura(patente_norm, dia, direccion_norm)
        for fila in filas:
            fila.reescrita = True
            fila.grupo_reescritura = clave
            ids_afectados.add(fila.id)
            total_marcadas += 1

        detalle_grupos.append({
            "patente": patente_norm,
            "dia": dia,
            "direccion": direccion_norm,
            "actas": [f.acta for f in filas],
            "expedientes": [f.expediente for f in filas],
        })

        if total_marcadas % tamano_lote < len(filas):
            db.commit()

    db.commit()

    # Limpieza: cualquier fila que hoy dice reescrita=True pero no está en
    # ids_afectados ya no corresponde a ningún grupo vigente (por ejemplo,
    # se borró una de las actas del grupo y ahora sólo queda una).
    query_desactualizadas = db.query(models.Registro).filter(models.Registro.reescrita.is_(True))
    if ids_afectados:
        query_desactualizadas = query_desactualizadas.filter(models.Registro.id.notin_(ids_afectados))

    total_limpiadas = 0
    for fila in query_desactualizadas.yield_per(tamano_lote):
        fila.reescrita = False
        fila.grupo_reescritura = None
        total_limpiadas += 1
        if total_limpiadas % tamano_lote == 0:
            db.commit()
    db.commit()

    return {
        "grupos_encontrados": len(grupos),
        "actas_marcadas": total_marcadas,
        "actas_desmarcadas": total_limpiadas,
        "detalle_grupos": detalle_grupos,
    }
    
def actualizar_estados(db: Session, registro_id: int, cambios: dict):
    """Wrapper para el PATCH del frontend: busca por id, aplica y comitea."""
    registro = db.query(models.Registro).filter(models.Registro.id == registro_id).first()
    if not registro:
        return None

    aplicar_cambios_estado(db, registro, cambios)

    db.commit()
    db.refresh(registro)
    anotar_consistencia([registro])
    return registro


def historial_de_registro(db: Session, registro_id: int):
    """Historial completo (los 3 sistemas mezclados) de un registro, del más viejo al más nuevo."""
    return (
        db.query(models.HistorialEstado)
        .filter(models.HistorialEstado.registro_id == registro_id)
        .order_by(models.HistorialEstado.fecha_inicio.asc(), models.HistorialEstado.id.asc())
        .all()
    )


def historial_de_registros(db: Session, registro_ids: List[int]):
    """Igual que historial_de_registro pero para varios registros a la vez (usado en el reporte)."""
    if not registro_ids:
        return []
    return (
        db.query(models.HistorialEstado)
        .filter(models.HistorialEstado.registro_id.in_(registro_ids))
        .order_by(
            models.HistorialEstado.registro_id.asc(),
            models.HistorialEstado.fecha_inicio.asc(),
            models.HistorialEstado.id.asc(),
        )
        .all()
    )


def obtener_registro(db: Session, registro_id: int):
    registro = db.query(models.Registro).filter(models.Registro.id == registro_id).first()
    if registro:
        anotar_consistencia([registro])
    return registro


def aplicar_filtros_avanzados(query, filtros: List[dict]):
    """
    Aplica una lista de filtros libres sobre la consulta, para la pantalla de
    "Exportar Actas". Cada filtro es {campo, modo, valor}:
      - campo: una clave de CAMPOS_EXPORTABLES
      - modo:  "coincide" (default) o "no_coincide"
      - valor: lo que el usuario escribió/eligió

    Texto  -> "coincide" es "contiene" (case-insensitive); "no_coincide" es
               "no contiene" (los registros con el campo vacío cuentan como
               que no coinciden). Para "patente" se compara la versión
               normalizada (sin guiones/espacios) de ambos lados.
    Estado -> comparación exacta / distinta.
    Fecha  -> el valor es una fecha (YYYY-MM-DD); "coincide" son las actas de
               ese día; "no_coincide" son todas las demás (incluidas las que
               no tienen esa fecha cargada).
    """
    for f in filtros:
        campo = f.get("campo")
        modo = f.get("modo") or "coincide"
        valor = (f.get("valor") or "").strip()

        info = CAMPOS_EXPORTABLES.get(campo)
        if not info or not valor:
            continue

        columna = info["columna"]
        tipo = info["tipo"]
        negar = modo == "no_coincide"

        if tipo == "texto":
            if campo in CAMPOS_TEXTO_NORMALIZADOS:
                columna_comparar = _columna_patente_normalizada() if campo == "patente" else columna
                valor_comparar = _normalizar_patente_texto(valor)
            else:
                columna_comparar = columna
                valor_comparar = valor
            positiva = columna_comparar.ilike(f"%{valor_comparar}%")
            condicion = or_(columna.is_(None), ~positiva) if negar else positiva

        elif tipo == "estado":
            condicion = (columna != valor) if negar else (columna == valor)

        elif tipo == "fecha":
            try:
                dia = datetime.strptime(valor, "%Y-%m-%d")
            except ValueError:
                continue
            siguiente = dia + timedelta(days=1)
            positiva = (columna >= dia) & (columna < siguiente)
            condicion = or_(columna.is_(None), ~positiva) if negar else positiva
        elif tipo == "numero":
            try:
                valor_num = int(valor)
            except ValueError:
                continue
            condicion = (columna != valor_num) if negar else (columna == valor_num)
        else:
            continue

        query = query.filter(condicion)

    return query


def contar_para_exportar(
    db: Session,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
) -> int:
    """
    Igual que buscar_para_exportar pero sin traer las filas ni calcular
    consistencia: sólo cuenta. Este endpoint se llama en cada tecla que se
    tipea en el formulario de filtros libres (con debounce), así que no
    tiene sentido traer todas las columnas de cada fila sólo para mostrar
    un número en pantalla.
    """
    query = db.query(models.Registro)
    query = aplicar_filtros_avanzados(query, filtros)
    query = _aplicar_rango_fechas(query, models.Registro.fecha_hora, fecha_desde, fecha_hasta)
    return query.count()


def buscar_para_exportar(
    db: Session,
    filtros: List[dict],
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
):
    """Trae TODAS las actas que matchean los filtros (sin paginar), para el reporte."""
    query = db.query(models.Registro)
    query = aplicar_filtros_avanzados(query, filtros)
    query = _aplicar_rango_fechas(query, models.Registro.fecha_hora, fecha_desde, fecha_hasta)
    resultados = (
        query.order_by(models.Registro.fecha_hora.desc().nullslast(), models.Registro.id.desc())
        .all()
    )
    anotar_consistencia(resultados)
    return resultados


# ---------------------------------------------------------------------------
# Consistencia entre SEMyT, SIGEMI y SIGI
# ---------------------------------------------------------------------------
# Los tres sistemas se reducen a un mismo resultado de negocio:
# PAGADA, RESUELTA o VENCIDA. La comparación se hace sobre esas categorías,
# no sobre el texto literal de cada estado.

def categoria_sigemi(estado_sigemi, motivo_archivo_sigemi):
    if estado_sigemi == models.EstadoSigemi.pagada:
        return "PAGADA"
    if estado_sigemi == models.EstadoSigemi.archivada:
        if motivo_archivo_sigemi == models.MotivoArchivoSigemi.por_pago:
            return "PAGADA"
        if motivo_archivo_sigemi is not None:
            return "RESUELTA"
        return None
    if estado_sigemi == models.EstadoSigemi.resuelta_sin_archivo:
        return "RESUELTA"
    # archivada_sin_resolucion: SIGEMI la marca archivada pero también como
    # no resuelta (inconsistencia del propio sistema de origen) -- para la
    # comparación contra SEMyT/SIGI se trata como VENCIDA (el trámite en
    # los hechos sigue sin estar resuelto), no como RESUELTA/PAGADA. Mismo
    # criterio que el color que se le da en el frontend.
    if estado_sigemi in (
        models.EstadoSigemi.sin_resolucion,
        models.EstadoSigemi.en_procuracion,
        models.EstadoSigemi.archivada_sin_resolucion,
    ):
        return "VENCIDA"
    return None


def categoria_semyt(estado_semyt):
    if estado_semyt == models.EstadoSemyt.pagada_en_juzgado:
        return "PAGADA"
    if estado_semyt in (models.EstadoSemyt.resuelta_en_juzgado, models.EstadoSemyt.rechazada):
        return "RESUELTA"
    if estado_semyt == models.EstadoSemyt.vencida:
        return "VENCIDA"
    return None

def categoria_sigi(estado_sigi, motivo_archivo_sigi):
    if estado_sigi == models.EstadoSigi.archivada:
        if motivo_archivo_sigi == models.MotivoArchivoSigi.por_pago:
            return "PAGADA"

        if motivo_archivo_sigi is not None:
            return "RESUELTA"

        return None

    if estado_sigi in (
        models.EstadoSigi.sin_notificar,
        models.EstadoSigi.notificada,
        models.EstadoSigi.resolucion_pendiente,
        models.EstadoSigi.pago_pendiente_con_resolucion,
        models.EstadoSigi.descargo_presentado,
        models.EstadoSigi.pre_judicial,
    ):
        return "VENCIDA"

    return None


_ETIQUETA_CATEGORIA = {
    "PAGADA": "pagada",
    "RESUELTA": "resuelta",
    "VENCIDA": "vencida",
}


def _sigemi_ignorable(registro):
    """
    SIGEMI se ignora en la comparación (no bloquea, no exige nada) cuando
    no tiene ningún dato útil todavía: no cargado, o "Archivada" sin que
    se haya elegido motivo_archivo_sigemi (a esta altura, archivada sin
    motivo es lo mismo que no tener nada: no se sabe si fue por pago o
    por otra causa).
    """
    if registro.estado_sigemi in (None, models.EstadoSigemi.no_cargada):
        return True
    if registro.estado_sigemi == models.EstadoSigemi.archivada and registro.motivo_archivo_sigemi is None:
        return True
    return False


def _sigi_ignorable(registro):
    """Mismo criterio que _sigemi_ignorable, para SIGI."""
    if registro.estado_sigi in (None, models.EstadoSigi.no_cargada):
        return True
    if registro.estado_sigi == models.EstadoSigi.archivada and registro.motivo_archivo_sigi is None:
        return True
    return False


def calcular_consistencia(registro):
    """
    SEMyT siempre se exige cargado. SIGEMI y SIGI, en cambio, pueden estar
    "ignorables" (sin cargar, o Archivada sin motivo todavía elegido) --
    en ese caso no bloquean la consistencia, se comparan sólo los sistemas
    que sí tienen categoría. Si lo que sí está cargado coincide, se marca
    consistente igual, aunque falte SIGEMI y/o SIGI.

    Única excepción: si entre lo cargado aparece VENCIDA (el trámite sigue
    activo) y puntualmente SIGI está ignorable, eso SÍ es una
    inconsistencia real -- SIGI es el sistema vigente y se espera que
    tenga reflejado un trámite todavía activo. Esto no aplica a SIGEMI: si
    sólo SIGEMI está sin cargar y SIGI coincide con SEMyT, no hay problema.
    """
    categorias = {}
    faltantes = []
    ignorados = []

    cat_semyt = categoria_semyt(registro.estado_semyt)
    if cat_semyt is None:
        faltantes.append("SEMyT")
    else:
        categorias["SEMyT"] = cat_semyt

    if _sigemi_ignorable(registro):
        ignorados.append("SIGEMI")
    else:
        cat_sigemi = categoria_sigemi(registro.estado_sigemi, registro.motivo_archivo_sigemi)
        if cat_sigemi is None:
            faltantes.append("SIGEMI")
        else:
            categorias["SIGEMI"] = cat_sigemi

    if _sigi_ignorable(registro):
        ignorados.append("SIGI")
    else:
        cat_sigi = categoria_sigi(registro.estado_sigi, registro.motivo_archivo_sigi)
        if cat_sigi is None:
            faltantes.append("SIGI")
        else:
            categorias["SIGI"] = cat_sigi

    if faltantes:
        return None

    # categorias siempre tiene al menos SEMyT acá (si faltara, ya se
    # devolvió arriba).

    if "VENCIDA" in categorias.values() and "SIGI" in ignorados:
        return False

    valores = set(categorias.values())
    if len(valores) == 1:
        return True
    return False

def anotar_consistencia(registros):
    for r in registros:
        r.consistente = calcular_consistencia(r)

def _fmt_fecha(fecha):
    if not fecha:
        return ""
    return fecha.strftime("%d/%m/%Y %H:%M")


def generar_reporte_txt(registros, filtros: List[dict], fecha_desde: Optional[date] = None, fecha_hasta: Optional[date] = None) -> str:
    """Genera un reporte compacto, delimitado por |, con una fila por acta."""
    columnas = [
        "JUZGADO","EXPEDIENTE", "ACTA_NUMERO", "CAUSA_NUMERO", "PATENTE", "DIRECCION",
        "FECHA_LABRADA", "ESTADO_SIGEMI", "MOTIVO_ARCHIVO_SIGEMI",
        "ESTADO_SEMYT", "ESTADO_SIGI", "MOTIVO_ARCHIVO_SIGI",
        "CONSISTENCIA", "FECHA_COBRO_SIGI","FECHA_COBRO_SIGEMI",
    ]

    def limpiar(valor):
        if valor is None:
            return ""

        texto = str(valor).strip()

        if texto in ("None", "null", "nan", "NaN", "-"):
            return ""

        return (
            texto
            .replace("|", "/")
            .replace("\n", " ")
            .replace("\r", " ")
        )

    def estado(valor):
        return valor.value if hasattr(valor, "value") else (valor or "")

    lineas = ["|".join(columnas)]

    for r in registros:
        consistente, detalle = calcular_consistencia(r)
        if consistente is True:
            consistencia = "CONSISTENTE"
        elif consistente is False:
            consistencia = "INCONSISTENTE"
        else:
            consistencia = "PENDIENTE"

        fila = [
            r.juzgado,
            r.expediente,
            r.acta,
            r.causa,
            r.patente,
            r.direccion,
            _fmt_fecha(r.fecha_hora),
            estado(r.estado_sigemi),
            estado(r.motivo_archivo_sigemi),
            estado(r.estado_semyt),
            estado(r.estado_sigi),
            estado(r.motivo_archivo_sigi),
            consistencia,
            _fmt_fecha(r.fecha_cobro_sigi),
            _fmt_fecha(r.fecha_cobro_sigemi),
        ]
        lineas.append("|".join(limpiar(v) for v in fila))

    return "\n".join(lineas) + "\n"

def buscar_acta_por_numero(db, numero_acta):
    return (
        db.query(models.Acta)
        .filter(models.Acta.numero_acta == numero_acta)
        .first()
    )