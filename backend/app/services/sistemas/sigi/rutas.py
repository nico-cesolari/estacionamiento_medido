# sistemas/sigi/rutas.py
# -----------------------------------------------------------------------------
# URL de login de SIGI (CiDi). Sigue el mismo patrón que
# sistemas/semyt/rutas.py: SIGI_LOGIN es opcional (os.getenv, sin exigir)
# porque solo lo necesita el proyecto que hace LOGIN de verdad
# (API-REST Payment). El proyecto que solo LEE datos con una sesión ya
# logueada (backend/, estacionamiento_medido) no necesita esta variable.
#
# Reemplaza al placeholder vacío "sistemas/sigi/rutas/rutas_sigi.py" (0
# bytes, nunca se llegó a completar).
# -----------------------------------------------------------------------------
import os

SIGI_LOGIN = os.getenv("SIGI_LOGIN")


def sigi_login_obligatorio() -> str:
    """Para quien SÍ necesita loguearse: falla explícito y claro si falta
    la variable, en vez de que el login reviente más adelante con un
    TypeError críptico por SIGI_LOGIN=None."""
    if not SIGI_LOGIN:
        raise RuntimeError(
            "Falta la variable de entorno SIGI_LOGIN (necesaria para hacer "
            "login real en SIGI). Definila en el .env del proyecto que hace "
            "el login (API-REST Payment/backend/.env)."
        )
    return SIGI_LOGIN