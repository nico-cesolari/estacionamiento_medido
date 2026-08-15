# Ubicación: backend/app/reglas/reglas_sigemi.py
"""
Lógica compartida por llenar_actas_sigemi.py y actualizar_estado_sigemi.py.

Antes esto vivía todo junto en un solo script que hacía dos cosas
distintas en una sola pasada (cargar causa+estado la primera vez, Y
actualizar el estado en las siguientes). Se separó en dos scripts porque
son dos operaciones con reglas distintas:

  - llenar_actas_sigemi.py: sólo para actas que TODAVÍA NO tienen
    causa/estado cargado (carga inicial). Completa causa Y estado.

  - actualizar_estado_sigemi.py: sólo para actas que YA tienen causa
    cargada de una pasada anterior. Actualiza el estado, pero NUNCA
    toca la causa (una vez cargada, se asume estable).

Todo el parseo del archivo crudo (roturas de fila, regex de estado,
reglas de Juicio/Plan de Pago/Pago Voluntario/Sin Resolución) es
EXACTAMENTE el mismo para las dos operaciones, así que vive acá.

------------------------------------------------------------------------
NOVEDAD (respecto a la versión anterior): estado "JUZGADO DE FALTAS"
------------------------------------------------------------------------
En el archivo real (todas_em_sigemi.txt) aparece un estado que antes no
estaba contemplado: ESTADO_ACTUAL="JF" / ESTADO_DESCRIPCION="JUZGADO DE
FALTAS". Comprobado contra los datos reales, se comporta EXACTAMENTE
igual que "ARCHIVO":

  - A veces viene en fila partida, con "Pago Voluntario" en la
    continuación (mismo patrón que ARCHIVO + Pago Voluntario):
      2|2024|867411|5/11/24|Tránsito|||LEVE|Sin Resolución||||||||||||||||||||||
      Pago Voluntario||||JF|JUZGADO DE FALTAS|18/8/25|||418177||, |2024|FM|518|27/6/24|TR||LEVE|0|418177|436849||||||||
    -> en ese caso, PAGADA (igual que "ARCHIVO" + Pago Voluntario).

  - A veces viene en una sola fila, sin Pago Voluntario:
      1|2024|867184|28/10/24|Tránsito|||LEVE|Saldo Actualizado: $14.216,40|||||JUZGADO DE FALTAS||||416013|||2024||3354|02/07/2024|TR||LEVE|0|416013|434607
    -> en ese caso, archivada pero sin poder determinar el motivo con
       certeza (igual que "ARCHIVADA - REVISAR MOTIVO"): se carga
       archivada, sin motivo, para elegirlo a mano.

Por eso "JUZGADO DE FALTAS" se trata en el mismo branch que "ARCHIVO"
en calcular_estado() de acá abajo, no como un estado aparte.

------------------------------------------------------------------------
NOVEDAD: estado "PASAR A PROCURACION"
------------------------------------------------------------------------
Etapa previa a "PROCURACION" (ESTADO_DESCRIPCION="PASAR A PROCURACION").
Se mapea a su propio estado en la base (EstadoSigemi.pendiente_procuracion,
"Pasar a Procuración"), NO se lo confunde con "En Procuración". Si en el
archivo real aparece con otra puntuación/formato, ajustar ESTADO_RE.

------------------------------------------------------------------------
NOVEDAD: estado "RESUELTA SIN ARCHIVAR" (código corto tipo "SE")
------------------------------------------------------------------------
Hay registros con ESTADO_DESCRIPCION="_SIN ESTADO_" (mismo texto genérico
que antes se interpretaba siempre como "Vencida"/"Sin Resolución"), pero
que en realidad YA tienen una resolución: se distinguen porque el código
corto que acompaña a esa descripción (ESTADO_ACTUAL, primer grupo de
ESTADO_RE) no viene vacío y matchea uno de los códigos conocidos de
resolución. Ejemplo real:

  1|2025|873077|12/03/2025|Tránsito|||LEVE|||||SE|_SIN ESTADO_|12/03/2025|||470361||, |2024|FM|3594|02/07/2024|TR||LEVE|0|470361|489975

Acá ESTADO_ACTUAL="SE" (Sobreseída) junto con ESTADO_DESCRIPCION="_SIN
ESTADO_", y el registro NO tiene el texto "Sin Resolución" en ningún
lado -- eso lo diferencia de la "Vencida" real (que si no tiene código o
tiene "Sin Resolución" en el texto, sigue cayendo en VENCIDA como antes).

Se mapea a EstadoSigemi.resuelta_sin_archivo ("Resuelta sin Archivar"),
CON motivo (reutilizando MotivoArchivoSigemi, ver mapa
CODIGOS_MOTIVO_RESOLUCION_SIN_ARCHIVO más abajo) -- a diferencia de
"ARCHIVADA - REVISAR MOTIVO", acá el código SÍ nos da el motivo con
certeza, no hace falta elegirlo a mano.

OJO: sólo está confirmado el código "SE" -> Sobreseimiento. Si aparecen
otros códigos de este tipo (desestimación, amonestación, suspensión) hay
que sumarlos al diccionario a medida que se identifiquen en datos reales.

Un código NO vacío pero todavía no confirmado en el diccionario (ej.
"DS", "AM", "SU" antes de agregarlos) YA NO cae en VENCIDA: se carga
igual como "Resuelta sin Archivar", pero SIN motivo (motivo=None), para
elegirlo a mano -- mismo criterio que "ARCHIVADA - REVISAR MOTIVO" (no
adivinamos el motivo, pero tampoco lo dejamos afuera como Vencida, que
sería incorrecto: el registro ya está resuelto, sólo no sabemos con
certeza CUÁL es el motivo).

Sólo el caso de código VACÍO sigue cayendo en VENCIDA como antes: ahí no
hay ninguna pista de que esté resuelto, así que se mantiene el
comportamiento viejo.

------------------------------------------------------------------------
NOVEDAD: estado "ARCHIVADA SIN RESOLUCION" (dentro de ESTADOS_TIPO_ARCHIVO)
------------------------------------------------------------------------
Hasta ahora, dentro de la rama ARCHIVO/JUZGADO DE FALTAS sin Pago
Voluntario, todo caía en "ARCHIVADA - REVISAR MOTIVO" sin mirar si el
registro además traía el texto "Sin Resolución". Pero hay una diferencia
real entre:

  - Archivada sin motivo (ej. acta 933053): ARCHIVO, sin Pago
    Voluntario, SIN el texto "Sin Resolución" -- no hay ninguna pista
    de por qué se archivó, pero tampoco nada que lo contradiga. Sigue
    cayendo en "ARCHIVADA - REVISAR MOTIVO" -> EstadoSigemi.archivada,
    motivo=None (se elige a mano).

  - Archivada por error (ej. acta 975905): ARCHIVO, sin Pago
    Voluntario, pero CON el texto "Sin Resolución" -- acá SIGEMI la
    marca como archivada Y como no resuelta al mismo tiempo, lo cual es
    contradictorio. No es "no sabemos el motivo", es "el propio sistema
    de origen se contradice". Se carga aparte, como
    EstadoSigemi.archivada_sin_resolucion (ver models.py), para poder
    distinguirla visualmente (mismo color que Vencida/naranja) en vez de
    mezclarla con las archivadas normales a revisar. No lleva motivo:
    no es "Archivada" ni "Resuelta sin Archivar", así que
    motivo_archivo_sigemi no aplica acá.

  El caso con Pago Voluntario (con o sin "Sin Resolución") sigue
  primero en la cadena de ifs y no se ve afectado: si hay Pago
  Voluntario, ya sabemos que está pagada, sin importar qué diga el
  texto de "Sin Resolución".
"""
import re
from typing import Optional

INICIO_REGISTRO_RE = re.compile(r'^\s*\d+\|\d{4}\|\d+\|')

# Estados "tipo archivo" que comparten la misma lógica (ver nota arriba
# sobre JUZGADO DE FALTAS). Si en el futuro aparece un tercer sinónimo de
# archivado, agregarlo acá alcanza.
ESTADOS_TIPO_ARCHIVO = ("ARCHIVO", "JUZGADO DE FALTAS")

ESTADO_RE = re.compile(r'\|(\w*)\|(PROCURACION|PASAR A PROCURACION|ARCHIVO|_?SIN ESTADO_?|JUZGADO DE FALTAS)\|')

# Código corto (ESTADO_ACTUAL) -> nombre del atributo en MotivoArchivoSigemi,
# para el caso "_SIN ESTADO_" que en realidad ya está resuelto (ver nota
# "RESUELTA SIN ARCHIVAR" arriba). Sólo "SE" está confirmado contra datos
# reales; sumar acá los que se vayan confirmando (no adivinar).
CODIGOS_MOTIVO_RESOLUCION_SIN_ARCHIVO = {
    "SE": "por_sobreseimiento",  # Sobreseída
    # "DS": "por_desestimacion",   # Desestimada -- confirmar código real antes de habilitar
    # "AM": "por_amonestacion",    # Amonestada  -- confirmar código real antes de habilitar
    # "SU": "suspendida",          # Suspendida  -- confirmar código real antes de habilitar
}
# OJO: '.' en vez de '[oó]' a propósito -- el archivo real trae algunas
# tildes mal decodificadas (llegan como U+FFFD, el caracter de reemplazo
# "�"), así que buscar literalmente 'o' u 'ó' fallaba silenciosamente y
# esos registros de "Sin Resolución" se colaban en la rama de RESUELTA
# SIN ARCHIVAR (ver bug detectado en actas 873333 vs 873368: ambas
# tenían codigo_estado="SE", la única diferencia real entre "Vencida" y
# "Resuelta sin Archivar" es este texto). Con '.' matchea cualquier
# caracter en esa posición, así que es inmune a cómo haya quedado
# decodificada la tilde.
JUICIO_RE = re.compile(r'Juicio\s+\S+\s+Saldo:\s*\$?\s*([\d.,]+)', re.IGNORECASE)
PLAN_PAGO_RE = re.compile(r'Plan de Pago\s+\S+\s+Saldo:\s*\$?\s*([\d.,]+)', re.IGNORECASE)
SALDO_ACTUALIZADO_RE = re.compile(r'Saldo Actualizado:\s*\$?\s*([\d.,]+)', re.IGNORECASE)
PAGO_VOLUNTARIO_RE = re.compile(r'Pago Voluntario', re.IGNORECASE)
SIN_RESOLUCION_RE = re.compile(r'Sin Resoluci.n', re.IGNORECASE)

# Cuando el registro viene partido en 2 filas, ACTA_NUMERO queda
# desalineado de su columna habitual. Pero SIGEMI siempre deja un campo
# ", " justo antes de ACTA_ANIO|ACTA_OFICINA|ACTA_NUMERO -> lo usamos de ancla.
ACTA_NUMERO_PARTIDO_RE = re.compile(r'\|,\s*\|\d{4}\|[A-Z]{1,4}\|(\d+)\|')

# Posición de ACTA_NUMERO cuando la fila NO viene partida (fila completa
# de 30 columnas, confirmado contra el header real de SIGEMI).
INDICE_ACTA_NUMERO_FILA_COMPLETA = 22


def parse_monto(texto):
    """Convierte '62.400,80' -> 62400.80  y  '0,00' -> 0.0"""
    if texto is None:
        return None
    limpio = texto.strip().replace('.', '').replace(',', '.')
    try:
        return float(limpio)
    except ValueError:
        return None


def leer_registros_crudos(path_entrada):
    """
    Lee el archivo linea por linea y agrupa las lineas que pertenecen al
    mismo registro (cuando el sistema lo parte en 2 filas, la segunda
    arranca con ',' y NO matchea el patrón de inicio de un registro nuevo).
    """
    contenido = None
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            with open(path_entrada, "r", encoding=enc) as f:
                contenido = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if contenido is None:
        with open(path_entrada, "r", encoding="latin-1", errors="replace") as f:
            contenido = f.read()

    registros = []
    actual = None
    for linea in contenido.splitlines():
        if not linea.strip():
            continue
        if INICIO_REGISTRO_RE.match(linea):
            if actual is not None:
                registros.append(actual)
            actual = linea
        else:
            if actual is not None:
                actual = actual + " " + linea
    if actual is not None:
        registros.append(actual)
    return registros


def extraer_numero_causa(raw: str) -> Optional[str]:
    """JUZGADO|ANIO|NUMERO|... -> NUMERO. Siempre en posición fija: la
    rotura entre filas partidas ocurre más adelante en el registro."""
    partes = raw.split("|")
    return partes[2].strip() if len(partes) > 2 and partes[2].strip() else None


def extraer_acta_numero(raw: str) -> Optional[str]:
    """Prueba primero el patrón de fila partida (más específico); si no
    matchea, asume fila completa y toma la columna 22."""
    m = ACTA_NUMERO_PARTIDO_RE.search(raw)
    if m:
        return m.group(1)
    partes = raw.split("|")
    if len(partes) > INDICE_ACTA_NUMERO_FILA_COMPLETA:
        valor = partes[INDICE_ACTA_NUMERO_FILA_COMPLETA].strip()
        return valor or None
    return None


def calcular_estado(raw: str) -> str:
    """
    Misma lógica de negocio que procesar_archivo_sigemi.py, con el
    agregado de que "JUZGADO DE FALTAS" se trata igual que "ARCHIVO"
    (ver ESTADOS_TIPO_ARCHIVO arriba).
    """
    m_estado = ESTADO_RE.search(raw)
    # El archivo real trae esta descripción a veces como "_SIN ESTADO_"
    # y a veces como "SIN ESTADO" (sin guiones bajos) -- se normaliza acá
    # para no tener que repetir el chequeo de ambas variantes en cada
    # rama de abajo.
    estado_desc = m_estado.group(2).strip('_') if m_estado else None
    codigo_estado = m_estado.group(1) if m_estado else None

    m_juicio = JUICIO_RE.search(raw)
    m_plan = PLAN_PAGO_RE.search(raw)
    tiene_pago_voluntario = bool(PAGO_VOLUNTARIO_RE.search(raw))
    tiene_sin_resolucion = bool(SIN_RESOLUCION_RE.search(raw))

    if m_juicio:
        saldo_juicio = parse_monto(m_juicio.group(1))
        if saldo_juicio is not None and saldo_juicio == 0.0:
            if m_plan:
                saldo_plan = parse_monto(m_plan.group(1))
                if saldo_plan is not None and saldo_plan != 0.0:
                    return "VENCIDA (Plan de Pago pendiente)"
            return "PAGADA"
        return "PROCURACION"

    if estado_desc in ESTADOS_TIPO_ARCHIVO:
        if tiene_pago_voluntario:
            return "PAGADA (Archivo - Pago Voluntario)"
        # ARCHIVO/JUZGADO DE FALTAS con el texto "Sin Resolución" pero SIN
        # Pago Voluntario: a diferencia de "ARCHIVADA - REVISAR MOTIVO"
        # (donde no hay ninguna pista de por qué se archivó, pero tampoco
        # nada que contradiga que esté archivada), acá SIGEMI la marca
        # como archivada Y como no resuelta al mismo tiempo -- es una
        # inconsistencia del propio sistema de origen, no simplemente "no
        # sabemos el motivo". Se carga aparte (ver EstadoSigemi.archivada_
        # sin_resolucion en models.py) para poder distinguirla visualmente
        # (mismo color que Vencida) en vez de mezclarla con las archivadas
        # normales a revisar.
        if tiene_sin_resolucion:
            return "ARCHIVADA SIN RESOLUCION"
        return "ARCHIVADA - REVISAR MOTIVO"

    # "_SIN ESTADO_" con CUALQUIER código corto no vacío (y sin el texto
    # "Sin Resolución" en el registro) -> ya está resuelta, no vencida.
    # No hace falta que el código esté en CODIGOS_MOTIVO_RESOLUCION_SIN_ARCHIVO
    # para tomar esta rama: si está confirmado, resolver_estado() le pone
    # el motivo exacto; si no, lo deja con motivo=None para elegir a mano
    # (ver nota en el docstring del módulo). Va ANTES de la regla vieja de
    # VENCIDA justamente para sacarle estos casos.
    if (
        estado_desc == "SIN ESTADO"
        and not tiene_sin_resolucion
        and codigo_estado
    ):
        return f"RESUELTA SIN ARCHIVAR ({codigo_estado})"

    # "SIN ESTADO" (o "_SIN ESTADO_") CON el texto "Sin Resolución" es,
    # en principio, VENCIDA -- salvo que además traiga "Pago Voluntario":
    # ahí SIGEMI todavía no le puso una resolución formal al trámite, pero
    # la infracción ya se pagó, así que es PAGADA y no Vencida. Mismo
    # criterio que "PAGADA (Archivo - Pago Voluntario)" más arriba, pero
    # acá el registro nunca pasó por archivo. Ejemplo real (actas 906616
    # vs 906607): idéntico "SE|SIN ESTADO|" y "Sin Resolución" en las dos,
    # la única diferencia es la presencia de "Pago Voluntario".
    if tiene_pago_voluntario and (estado_desc is None or estado_desc == "SIN ESTADO"):
        return "PAGADA (Sin Resolución - Pago Voluntario)"

    if tiene_sin_resolucion or estado_desc == "SIN ESTADO":
        return "VENCIDA"

    if estado_desc == "PASAR A PROCURACION":
        return "PASAR A PROCURACION"

    if estado_desc == "PROCURACION":
        return "PROCURACION"

    return f"DESCONOCIDO ({estado_desc or 'sin dato'})"


# estado_final -> (EstadoSigemi, MotivoArchivoSigemi | None)
# "ARCHIVADA - REVISAR MOTIVO" queda con motivo=None A PROPÓSITO (cubre
# tanto ARCHIVO como JUZGADO DE FALTAS sin Pago Voluntario). El otro caso
# con motivo=None a propósito es "RESUELTA SIN ARCHIVAR (código no
# confirmado)", que se resuelve aparte en resolver_estado() porque el
# código va adentro del string y no puede vivir como key fija acá.
def mapa_estado_final(models):
    """
    Recibe el módulo `models` (para no importar SQLAlchemy acá) y arma el
    diccionario de mapeo. Es función en vez de constante para no atar este
    módulo compartido a un import específico de la app.
    """
    return {
        "PAGADA": (models.EstadoSigemi.pagada, None),
        # El estado ARCHIVO/JUZGADO DE FALTAS en SIGEMI significa que el
        # acta YA ESTÁ ARCHIVADA en el trámite -- eso tiene prioridad
        # sobre cómo se resolvió. Si además vino con "Pago Voluntario",
        # eso es el MOTIVO del archivo (se pagó), no un estado aparte:
        # por eso va a EstadoSigemi.archivada con
        # MotivoArchivoSigemi.por_pago, igual que cualquier otra
        # archivada con motivo conocido, y NO a EstadoSigemi.pagada (que
        # queda reservado para la rama de Juicio con saldo 0, donde
        # SIGEMI nunca marcó el trámite como archivado).
        "PAGADA (Archivo - Pago Voluntario)": (models.EstadoSigemi.archivada, models.MotivoArchivoSigemi.por_pago),
        # A diferencia del caso anterior, acá el trámite nunca pasó por
        # archivo (sigue en "Sin Resolución" en SIGEMI) -- lo único que
        # sabemos con certeza es que se pagó voluntariamente. Va a
        # EstadoSigemi.pagada igual que la rama de Juicio con saldo 0,
        # sin motivo (motivo_archivo_sigemi no aplica: no es archivada).
        "PAGADA (Sin Resolución - Pago Voluntario)": (models.EstadoSigemi.pagada, None),
        "PASAR A PROCURACION": (models.EstadoSigemi.pendiente_procuracion, None),
        "PROCURACION": (models.EstadoSigemi.en_procuracion, None),
        "VENCIDA (Plan de Pago pendiente)": (models.EstadoSigemi.en_procuracion, None),
        "ARCHIVADA - REVISAR MOTIVO": (models.EstadoSigemi.archivada, None),
        # Ver nota en calcular_estado(): archivada pero con "Sin Resolución"
        # y sin Pago Voluntario -- inconsistencia de SIGEMI, no una
        # archivada normal. motivo=None: no aplica (no es "Archivada" ni
        # "Resuelta sin Archivar", así que motivo_archivo_sigemi no se usa
        # para este estado -- ver aplicar_cambios_estado en crud.py).
        "ARCHIVADA SIN RESOLUCION": (models.EstadoSigemi.archivada_sin_resolucion, None),
        "VENCIDA": (models.EstadoSigemi.sin_resolucion, None),
    }


def resolver_estado(estado_final: str, models):
    if estado_final.startswith("RESUELTA SIN ARCHIVAR ("):
        return models.EstadoSigemi.resuelta_sin_archivo, None

    return mapa_estado_final(models).get(estado_final)