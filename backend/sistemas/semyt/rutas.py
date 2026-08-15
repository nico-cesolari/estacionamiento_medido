# sistemas/semyt/rutas.py
# -----------------------------------------------------------------------------
# Antes esto estaba PARTIDO entre los dos proyectos:
#   - "API-REST Payment/backend/configs/rutas.py"          -> SEMYT_LOGIN
#   - "backend/sistemas/semyt/rutas/rutas_semyt.py"          -> URL_SEMYT
# con el agravante de que backend/sistemas/semyt/rutas/rutas_semyt.py
# además calculaba ARCHIVO_SESION apuntando cruzado a la carpeta de
# sesiones de "API-REST Payment" con dos ".parent.parent" encadenados.
#
# Acá queda todo junto. SEMYT_LOGIN es opcional (os.getenv, sin exigir):
# solo lo necesita el proyecto que hace LOGIN de verdad (API-REST Payment).
# El proyecto que sólo LEE datos con una sesión ya logueada (backend/,
# estacionamiento_medido) no necesita nunca esta variable, así que no
# tiene sentido reventar el arranque si no está seteada para él.
# -----------------------------------------------------------------------------
import os

# URL de la SPA de SEMyT donde vive la grilla de actas (scraping/lectura).
URL_SEMYT = os.getenv("SEMYT_URL", "https://ciudad.villamaria.gob.ar/#/actas")

# URL de login. Sólo la usa quien hace login real (ver
# sistemas/semyt/paginas/login_page.py). Puede venir vacía para quien
# solo lee datos con sesión ya guardada.
SEMYT_LOGIN = os.getenv("SEMYT_LOGIN")


def semyt_login_obligatorio() -> str:
    """Para quien SÍ necesita loguearse: falla explícito y claro si falta
    la variable, en vez de que el login reviente más adelante con un
    TypeError críptico por SEMYT_LOGIN=None."""
    if not SEMYT_LOGIN:
        raise RuntimeError(
            "SEMYT_LOGIN no está configurado. Definilo en el .env del "
            "proyecto que hace login (ver .env.ejemplo)."
        )
    return SEMYT_LOGIN