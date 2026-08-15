"""
Reglas de negocio para la sincronización con SIGI
(https://juzgado.villamaria.gob.ar/juzgado).

Este módulo es el único lugar donde viven:
  - el mapeo entre el texto que muestra SIGI (estado, motivo de archivo) y
    los Enums de nuestra app,
  - la normalización del número de acta,
  - las consultas a la base que definen qué registros le corresponden a
    cada paso (alta vs. actualización),
  - la decisión de qué escribir en la base a partir de lo leído en SIGI.

NO tiene nada de Playwright / selectores / navegador: eso vive en
backend/alta/llenar_actas_sigi.py y backend/update/actualizar_actas_sigi.py,
que importan este módulo. Si SIGI cambia el texto de un estado, o cambia
una regla de negocio, se corrige acá y no en el código que maneja el
browser.
"""
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ...app import crud, models


# ---------------------------------------------------------------------------
# Número de acta: SIGI lo muestra con puntos de miles ("351.937"); en
# nuestra base se guarda sin separadores ("351937"). Toda lectura de
# pantalla tiene que pasar por acá antes de comparar/buscar.
# ---------------------------------------------------------------------------
REGEX_NUMERO_CON_PUNTO = re.compile(r"\d{1,3}(?:\.\d{3})+")


def normalizar_acta(valor: Optional[str]) -> str:
    """Deja sólo letras y números, en mayúscula. '351.937', '351937' y
    '351-937' terminan siendo la misma acta a la hora de comparar."""
    return re.sub(r"[^A-Za-z0-9]", "", str(valor or "")).upper()


def normalizar_expediente(valor: Optional[str]) -> str:
    """Sólo recorta espacios -- el expediente de SIGI (ej. 'EXP-2026-123123')
    se compara tal cual, no como el acta (no tiene formato con puntos que
    haga falta despojar)."""
    return str(valor or "").strip()


def extraer_numero_acta_de_texto(texto: str) -> Optional[str]:
    """
    Busca en un bloque de texto (el body de la pestaña 'Actas' del
    detalle) el primer número con formato de puntos de miles, que es como
    SIGI muestra 'Número acta' (ej. '351.937'). Devuelve None si no
    encuentra ninguno.
    """
    coincidencia = REGEX_NUMERO_CON_PUNTO.search(texto or "")
    return coincidencia.group(0) if coincidencia else None


# ---------------------------------------------------------------------------
# Patente / dirección / fecha y hora: se leen del mismo bloque de texto
# que el número de acta (pestaña 'Actas' del detalle), para el caso en
# que el acta no exista todavía en la base y haya que darla de alta con
# estos datos (ver crear_registro_nuevo_por_acta). El layout confirmado
# por captura real es "etiqueta arriba, valor abajo" (ej. una línea
# 'Dirección' seguida de la línea 'Mendoza 700-800'), así que se lee por
# posición de línea en el texto plano en vez de por selector CSS -- es lo
# mismo que ya se venía haciendo para el número de acta, y es más
# resistente a cambios de maquetado que un selector.
# ---------------------------------------------------------------------------
REGEX_FECHA_HORA_ACTA = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4})\s*[—-]\s*(\d{1,2}):(\d{2})\s*hs",
    re.IGNORECASE,
)


def _valor_tras_etiqueta(texto: str, etiqueta: str) -> Optional[str]:
    """Busca una línea que sea (sin importar mayúsculas/minúsculas)
    exactamente `etiqueta`, y devuelve el contenido de la primera línea
    no vacía que aparece después. Así se lee cualquier campo con formato
    'etiqueta arriba, valor abajo' del detalle de SIGI."""
    if not texto:
        return None
    lineas = [linea.strip() for linea in texto.splitlines()]
    etiqueta_norm = etiqueta.strip().casefold()
    for i, linea in enumerate(lineas):
        if linea.casefold() == etiqueta_norm:
            for siguiente in lineas[i + 1:]:
                if siguiente:
                    return siguiente
    return None


def extraer_patente_de_texto(texto: str) -> Optional[str]:
    """Lee el campo 'Identificación' de la sección 'Bien identificado'
    (la patente del vehículo, ej. 'AF023KR'). AJUSTAR si en SIGI aparece
    más de un campo llamado 'Identificación' en la misma pantalla -- por
    ahora, según captura real, es único."""
    return _valor_tras_etiqueta(texto, "Identificación")


def extraer_direccion_de_texto(texto: str) -> Optional[str]:
    """Lee el campo 'Dirección' de la sección 'Datos del acta'."""
    return _valor_tras_etiqueta(texto, "Dirección")


def extraer_fecha_hora_de_texto(texto: str) -> Optional[datetime]:
    """Lee el encabezado del bloque de acta (ej. 'Estacionamiento Medido
    · 10/4/2026 — 11:32 hs') y devuelve la fecha/hora como datetime.
    None si no aparece o el formato no es el esperado (dd/mm/aaaa)."""
    if not texto:
        return None
    coincidencia = REGEX_FECHA_HORA_ACTA.search(texto)
    if not coincidencia:
        return None
    fecha_str, hora_str, minuto_str = coincidencia.groups()
    try:
        dia, mes, anio = (int(x) for x in fecha_str.split("/"))
        return datetime(anio, mes, dia, int(hora_str), int(minuto_str))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Estado y motivo de archivo: texto de pantalla -> Enum de la app.
# ---------------------------------------------------------------------------
MAPA_ESTADO_SIGI = {
    "Sin Notificar": models.EstadoSigi.sin_notificar,
    "citado": models.EstadoSigi.notificada,
    "Resolución Pendiente": models.EstadoSigi.resolucion_pendiente,
    "Pago Pendiente con Resolución": models.EstadoSigi.pago_pendiente_con_resolucion,
    "Descargo presentado": models.EstadoSigi.descargo_presentado,
    "Prejudicial": models.EstadoSigi.pre_judicial,
    "Archivada": models.EstadoSigi.archivada,
}

MAPA_MOTIVO_SIGI = {
    "Pagada": models.MotivoArchivoSigi.por_pago,
    "Desestimación": models.MotivoArchivoSigi.por_desestimacion,
    "Amonestación": models.MotivoArchivoSigi.por_amonestacion,
    "Sobreseimiento": models.MotivoArchivoSigi.por_sobreseimiento,
    "Suspensión": models.MotivoArchivoSigi.suspendida,
}

# Confirmado contra models.py: models.EstadoSigi.no_cargada = "No Cargada".
NOMBRE_ESTADO_NO_CARGADA = "no_cargada"


def mapear_estado(texto: Optional[str]) -> Optional[models.EstadoSigi]:
    """Traduce el texto de pantalla (badge / columna ESTADO) al Enum
    EstadoSigi. Devuelve None si el texto no coincide con ninguno conocido."""
    if not texto:
        return None
    return MAPA_ESTADO_SIGI.get(texto.strip())


def mapear_motivo(texto: Optional[str]) -> Optional[models.MotivoArchivoSigi]:
    """Traduce el texto de motivo de archivo al Enum MotivoArchivoSigi."""
    if not texto:
        return None
    return MAPA_MOTIVO_SIGI.get(texto.strip())


def estado_no_cargada() -> Optional[models.EstadoSigi]:
    """models.EstadoSigi.no_cargada, resuelto con getattr como red de
    seguridad por si algún día cambia el nombre del atributo en el enum."""
    estado = getattr(models.EstadoSigi, NOMBRE_ESTADO_NO_CARGADA, None)
    if estado is None:
        print(
            f"[SIGI] AVISO: no existe models.EstadoSigi.{NOMBRE_ESTADO_NO_CARGADA}; "
            f"revisar si cambió el nombre del atributo en el enum."
        )
    return estado


def armar_cambios_estado(estado_texto: Optional[str], motivo_texto: Optional[str] = None) -> Optional[dict]:
    """
    A partir de lo leído en pantalla, arma el dict de cambios que espera
    crud.aplicar_cambios_estado. El motivo sólo se incluye cuando el
    estado es 'Archivada' Y se pudo mapear (si SIGI no muestra motivo para
    un acta archivada, igual se actualiza el estado, sin motivo).

    Devuelve None si el estado no se pudo mapear (texto desconocido o vacío).
    """
    nuevo_estado = mapear_estado(estado_texto)
    if nuevo_estado is None:
        return None

    cambios = {"estado_sigi": nuevo_estado}
    if nuevo_estado == models.EstadoSigi.archivada:
        motivo = mapear_motivo(motivo_texto)
        if motivo is not None:
            cambios["motivo_archivo_sigi"] = motivo
    return cambios


def armar_datos_alta_por_acta(estado_texto: Optional[str], motivo_texto: Optional[str] = None) -> dict:
    """
    Para cargar_actas_sigi.py: arma estado_sigi/motivo_archivo_sigi para
    un registro NUEVO (acta que todavía no existía en la base).

    A diferencia de armar_cambios_estado (pensada para actualizar un
    registro ya existente, donde si el texto no se pudo mapear se
    devuelve None y no se toca nada), acá el alta se hace igual aunque
    el estado no se haya podido mapear -- se cae a 'No Cargada', porque
    ya se leyeron bien expediente/patente/dirección/fecha y no tiene
    sentido perder esos datos por un texto de estado desconocido.
    """
    cambios = armar_cambios_estado(estado_texto, motivo_texto)
    if cambios is not None:
        return cambios
    estado = estado_no_cargada()
    return {"estado_sigi": estado} if estado is not None else {}


def crear_registro_nuevo_por_acta(
    db: Session,
    *,
    expediente: Optional[str],
    acta: str,
    patente: str,
    direccion: Optional[str],
    fecha_hora,
    estado_texto: Optional[str],
    motivo_texto: Optional[str] = None,
) -> "models.Registro":
    """
    Para cargar_actas_sigi.py: crea un Registro nuevo para un acta que
    apareció en SIGI pero no existía todavía en la base (no hizo match
    contra ningún registro local). No hace commit -- eso queda a cargo
    de quien orquesta la corrida, igual que aplicar_actualizacion.

    estado_semyt se fija siempre en 'Eliminada': un acta que se da de
    alta por acá nunca pasó por SEMyT, así que ese estado no aplica
    (decisión de negocio explícita, no un default técnico).
    """
    datos_estado = armar_datos_alta_por_acta(estado_texto, motivo_texto)
    registro = models.Registro(
        expediente=expediente,
        acta=normalizar_acta(acta),
        patente=patente,
        direccion=direccion,
        fecha_hora=fecha_hora,
        estado_semyt=models.EstadoSemyt.eliminada,
        **datos_estado,
    )
    db.add(registro)
    return registro


def hay_cambio_real(registro: "models.Registro", cambios: dict) -> bool:
    """
    True si aplicar `cambios` modificaría algo del registro. Evita pisar
    (y generar historial de más para) un estado/motivo que ya está
    guardado tal cual.
    """
    if registro.estado_sigi != cambios.get("estado_sigi"):
        return True
    if "motivo_archivo_sigi" in cambios and registro.motivo_archivo_sigi != cambios["motivo_archivo_sigi"]:
        return True
    return False


def clonar_registro(registro: "models.Registro") -> "models.Registro":
    """Crea una instancia NUEVA (sin PK) con los mismos datos que
    `registro`, para el caso en que una misma acta aparezca en SIGI
    asociada a más de un expediente: el primero pisa el registro
    'pendiente' original, y para los siguientes hace falta un registro
    aparte (mismo acta/patente, expediente y estado distintos).

    (Antes vivía sólo en llenar_actas_sigi.py; se movió acá porque
    sincronizar_actas_sigi.py necesita exactamente la misma lógica y no
    tiene sentido tener dos copias que se puedan desincronizar.)"""
    from sqlalchemy import inspect as sa_inspect
    Modelo = type(registro)
    mapper = sa_inspect(Modelo)
    columnas_pk = {c.name for c in mapper.primary_key}
    datos = {
        col.key: getattr(registro, col.key)
        for col in mapper.column_attrs
    }
    return Modelo(**datos)


# ---------------------------------------------------------------------------
# Qué registros le tocan a cada paso.
# ---------------------------------------------------------------------------
def todos_los_expedientes_cargados(db: Session) -> dict:
    """Para sincronizar_actas_sigi.py: TODOS los registros que ya tienen
    expediente (incluidas las archivadas -- acá SÍ importan, porque son
    justamente las que hay que reconocer como "ya cargada" y saltear sin
    abrir detalle; excluirlas llevaría a tratarlas como desconocidas y
    buscarlas de nuevo por acta en cada corrida).

    Devuelve {expediente_normalizado: Registro}. Si dos registros
    comparten expediente (no debería pasar, pero por las dudas) gana el
    último leído -- no es el uso pensado de esta función."""
    registros = (
        db.query(models.Registro)
        .filter(models.Registro.expediente.isnot(None), models.Registro.expediente != "")
        .all()
    )
    return {normalizar_expediente(r.expediente): r for r in registros}


def todas_las_actas_conocidas(db: Session) -> dict:
    """Para sincronizar_actas_sigi.py: TODOS los registros, agrupados por
    acta normalizada -- tengan o no expediente ya cargado. Hace falta
    así de amplio porque una misma acta puede tener más de un expediente
    asociado (ver clonar_registro), y porque un registro sin expediente
    es justamente el candidato a completar cuando se encuentra la
    coincidencia en SIGI.

    Devuelve {acta_normalizada: [Registro, ...]}."""
    registros = db.query(models.Registro).all()
    agrupado: dict[str, list] = {}
    for r in registros:
        clave = normalizar_acta(r.acta)
        if not clave:
            continue
        agrupado.setdefault(clave, []).append(r)
    return agrupado


def registros_con_expediente_pendientes(db: Session):
    """Para actualizar_actas_sigi.py (camino viejo, por expediente
    individual): ya tienen expediente, sólo falta revisar si el estado
    cambió. Se excluyen las ya archivadas (estado terminal: no hace falta
    seguir consultándolas en cada corrida)."""
    return (
        db.query(models.Registro)
        .filter(
            models.Registro.expediente.isnot(None),
            models.Registro.expediente != "",
            models.Registro.estado_sigi != models.EstadoSigi.archivada,
        )
        .all()
    )


def registros_sin_expediente(db: Session):
    """Para llenar_actas_sigi.py: todavía no pasaron por SIGI ni una vez."""
    return (
        db.query(models.Registro)
        .filter(
            (models.Registro.expediente.is_(None)) | (models.Registro.expediente == "")
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Escritura en la base (siempre a través de crud.aplicar_cambios_estado,
# para que quede reflejada en HistorialEstado). No hace commit: eso queda
# a cargo de quien orquesta la corrida (script standalone o
# procesar_actas_sigi.py), para poder comitear una sola vez al final.
# ---------------------------------------------------------------------------
def aplicar_actualizacion(db: Session, registro: "models.Registro", cambios: dict) -> None:
    crud.aplicar_cambios_estado(db, registro, cambios)


def marcar_sin_coincidencia(db: Session, registro: "models.Registro") -> bool:
    """
    Ninguna fila de la búsqueda por patente coincidió con el acta que
    buscábamos: se marca estado_sigi = no_cargada (si no lo tenía ya) y el
    expediente queda sin tocar. Devuelve True si hubo cambio.
    """
    estado = estado_no_cargada()
    if estado is None or registro.estado_sigi == estado:
        return False
    aplicar_actualizacion(db, registro, {"estado_sigi": estado})
    return True


def desvincular_expediente_no_encontrado(db: Session, registro: "models.Registro") -> bool:
    """
    Para actualizar_actas_sigi.py: el registro YA tenía expediente, pero
    al buscarlo en SIGI (filtro 'Número de expediente') no aparece
    ninguna fila -- el expediente dejó de existir del lado de SIGI (se
    anuló, se corrigió un error de tipeo, etc.). Le sacamos el expediente
    y lo dejamos en 'No Cargada': vuelve a quedar disponible para que
    llenar_actas_sigi.py lo re-busque por patente en la próxima corrida,
    en vez de seguir intentando actualizar algo que ya no existe.

    OJO CON DUPLICADOS: si esta acta tiene más de un registro (dos
    expedientes distintos para la misma acta -- ver crud.anotar_duplicadas
    / _clonar_registro en llenar_actas_sigi.py), esta función sólo toca
    EL registro puntual que se le pasó. Nunca hay que iterar "todos los
    registros con esta acta" acá: cada expediente se valida por separado,
    y un expediente inválido en un registro no dice nada sobre si el
    expediente del registro hermano sigue siendo válido o no.

    No se borra la fila (no se pierde patente/fecha_hora/dirección/
    historial): sólo se limpia expediente y se actualiza estado_sigi (con
    su historial correspondiente, vía aplicar_actualizacion).

    Devuelve True si hubo algún cambio real (expediente tenía algo cargado
    antes de llamar a esto).
    """
    if not registro.expediente:
        return False  # ya estaba sin expediente, no hay nada que desvincular

    estado = estado_no_cargada()
    if estado is not None and registro.estado_sigi != estado:
        aplicar_actualizacion(db, registro, {"estado_sigi": estado})
    registro.expediente = None
    return True