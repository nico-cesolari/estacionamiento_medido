# sistemas/sigemi/archivos/rutas_archivos_sigemi.py
# -----------------------------------------------------------------------------
# Placeholder que había quedado sin completar (dos variables sin valor
# asignado = SyntaxError apenas alguien lo importe). Nadie lo usa todavía
# (por eso no había roto nada en ejecución); lo completo con las rutas a
# los .txt que ya viven al lado, siguiendo el mismo patrón que
# sistemas/sigi/rutas.py y sistemas/semyt/rutas.py.
#
# ⚠️ Revisar: infiero el nombre de archivo -> constante por convención
# (TXT_PAGOS_SIGEMI -> total_pagos_em_sigemi.txt, TXT_MULTAS_EM_COMPLETAS
# -> total_em_sigemi.txt). Si el uso real que le ibas a dar era otro,
# ajustá acá antes de conectarlo a algo.
# -----------------------------------------------------------------------------
import os
_CARPETA = os.path.dirname(os.path.abspath(__file__))

TXT_PAGOS_SIGEMI = os.path.join(_CARPETA, "total_pagos_em_sigemi.txt")
TXT_MULTAS_EM_COMPLETAS = os.path.join(_CARPETA, "total_em_sigemi.txt")