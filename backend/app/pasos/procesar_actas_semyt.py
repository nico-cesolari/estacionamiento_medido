"""
Lee actas desde la grilla "Consulta de Actas Registradas" en
https://ciudad.villamaria.gob.ar/#/actas usando la sesión ya logueada
(storage_state) que dejó API-REST Payment.

Hace DOS cosas, cada vez que corre (pensado para correr una vez por día):

  1) CREAR actas nuevas: filtra la grilla por Fecha Desde/Hasta = hoy,
     recorre todas las páginas de resultados y, para cada fila cuyo Nº de
     acta todavía no exista en nuestra DB, crea un `Registro` nuevo con
     los datos que trae SEMyT (acta, dominio/patente, estado, fecha,
     cuadra, vencimiento, importe). Los campos que sólo da SIGEMI o SIGI
     (expediente, estado_sigi, motivo_archivo_sigi, etc.) se dejan sin
     completar; los llenan esos otros pasos más adelante.
     Se ignoran (no se crean) las actas en estado IMPAGA o PAGADA.

  2) ACTUALIZAR estado: para las actas que ya teníamos guardadas y todavía
     no llegaron a un estado terminal, busca por "Filtrar por Número" y
     relee la columna "Estado" para actualizar estado_semyt.

Basado en la grilla real ("Consulta de Actas Registradas"):
  Filtros: "Filtrar por Dominio", "Filtrar por Número", "Fecha Desde",
  "Fecha Hasta", "Estado de Acta" (dropdown), botones "Buscar" / "Limpiar".
  Columnas de resultado: Nro. | Fecha | Dominio | Cuadra | Estado |
  Vencimiento | Importe | Acciones.
  Paginación al pie: "Elementos por página" + "Página X de Y" + botones
  |< < > >|.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from .. import crud, models
from .navegador import PaginaConSesion
from ..reglas.reglas_semyt import (
    ESTADOS_IGNORADOS_SEMYT, MAPA_ESTADO_SEMYT, ESTADO_PAGADA_EN_JUZGADO,
    COLUMNAS_TABLA, INDICE_COLUMNA_ESTADO, SELECTOR_FILAS_RESULTADO,
    parsear_fecha_hora, pagada_en_juzgado_con_datos,
)

URL_SEMYT = "https://ciudad.villamaria.gob.ar/#/actas"
ARCHIVO_SESION = "sesion_semyt.json"

# --- Filtros de la grilla (labels tal cual figuran en la captura) ---
LABEL_FILTRO_DOMINIO = "Filtrar por Dominio"
LABEL_FILTRO_NUMERO = "Filtrar por Número"
LABEL_FECHA_DESDE = "Fecha Desde"
LABEL_FECHA_HASTA = "Fecha Hasta"
TEXTO_BOTON_BUSCAR = "Buscar"

# AJUSTAR: selector del botón "siguiente" de la paginación (en la captura
# se ve como iconos "|< < > >|", sin texto). Esta es una suposición
# razonable (busca por aria-label/title conteniendo "siguiente"/"next");
# confirmar el selector real si no encuentra el botón.
SELECTOR_BOTON_SIGUIENTE = (
    "button[aria-label*='iguiente'], button[aria-label*='next' i], "
    "button[title*='iguiente'], button[title*='next' i]"
)

# AJUSTAR: nombres reales de los atributos en models.Registro para estos
# datos que trae SEMyT y que todavía no confirmamos. La clave es el
# nombre de columna tal cual viene de la grilla (no tocar); el valor es
# el atributo real del modelo (poner None para no cargar ese campo).
MAPEO_CAMPOS_EXTRA = {
    "fecha": "fecha",
    "cuadra": "direccion",
}


def _registros_pendientes(db: Session):
    """Actas que todavía no llegaron a un estado terminal en SEMyT."""
    return (
        db.query(models.Registro)
        .filter(
            models.Registro.estado_semyt.is_(None)
            | (models.Registro.estado_semyt == models.EstadoSemyt.vencida)
        )
        .all()
    )


def _fecha_iso(fecha: date) -> str:
    """Formato que espera un <input type="date"> al hacer .fill() en Playwright."""
    return fecha.isoformat()


def _parsear_fecha_corta(texto: str) -> Optional[date]:
    """"28/06/24" -> date. None si está vacío ("-") o no matchea el formato."""
    if not texto or texto.strip() in ("-", ""):
        return None
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%y").date()
    except ValueError:
        return None


def _parsear_importe(texto: str) -> Optional[Decimal]:
    """"$ 4.320,00" -> Decimal("4320.00"). None si está vacío ("-")."""
    if not texto or texto.strip() in ("-", ""):
        return None
    limpio = texto.replace("$", "").strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(limpio)
    except InvalidOperation:
        return None


def _aplicar_campos_extra(nuevo_registro, datos: Dict[str, str]):
    """
    Setea, de forma segura, los campos adicionales que trae SEMyT
    (fecha, dirección/cuadra, vencimiento, importe) usando el mapeo de
    MAPEO_CAMPOS_EXTRA. Si el atributo no existe en models.Registro, no
    rompe: sólo avisa por consola para que se corrija el mapeo.
    """
    parsers = {
        "fecha": parsear_fecha_hora,
        "vencimiento": _parsear_fecha_corta,
        "importe": _parsear_importe,
    }
    for clave_dato, nombre_atributo in MAPEO_CAMPOS_EXTRA.items():
        if not nombre_atributo:
            continue
        valor_crudo = datos.get(clave_dato)
        if valor_crudo is None:
            continue
        parser = parsers.get(clave_dato)
        valor = parser(valor_crudo) if parser else valor_crudo

        if hasattr(nuevo_registro, nombre_atributo):
            setattr(nuevo_registro, nombre_atributo, valor)
        else:
            print(
                f"[SEMyT] AVISO: models.Registro no tiene el atributo '{nombre_atributo}' "
                f"(mapeado desde '{clave_dato}'); revisar MAPEO_CAMPOS_EXTRA."
            )


async def _parsear_fila(fila) -> Optional[Dict[str, str]]:
    celdas = fila.locator("td")
    total_celdas = await celdas.count()
    if total_celdas == 0:
        return None
    valores = {}
    for idx, nombre in enumerate(COLUMNAS_TABLA):
        if nombre == "acciones" or idx >= total_celdas:
            continue
        valores[nombre] = (await celdas.nth(idx).inner_text()).strip()
    return valores or None


async def _recorrer_paginas_y_extraer(page) -> List[Dict[str, str]]:
    """Recorre todas las páginas de resultados actuales (después de
    aplicar los filtros) usando el botón 'siguiente' y devuelve todas las
    filas ya parseadas."""
    resultados: List[Dict[str, str]] = []
    while True:
        filas = page.locator(SELECTOR_FILAS_RESULTADO)
        total_filas = await filas.count()
        for i in range(total_filas):
            datos = await _parsear_fila(filas.nth(i))
            if datos:
                resultados.append(datos)

        boton_siguiente = page.locator(SELECTOR_BOTON_SIGUIENTE)
        if await boton_siguiente.count() == 0:
            break
        if await boton_siguiente.first.is_disabled():
            break
        await boton_siguiente.first.click()
        await page.wait_for_load_state("networkidle")

    return resultados


INDICE_COLUMNA_NRO = COLUMNAS_TABLA.index("nro")

# Cuántas veces reintentar la lectura si la fila que devuelve la grilla
# todavía no corresponde al acta pedida (repintado async atrasado), y
# cuánto esperar entre reintento y reintento.
INTENTOS_LEER_ACTA = 3
ESPERA_REINTENTO_MS = 800


async def _leer_acta(page, numero_acta: str) -> Optional[Dict[str, str]]:
    """
    Filtra por número de acta y devuelve {"estado", "vencimiento", "importe"}
    de la fila correspondiente. None si no hay resultados o si, tras
    reintentar, la grilla nunca llega a mostrar la fila del acta pedida
    (evita leer por error el estado de una fila vieja de la búsqueda
    anterior cuando el repintado de la grilla se atrasa respecto de
    "networkidle").
    """
    numero_acta = str(numero_acta).strip()

    await page.get_by_label(LABEL_FILTRO_NUMERO).fill(numero_acta)
    await page.get_by_role("button", name=TEXTO_BOTON_BUSCAR).click()
    await page.wait_for_load_state("networkidle")

    for intento in range(1, INTENTOS_LEER_ACTA + 1):
        filas = page.locator(SELECTOR_FILAS_RESULTADO)
        if await filas.count() == 0:
            return None

        celdas = filas.first.locator("td")
        nro_leido = (await celdas.nth(INDICE_COLUMNA_NRO).inner_text()).strip()

        if nro_leido == numero_acta:
            return {
                "estado": (await celdas.nth(INDICE_COLUMNA_ESTADO).inner_text()).strip().upper(),
                "vencimiento": (await celdas.nth(COLUMNAS_TABLA.index("vencimiento")).inner_text()).strip(),
                "importe": (await celdas.nth(COLUMNAS_TABLA.index("importe")).inner_text()).strip(),
            }

        # La grilla todavía muestra una fila de la búsqueda anterior
        # (o de otra acta): esperamos un toque y volvemos a leer, sin
        # repetir el fill/click (el filtro ya está aplicado, sólo falta
        # que termine de repintar).
        # LOG PARA VER INTERACCION DE
        # print(
        #     f"[SEMyT] Pedí acta {numero_acta} pero la grilla muestra Nro. {nro_leido} "
        #     f"(intento {intento}/{INTENTOS_LEER_ACTA}); reintentando lectura..."
        # )
        if intento < INTENTOS_LEER_ACTA:
            await page.wait_for_timeout(ESPERA_REINTENTO_MS)

    print(
        f"[SEMyT] No se pudo confirmar la fila del acta {numero_acta} tras "
        f"{INTENTOS_LEER_ACTA} intentos; se descarta esta lectura."
    )
    return None


async def _crear_actas_nuevas(db: Session, page, fecha_desde: str, fecha_hasta: str) -> tuple[int, int, int]:
    """
    Filtra la grilla por rango de fechas (pensado para "hoy"), recorre
    todas las páginas de resultados y crea en la DB los registros que
    todavía no existen (usando el Nº de acta como clave), salvo los que
    estén en estado IMPAGA o PAGADA.

    Devuelve (creadas, ignoradas, errores).
    """
    await page.get_by_label(LABEL_FECHA_DESDE).fill(fecha_desde)
    await page.get_by_label(LABEL_FECHA_HASTA).fill(fecha_hasta)
    await page.get_by_role("button", name=TEXTO_BOTON_BUSCAR).click()
    await page.wait_for_load_state("networkidle")

    filas_grilla = await _recorrer_paginas_y_extraer(page)

    creadas, ignoradas, errores = 0, 0, 0
    for datos in filas_grilla:
        numero_acta = datos.get("nro")
        estado_texto = (datos.get("estado") or "").strip().upper()

        if not numero_acta:
            errores += 1
            continue

        if estado_texto in ESTADOS_IGNORADOS_SEMYT:
            ignoradas += 1
            continue
        
        if estado_texto == ESTADO_PAGADA_EN_JUZGADO and pagada_en_juzgado_con_datos(
            datos.get("vencimiento", ""), datos.get("importe", "")
        ):
            ignoradas += 1
            continue
        
        ya_existe = db.query(models.Registro).filter(models.Registro.acta == numero_acta).first()
        if ya_existe:
            # No se crea de nuevo; su estado se actualiza en el otro paso
            # (_registros_pendientes) si todavía no es un estado terminal.
            continue

        nuevo_estado = MAPA_ESTADO_SEMYT.get(estado_texto)
        if nuevo_estado is None:
            print(f"[SEMyT] Estado desconocido '{estado_texto}' en acta {numero_acta}; no se crea el registro.")
            errores += 1
            continue

        nuevo_registro = models.Registro(
            acta=numero_acta,
            patente=datos.get("dominio"),
            estado_semyt=nuevo_estado,
        )
        _aplicar_campos_extra(nuevo_registro, datos)

        try:
            db.add(nuevo_registro)
            db.flush()
            creadas += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[SEMyT] Error creando registro para acta {numero_acta}: {exc}")
            db.rollback()
            errores += 1

    return creadas, ignoradas, errores


async def procesar_actas_semyt(
    db: Session,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
) -> dict:
    """
    Punto de entrada del paso. Por defecto usa la fecha de hoy tanto para
    Desde como para Hasta (pensado para correr una vez por día); se pueden
    pasar fechas explícitas para reprocesar un rango puntual.
    """
    fecha_desde = fecha_desde or date.today()
    fecha_hasta = fecha_hasta or fecha_desde

    registros_pendientes = _registros_pendientes(db)

    creadas = ignoradas_en_creacion = errores_en_creacion = 0
    actualizados, sin_cambios, ignorados, errores = 0, 0, 0, 0

    async with PaginaConSesion(ARCHIVO_SESION, URL_SEMYT) as page:

        # --- 1) Crear actas nuevas del rango de fechas (por defecto, hoy) ---
        try:
            creadas, ignoradas_en_creacion, errores_en_creacion = await _crear_actas_nuevas(
                db, page, _fecha_iso(fecha_desde), _fecha_iso(fecha_hasta)
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[SEMyT] Error buscando actas nuevas ({fecha_desde} a {fecha_hasta}): {exc}")
            errores_en_creacion += 1

        # --- 2) Actualizar estado de actas ya existentes que no llegaron a un estado terminal ---
        for registro in registros_pendientes:
            try:
                datos_acta = await _leer_acta(page, registro.acta)
            except Exception as exc:  # noqa: BLE001
                print(f"[SEMyT] Error leyendo acta {registro.acta}: {exc}")
                errores += 1
                continue

            if datos_acta is None:
                print(f"[SEMyT] Acta {registro.acta} no encontrada en la grilla")
                errores += 1
                continue

            estado_texto = datos_acta["estado"]

            if estado_texto in ESTADOS_IGNORADOS_SEMYT:
                ignorados += 1
                continue

            if estado_texto == ESTADO_PAGADA_EN_JUZGADO and pagada_en_juzgado_con_datos(
                datos_acta["vencimiento"], datos_acta["importe"]
            ):
                ignorados += 1
                continue

            nuevo_estado = MAPA_ESTADO_SEMYT.get(estado_texto)
            if nuevo_estado is None:
                print(f"[SEMyT] Estado desconocido '{estado_texto}' en acta {registro.acta}")
                errores += 1
                continue

            if registro.estado_semyt == nuevo_estado:
                sin_cambios += 1
                continue

            crud.aplicar_cambios_estado(db, registro, {"estado_semyt": nuevo_estado})
            actualizados += 1

    db.commit()
    return {
        "creadas": creadas,
        "ignoradas_en_creacion": ignoradas_en_creacion,
        "errores_en_creacion": errores_en_creacion,
        "actualizados": actualizados,
        "sin_cambios": sin_cambios,
        "ignorados": ignorados,
        "errores": errores,
    }