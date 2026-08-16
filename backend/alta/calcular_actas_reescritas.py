#!/usr/bin/env python3
"""
calcular_actas_reescritas.py

Detecta actas "reescritas": mismo vehículo (patente), mismo día de labrado
y misma dirección, pero con distinto número de acta (y normalmente distinto
expediente). A diferencia de un acta simplemente "duplicada" (mismo número
de acta repetido, que ya se filtra en la grilla con el checkbox existente),
esto detecta cuando alguien volvió a labrar un acta nueva sobre el mismo
hecho.

Qué hace:
  1. Agrupa todos los registros por (patente normalizada, día de
     fecha_hora, dirección normalizada).
  2. De esos grupos, se queda con los que tienen más de una fila Y más de
     un número de acta distinto entre esas filas (esa segunda condición es
     la que descarta el caso "mismo acta repetida", que es otro problema).
  3. Marca `reescrita = True` y `grupo_reescritura = <clave>` en cada fila
     de esos grupos (columnas agregadas en models.Registro).
  4. Limpia (`reescrita = None`) cualquier fila que había quedado marcada
     en una corrida anterior y ya no corresponde (ej: se borró una de las
     actas del grupo).
  5. Imprime un resumen por consola y, opcionalmente, escribe un reporte
     .txt con el detalle de cada grupo encontrado.

No hace falta setear nada a mano: una vez que las columnas `reescrita` /
`grupo_reescritura` existen en la base (correr la migración -- ver abajo),
alcanza con ejecutar este script. Se puede correr las veces que haga falta
(es idempotente) y conviene dejarlo como tarea programada (cron / tarea
programada de Windows) para que el filtro "Actas reescritas" de la grilla
esté siempre al día.

USO
---
    python calcular_actas_reescritas.py                  # calcula y comitea
    python calcular_actas_reescritas.py --dry-run         # sólo muestra qué haría, no escribe nada
    python calcular_actas_reescritas.py --reporte out.txt # además guarda el detalle en un .txt

ANTES DE CORRERLO POR PRIMERA VEZ
----------------------------------
Las columnas `reescrita` y `grupo_reescritura` son nuevas en models.Registro.
Hace falta que existan en la base antes de correr el script:

    # Con Alembic (recomendado si ya lo usás en el proyecto):
    alembic revision --autogenerate -m "agrega reescrita y grupo_reescritura a registros"
    alembic upgrade head

    # Sin Alembic (rápido, para desarrollo/una sola vez):
    python -c "from app.database import engine; from app import models; models.Base.metadata.create_all(bind=engine)"
    # OJO: create_all sólo crea tablas que no existen, NO agrega columnas
    # nuevas a una tabla ya creada. Si `registros` ya existe, hace falta un
    # ALTER TABLE manual:
    #   ALTER TABLE registros ADD COLUMN reescrita BOOLEAN;
    #   ALTER TABLE registros ADD COLUMN grupo_reescritura VARCHAR;
    #   CREATE INDEX ix_registros_reescrita ON registros (reescrita);
    #   CREATE INDEX ix_registros_grupo_reescritura ON registros (grupo_reescritura);
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path


def _obtener_session_local():
    from app.database import SessionLocal
    return SessionLocal

def _obtener_calcular_actas_reescritas():
    from app.services.duplicados import calcular_actas_reescritas
    return calcular_actas_reescritas

def generar_reporte_txt(resultado: dict, ruta: Path):
    lineas = [
        f"Reporte de actas reescritas -- generado {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"Grupos encontrados: {resultado['grupos_encontrados']}",
        f"Actas marcadas como reescritas: {resultado['actas_marcadas']}",
        "",
        "=" * 70,
        "",
    ]
    for i, grupo in enumerate(resultado["detalle_grupos"], start=1):
        lineas.append(f"Grupo {i}")
        lineas.append(f"  Patente:    {grupo['patente']}")
        lineas.append(f"  Día:        {grupo['dia']}")
        lineas.append(f"  Dirección:  {grupo['direccion']}")
        lineas.append(f"  Actas:      {', '.join(str(a) for a in grupo['actas'])}")
        lineas.append(f"  Expedientes:{', '.join(str(e) if e else 'Sin expediente' for e in grupo['expedientes'])}")
        lineas.append("")

    ruta.write_text("\n".join(lineas), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Calcula y persiste las actas reescritas (mismo vehículo, día y dirección, distinto número de acta)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calcula y muestra el resultado, pero hace rollback en vez de commit (no modifica la base).",
    )
    parser.add_argument(
        "--reporte",
        type=str,
        default=None,
        help="Ruta de un .txt donde además guardar el detalle de cada grupo encontrado.",
    )
    args = parser.parse_args()

    SessionLocal = _obtener_session_local()
    calcular_actas_reescritas = _obtener_calcular_actas_reescritas()

    db = SessionLocal()
    try:
        print("Calculando actas reescritas...")
        resultado = calcular_actas_reescritas(db)

        if args.dry_run:
            db.rollback()
            print("(--dry-run: no se guardó nada en la base)")
        else:
            db.commit()

        print()
        print(f"Grupos de actas reescritas encontrados: {resultado['grupos_encontrados']}")
        print(f"Actas marcadas como reescritas:          {resultado['actas_marcadas']}")
        print(f"Actas desmarcadas (ya no aplican):       {resultado['actas_desmarcadas']}")

        if resultado["grupos_encontrados"]:
            print()
            print("Detalle:")
            for grupo in resultado["detalle_grupos"]:
                actas = ", ".join(str(a) for a in grupo["actas"])
                print(f"  - Patente {grupo['patente']} | {grupo['dia']} | {grupo['direccion']} -> actas: {actas}")

        if args.reporte:
            ruta = Path(args.reporte)
            generar_reporte_txt(resultado, ruta)
            print()
            print(f"Reporte guardado en: {ruta.resolve()}")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()