# consistencia.py
from app.models import models

# ---------------------------------------------------------------------------
# Consistencia entre SEMyT, SIGEMI y SIGI
# ---------------------------------------------------------------------------
# Los tres sistemas se reducen a un mismo resultado de negocio:
# PAGADA, RESUELTA o VENCIDA.
# La comparación se hace sobre esas categorías,
# no sobre el texto literal de cada estado.

# Categorías que se consideran equivalentes a los fines de consistencia:
# "pagó" y "se resolvió" son, en la práctica, la misma resolución de fondo.
_EQUIVALENTES = [{"PAGADA", "RESUELTA"}]

def _mismo_grupo(valores: set) -> bool:
    if len(valores) == 1:
        return True
    for grupo in _EQUIVALENTES:
        if valores <= grupo:
            return True
    return False

def categoria_sigemi(estado_sigemi, motivo_archivo_sigemi):
    if estado_sigemi == models.EstadoSigemi.pagada:
        return "PAGADA"

    if estado_sigemi == models.EstadoSigemi.archivado:
        if motivo_archivo_sigemi in (
            models.MotivoArchivoSigemi.por_pago,
            models.MotivoArchivoSigemi.por_pago_procuracion,
        ):
            return "PAGADA"

        if motivo_archivo_sigemi is not None:
            return "RESUELTA"

        return None

    if estado_sigemi == models.EstadoSigemi.resuelta_sin_archivo:
        return "RESUELTA"

    if estado_sigemi in (
        models.EstadoSigemi.sin_resolucion,
        models.EstadoSigemi.en_procuracion,
        models.EstadoSigemi.pendiente_procuracion,
        models.EstadoSigemi.archivado_sin_resolucion,
    ):
        return "VENCIDA"

    return None


def categoria_semyt(estado_semyt):
    if estado_semyt == models.EstadoSemyt.pagada_en_juzgado:
        return "PAGADA"

    if estado_semyt in (
        models.EstadoSemyt.resuelta_en_juzgado,
        models.EstadoSemyt.rechazada,
    ):
        return "RESUELTA"

    if estado_semyt == models.EstadoSemyt.vencida:
        return "VENCIDA"
    
    if estado_semyt == models.EstadoSemyt.eliminada:
        return "ELIMINADA"
    return None


def categoria_sigi(estado_sigi, motivo_archivo_sigi):
    if estado_sigi == models.EstadoSigi.archivado:
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


def _sigemi_ignorable(registro):
    """
    SIGEMI se ignora cuando todavía no tiene información suficiente:
    - estado inexistente
    - no cargada
    - archivado sin motivo
    """
    if registro.estado_sigemi in (
        None,
        models.EstadoSigemi.no_cargada,
    ):
        return True

    if (
        registro.estado_sigemi == models.EstadoSigemi.archivado
        and registro.motivo_archivo_sigemi is None
    ):
        return True

    return False


def _sigi_ignorable(registro):
    """
    SIGI se ignora cuando todavía no tiene información suficiente:
    - estado inexistente
    - no cargada
    - archivado sin motivo
    """
    if registro.estado_sigi in (
        None,
        models.EstadoSigi.no_cargada,
    ):
        return True

    if (
        registro.estado_sigi == models.EstadoSigi.archivado
        and registro.motivo_archivo_sigi is None
    ):
        return True

    return False


def calcular_consistencia(registro):
    """
    SEMyT siempre se exige cargado.

    SIGEMI y SIGI pueden estar "ignorables" (sin cargar o archivados
    sin motivo). En ese caso no bloquean la consistencia.

    Si los sistemas que sí tienen información coinciden, el registro
    se considera consistente.

    Excepción:
    si existe una categoría VENCIDA y SIGI está ignorable, se considera
    inconsistente porque SIGI es el sistema vigente y debería reflejar
    el trámite activo.
    """
    categorias = {}
    faltantes = []
    ignorados = []

    # SEMyT siempre es obligatorio
    cat_semyt = categoria_semyt(registro.estado_semyt)

    if cat_semyt is None:
        faltantes.append("SEMyT")
    else:
        categorias["SEMyT"] = cat_semyt

    # SIGEMI puede ser ignorado
    if _sigemi_ignorable(registro):
        ignorados.append("SIGEMI")
    else:
        cat_sigemi = categoria_sigemi(
            registro.estado_sigemi,
            registro.motivo_archivo_sigemi,
        )

        if cat_sigemi is None:
            faltantes.append("SIGEMI")
        else:
            categorias["SIGEMI"] = cat_sigemi

    # SIGI puede ser ignorado
    if _sigi_ignorable(registro):
        ignorados.append("SIGI")
    else:
        cat_sigi = categoria_sigi(
            registro.estado_sigi,
            registro.motivo_archivo_sigi,
        )

        if cat_sigi is None:
            faltantes.append("SIGI")
        else:
            categorias["SIGI"] = cat_sigi

    # Falta información obligatoria para determinar consistencia
    if faltantes:
        return None

    if "SIGI" in ignorados:
        sigemi_cerrado = (
            "SIGEMI" in ignorados
            or categorias.get("SIGEMI") in ("PAGADA", "RESUELTA")
            or registro.estado_sigemi == models.EstadoSigemi.archivado_sin_resolucion
        )
        semyt_cerrado = categorias.get("SEMyT") in ("PAGADA", "RESUELTA")

        if sigemi_cerrado and semyt_cerrado:
            return True

        if "VENCIDA" in categorias.values():
            return False

    valores = set(categorias.values())

    return _mismo_grupo(valores)

def actualizar_consistencia(registros):
    for registro in registros:
        registro.consistente = calcular_consistencia(registro)

# ---------------------------------------------------------------------------
# "Archivar en SIGI": casos en que SEMyT/SIGEMI ya resolvieron el trámite
# pero SIGI todavía lo tiene como vigente (no archivado). Reglas explícitas
# en una lista -- sumar un caso nuevo es agregar un lambda más a
# REGLAS_ARCHIVAR_SIGI, sin tocar los que ya funcionan.
# ---------------------------------------------------------------------------

def _sigi_sigue_vigente_vinculo(vinculo) -> bool:
    return categoria_sigi(vinculo.estado_sigi, vinculo.motivo_archivo_sigi) == "VENCIDA"


REGLAS_ARCHIVAR_SIGI_VINCULO = [
    lambda r, v: (
        categoria_semyt(r.estado_semyt) in ("PAGADA", "RESUELTA")
        and categoria_sigemi(r.estado_sigemi, r.motivo_archivo_sigemi) in ("PAGADA", "RESUELTA")
        and _sigi_sigue_vigente_vinculo(v)
    ),
    lambda r, v: (
        r.estado_semyt == models.EstadoSemyt.rechazada
        and _sigi_sigue_vigente_vinculo(v)
    ),
]

def debe_archivar_sigi_vinculo(registro, vinculo) -> bool:
    return any(regla(registro, vinculo) for regla in REGLAS_ARCHIVAR_SIGI_VINCULO)