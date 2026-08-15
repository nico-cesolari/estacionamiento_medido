#!/usr/bin/env python3
"""
FUNCIONAL
solucionar_foto_url.py
---------------------------
Recorre las actas que YA TIENEN foto_url cargada (por ejemplo con la
ruta vieja "/fotos/{acta}.png" de cuando se guardaba en disco), entra
a cada una en SEMyT, busca el <img alt="Imagen del Acta"> en la
página de detalle, y reemplaza foto_url por la URL absoluta que
devuelve la API (ej: https://ciudad.villamaria.gob.ar/media/actas/.../xxx.png).

No descarga ni escribe ningún archivo a disco: solo actualiza el
campo foto_url en la base de datos.

Por defecto corre en DRY-RUN (no escribe en la DB). Para grabar de
verdad, pasale --commit.

USO:
    cd backend
    python baja/solucionar_foto_url.py                     # dry-run
    python baja/solucionar_foto_url.py --commit
    python baja/solucionar_foto_url.py --commit --limit 50  # probar con pocas
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from app.models import models

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from sqlalchemy import cast, Integer, or_

from app.database import SessionLocal
from app.pasos.navegador import PaginaConSesion
from app.services.estados import aplicar_cambios_estado
from app.pasos.procesar_actas_semyt import (
    ARCHIVO_SESION,
    URL_SEMYT,
    SELECTOR_FILAS_RESULTADO,
    _leer_acta,
)
from app.reglas.reglas_semyt import obtener_url_foto_de_fila


def _registros_con_foto_existente(db: Session, limite: Optional[int]):
    """
    Actas que tienen foto_url cargada pero MAL (la ruta vieja de disco,
    ej. "/fotos/xxx.png", o cualquier valor que no sea ya una URL
    absoluta http/https). Las que ya tienen una URL absoluta correcta
    (ej. "https://ciudad.villamaria.gob.ar/media/actas/.../xxx.png") se
    excluyen: no hace falta tocarlas de nuevo.

    Además, solo actas SIN consistencia (`consistente` distinto de True,
    o sea False o pendiente/None) -- las que ya están consistentes se
    saltean, no hace falta re-solucionarles la foto.
    """
    query = (
        db.query(models.Registro)
        .filter(
            or_(
                models.Registro.foto_url.is_(None),
                models.Registro.foto_url == "",
                ~models.Registro.foto_url.ilike("http%"),
            )
        )
        .filter(models.Registro.consistente.isnot(True))
        .order_by(cast(models.Registro.acta, Integer))
    )
    if limite:
        query = query.limit(limite)
    return query.all()


async def _debug_listar_imagenes(page):
    """
    Diagnóstico: lista TODOS los <img> presentes en la página en ese
    momento (alt, src, y si están visibles) para entender por qué no
    matchea el selector esperado.
    """
    print(f"    [debug] page.url = {page.url}")
    todas = page.locator("img")
    total = await todas.count()
    print(f"    [debug] total <img> en la página: {total}")
    for i in range(total):
        img = todas.nth(i)
        alt = await img.get_attribute("alt")
        src = await img.get_attribute("src")
        visible = await img.is_visible()
        print(f"    [debug]   #{i}: alt={alt!r} src={src!r} visible={visible}")


async def cambio_de_url(db: Session, commit: bool, limite: Optional[int]):
    registros = _registros_con_foto_existente(db, limite)
    actualizados, sin_cambios, no_encontrados, sin_imagen, errores = 0, 0, 0, 0, 0

    async with PaginaConSesion(ARCHIVO_SESION, URL_SEMYT) as page:
        contexto = page.context
        for registro in registros:
            try:
                datos_acta = await _leer_acta(page, registro.acta)
            except Exception as exc:  # noqa: BLE001
                print(f"[SEMyT] Error leyendo acta {registro.acta}: {exc}")
                errores += 1
                continue

            if datos_acta is None:
                print(f"[SEMyT] Acta {registro.acta} no encontrada en la grilla")
                no_encontrados += 1
                continue

            # _leer_acta ya filtró y dejó la grilla mostrando esta fila,
            # así que la re-localizamos acá para pasársela a
            # obtener_url_foto_de_fila, que es la que clickea la lupa.
            fila = page.locator(SELECTOR_FILAS_RESULTADO).first

            try:
                nueva_url = await obtener_url_foto_de_fila(
                    contexto, page, fila, registro.acta, commit
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[SEMyT] Error abriendo imagen del acta {registro.acta}: {exc}")
                errores += 1
                continue

            if not nueva_url:
                print(f"[SEMyT] Acta {registro.acta} no tiene imagen en el detalle")
                sin_imagen += 1
                continue

            if nueva_url == registro.foto_url:
                sin_cambios += 1
                continue

            print(f"[SEMyT] Acta {registro.acta}: {registro.foto_url!r} -> {nueva_url!r}")
            if commit:
                aplicar_cambios_estado(db, registro, {"foto_url": nueva_url})
                db.commit()
            actualizados += 1

    if not commit:
        db.rollback()

    return {
        "actualizados": actualizados,
        "sin_cambios": sin_cambios,
        "sin_imagen_en_detalle": sin_imagen,
        "no_encontrados_en_semyt": no_encontrados,
        "errores": errores,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Refresca foto_url de actas que ya tenían una URL/ruta cargada, "
                    "tomando la URL real desde el detalle de SEMyT."
    )
    parser.add_argument("--commit", action="store_true", help="Graba en la DB (sin esto, dry-run)")
    parser.add_argument("--limit", type=int, default=None, help="Probar con sólo N actas")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        resumen = asyncio.run(cambio_de_url(db, commit=args.commit, limite=args.limit))
        modo = "COMMIT" if args.commit else "DRY-RUN (no se grabó nada)"
        print(f"\nModo: {modo}")
        for clave, valor in resumen.items():
            print(f"  {clave}: {valor}")
    finally:
        db.close()


if __name__ == "__main__":
    main()