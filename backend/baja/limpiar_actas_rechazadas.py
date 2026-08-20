#!/usr/bin/env python3
"""
limpiar_actas_rechazadas.py
----------------------------

Busca registros que tengan:

    estado_semyt = rechazada

Y que tanto SIGEMI como SIGI estén:

    - NULL / None
    - no_cargada

Antes de eliminar cada registro, guarda su número de acta en:

    backend/app/services/sistemas/semyt/archivos/
    actas_ignoradas_semyt.json

De esta forma, cuando posteriormente se ejecuta cargar_actas_semyt,
esas actas rechazadas pueden ser ignoradas y no volver a cargarse.

USO:

    cd backend

    # Solo muestra qué haría.
    python baja/limpiar_actas_rechazadas.py

    # Elimina los registros y agrega las actas al JSON.
    python baja/limpiar_actas_rechazadas.py --commit
"""

import argparse
import json
import sys
from pathlib import Path


# ============================================================
# PATHS
# ============================================================

BACKEND_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(BACKEND_DIR))

ARCHIVO_ACTAS_IGNORADAS = (
    BACKEND_DIR
    / "app"
    / "services"
    / "sistemas"
    / "semyt"
    / "archivos"
    / "actas_ignoradas_semyt.json"
)


from app.database import SessionLocal
from app.models import models


# ============================================================
# HELPERS
# ============================================================

def _vacio(valor) -> bool:
    """
    Considera vacío:
        None
        ""
        "   "
    """
    return (
        valor is None
        or (
            isinstance(valor, str)
            and valor.strip() == ""
        )
    )


def _estado_no_cargado(valor) -> bool:
    """
    Devuelve True si el estado está:

        - None
        - NULL
        - vacío
        - no_cargada

    Funciona tanto si el valor viene como Enum como si viene como string.
    """

    if valor is None:
        return True

    if hasattr(valor, "value"):
        valor = valor.value

    if isinstance(valor, str):
        valor = valor.strip().lower()

        return valor in (
            "",
            "no_cargada",
            "none",
            "null",
        )

    return False


# ============================================================
# BUSCAR CANDIDATOS
# ============================================================

def _candidatos(db):
    """
    Actas rechazadas en SEMyT que:

    - No tienen expediente
    - No tienen causa
    - SIGEMI está NULL / None / no_cargada
    - SIGI está NULL / None / no_cargada
    """

    registros = (
        db.query(models.Registro)
        .filter(
            models.Registro.estado_semyt
            == models.EstadoSemyt.rechazada
        )
        .all()
    )

    return [
        registro
        for registro in registros
        if (
            _vacio(registro.expediente)
            and _vacio(registro.causa)
            and _estado_no_cargado(registro.estado_sigemi)
            and _estado_no_cargado(registro.estado_sigi)
        )
    ]


# ============================================================
# JSON DE ACTAS IGNORADAS
# ============================================================

def cargar_actas_ignoradas():
    """
    Lee el JSON actual.

    Si no existe, devuelve una lista vacía.
    """

    if not ARCHIVO_ACTAS_IGNORADAS.exists():
        return []

    try:
        with open(
            ARCHIVO_ACTAS_IGNORADAS,
            "r",
            encoding="utf-8",
        ) as archivo:

            datos = json.load(archivo)

            if not isinstance(datos, list):
                print(
                    "[ADVERTENCIA] El JSON no contiene una lista. "
                    "Se utilizará una lista vacía."
                )
                return []

            return datos

    except json.JSONDecodeError as error:

        print(
            f"[ERROR] El archivo JSON tiene un formato inválido:\n"
            f"{ARCHIVO_ACTAS_IGNORADAS}\n"
            f"{error}"
        )

        raise


def guardar_actas_ignoradas(actas_nuevas):
    """
    Agrega las nuevas actas al JSON sin duplicados.
    """

    actas_existentes = cargar_actas_ignoradas()

    # Normalizamos todo como string.
    existentes = {
        str(acta).strip()
        for acta in actas_existentes
        if acta is not None
    }

    nuevas = {
        str(acta).strip()
        for acta in actas_nuevas
        if not _vacio(acta)
    }

    # Unión de las existentes + nuevas.
    todas = existentes | nuevas

    # Intentamos ordenar numéricamente.
    def clave_orden(acta):
        try:
            return (0, int(acta))
        except ValueError:
            return (1, acta)

    actas_ordenadas = sorted(
        todas,
        key=clave_orden,
    )

    # Crear carpeta si no existe.
    ARCHIVO_ACTAS_IGNORADAS.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with open(
        ARCHIVO_ACTAS_IGNORADAS,
        "w",
        encoding="utf-8",
    ) as archivo:

        json.dump(
            actas_ordenadas,
            archivo,
            ensure_ascii=False,
            indent=2,
        )

        archivo.write("\n")

    return {
        "existentes": len(existentes),
        "nuevas": len(nuevas - existentes),
        "total": len(actas_ordenadas),
    }


# ============================================================
# LIMPIEZA
# ============================================================

def limpiar(db, commit: bool):

    candidatos = _candidatos(db)

    actas_a_ignorar = []

    print()

    for registro in candidatos:

        acta = registro.acta

        print(
            f"{'[COMMIT]' if commit else '[DRY-RUN]'} "
            f"acta={acta} | "
            f"patente={registro.patente} | "
            f"SEMyT={registro.estado_semyt} | "
            f"SIGEMI={registro.estado_sigemi} | "
            f"SIGI={registro.estado_sigi}"
        )

        if not _vacio(acta):
            actas_a_ignorar.append(
                str(acta).strip()
            )

    # ========================================================
    # DRY RUN
    # ========================================================

    if not commit:

        print("\n[DRY-RUN] No se eliminó ningún registro.")
        print("[DRY-RUN] No se modificó el archivo JSON.")

        return {
            "actas_encontradas": len(candidatos),
            "actas_a_eliminar": len(candidatos),
            "actas_a_agregar_al_json": len(
                set(actas_a_ignorar)
            ),
        }

    # ========================================================
    # COMMIT
    # ========================================================

    try:

        # ----------------------------------------------------
        # 1. Actualizar JSON
        # ----------------------------------------------------

        resultado_json = guardar_actas_ignoradas(
            actas_a_ignorar
        )

        print(
            f"\n[JSON] Archivo actualizado:"
            f"\n  {ARCHIVO_ACTAS_IGNORADAS}"
        )

        print(
            f"  Actas existentes: {resultado_json['existentes']}"
        )

        print(
            f"  Actas nuevas agregadas: "
            f"{resultado_json['nuevas']}"
        )

        print(
            f"  Total de actas ignoradas: "
            f"{resultado_json['total']}"
        )

        # ----------------------------------------------------
        # 2. Eliminar registros
        # ----------------------------------------------------

        for registro in candidatos:

            db.delete(registro)

        # ----------------------------------------------------
        # 3. Confirmar base de datos
        # ----------------------------------------------------

        db.commit()

        return {
            "actas_encontradas": len(candidatos),
            "actas_eliminadas": len(candidatos),
            "actas_nuevas_agregadas_al_json":
                resultado_json["nuevas"],
            "total_actas_ignoradas":
                resultado_json["total"],
        }

    except Exception:

        db.rollback()

        print(
            "\n[ERROR] Ocurrió un error. "
            "Se hizo rollback de la base de datos."
        )

        raise


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Elimina actas rechazadas en SEMyT cuando SIGEMI "
            "y SIGI están en NULL/no_cargada, y agrega sus "
            "números al JSON de actas ignoradas."
        )
    )

    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Elimina los registros y actualiza el JSON. "
            "Sin esto solo realiza un dry-run."
        ),
    )

    args = parser.parse_args()

    db = SessionLocal()

    try:

        resumen = limpiar(
            db,
            commit=args.commit,
        )

        modo = (
            "COMMIT (se eliminaron registros y se actualizó el JSON)"
            if args.commit
            else
            "DRY-RUN (no se eliminó ni modificó nada)"
        )

        print()
        print("=" * 60)
        print(f"Modo: {modo}")
        print("=" * 60)

        for clave, valor in resumen.items():

            print(
                f"  {clave}: {valor}"
            )

    finally:

        db.close()


if __name__ == "__main__":
    main()