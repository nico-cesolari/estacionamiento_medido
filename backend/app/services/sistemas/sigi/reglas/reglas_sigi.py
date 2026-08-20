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

# Import ABSOLUTO a propósito, no relativo: "app" y "sistemas" son dos
# paquetes de nivel superior distintos (ambos cuelgan de backend/, que es
# lo que se agrega a sys.path en cada script que usa este módulo), no
# están anidados uno dentro del otro. Un relativo tipo "...app" desde acá
# (sistemas.sigi.reglas.reglas_sigi) apunta a "sistemas.app", que no
# existe -- de ahí el ModuleNotFoundError que tenía esto antes.
from app.services.estados import aplicar_cambios_estado
from app.models import models


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
    "Archivado": models.EstadoSigi.archivado,
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

def crear_registro_nuevo_por_acta(
    db: Session, *, expediente, acta, patente, direccion, fecha_hora,
    estado_texto=None, motivo_texto=None,
) -> "models.Registro":
    from app.services.sigi_vinculos import crear_vinculo
    registro = models.Registro(
        expediente=None,  # ya no existe esta columna -- si esto tira
                           # error de atributo, es la señal de que la
                           # migración del modelo (2.1) no se aplicó
        acta=normalizar_acta(acta),
        patente=patente,
        direccion=direccion,
        fecha_hora=fecha_hora,
        estado_semyt=models.EstadoSemyt.eliminada,
    )
    db.add(registro)
    db.flush()  # necesitamos registro.id para el vínculo

    nuevo_estado = mapear_estado(estado_texto) or estado_no_cargada()
    nuevo_motivo = mapear_motivo(motivo_texto)
    crear_vinculo(db, registro, expediente, nuevo_estado, nuevo_motivo, origen="directo")
    return registro


def hay_cambio_real(vinculo: "models.VinculoSigi", cambios: dict) -> bool:
    if vinculo.estado_sigi != cambios.get("estado_sigi"):
        return True
    if "motivo_archivo_sigi" in cambios and vinculo.motivo_archivo_sigi != cambios["motivo_archivo_sigi"]:
        return True
    return False

# ---------------------------------------------------------------------------
# Qué registros le tocan a cada paso.
# ---------------------------------------------------------------------------
def todos_los_expedientes_cargados(db: Session) -> dict:
    """{expediente_normalizado: VinculoSigi} de TODOS los vínculos
    existentes (sin importar el registro)."""
    vinculos = db.query(models.VinculoSigi).all()
    return {normalizar_expediente(v.expediente): v for v in vinculos}

def vinculos_pendientes(db: Session):
    """Vínculos SIGI cuyo estado todavía puede cambiar (no Archivado,
    no No Cargada)."""
    return (
        db.query(models.VinculoSigi)
        .filter(
            models.VinculoSigi.estado_sigi.isnot(None),
            models.VinculoSigi.estado_sigi != models.EstadoSigi.no_cargada,
            models.VinculoSigi.estado_sigi != models.EstadoSigi.archivado,
        )
        .all()
    )