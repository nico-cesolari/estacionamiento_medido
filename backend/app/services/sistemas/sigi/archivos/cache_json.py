# backend/app/services/sistemas/comun/cache_json.py
# -----------------------------------------------------------------------------
# Persistencia genérica de sets en JSON, para no re-consultar contra un
# sitio externo (SIGI/SEMyT) algo que una corrida anterior ya determinó
# (ej: "esta acta no tiene ningún match", "este expediente ya no existe").
#
# Mismo patrón que ya usaba backend/alta/cargar_actas_semyt.py
# (_cargar_set_json / _guardar_set_json), sacado de ahí para no
# reimplementarlo en cada script nuevo que necesite lo mismo.
#
# Uso típico:
#   ARCHIVO_SIN_MATCH = CARPETA_CACHE / "actas_sin_match_sigi.json"
#   sin_match = cargar_set_json(ARCHIVO_SIN_MATCH, "SIN-MATCH")
#   ...
#   guardar_set_json(ARCHIVO_SIN_MATCH, sin_match, "SIN-MATCH")
# -----------------------------------------------------------------------------
import json
from pathlib import Path
from typing import Set


def log(etiqueta: str, mensaje: str):
    print(f"[{etiqueta}] {mensaje}", flush=True)


def cargar_set_json(ruta: Path, etiqueta: str) -> Set[str]:
    """Lee un JSON con una lista de strings y la devuelve como set.
    Si el archivo no existe o está corrupto, arranca vacío (nunca revienta
    la corrida por un problema de caché)."""
    ruta = Path(ruta)
    if not ruta.exists():
        return set()
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return {str(v) for v in json.load(f)}
    except (json.JSONDecodeError, OSError) as exc:
        log(etiqueta, f"⚠️ No se pudo leer {ruta} ({exc}); se arranca vacío.")
        return set()


def guardar_set_json(ruta: Path, valores: Set[str], etiqueta: str):
    """Escritura completa, UNA sola vez, ordenada numéricamente cuando se
    puede (valores no numéricos se van al final sin romper)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    def _clave_orden(v: str):
        try:
            return (0, int(v))
        except ValueError:
            return (1, v)

    try:
        ordenados = sorted(valores, key=_clave_orden)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(ordenados, f, indent=2, ensure_ascii=False)
        log(etiqueta, f"💾 {len(valores)} valor(es) guardados en {ruta.name}")
    except OSError as exc:
        log(etiqueta, f"⚠️ No se pudo guardar {ruta}: {exc}")