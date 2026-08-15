# workflows/pagos_workflow.py
# Único proceso de negocio de esta API: actualizar multas vencidas a
# pagadas. Corre automático cada 1 hora (ver orquestador/programador.py) o
# manual desde el panel (opción "1"):
#   1. Validar/reutilizar sesiones SEMyT y SIGI.
#   2. Descargar TXT de pagos (SIGI) Y los dos Excel de actas vencidas
#      usados para el cruce (SEMyT) AL MISMO TIEMPO, en paralelo (ver
#      pasos/descargas_paralelas_paso.py): son páginas (Page) distintas,
#      así que no compiten entre sí.
#   3. Cruzar pagos contra actas y causas SIGEMI -> arma el TXT final.
#   4. Cargar ese TXT final (ya cruzado) al SEMyT, lo que marca esas
#      multas como pagadas.
#
# El TXT que se sube al SEMyT es siempre el que sale del cruce, no el TXT
# crudo del SIGI.
from backend.pasos.cargar_pagos_paso import CargarPagosPaso
from backend.pasos.comparar_actas_paso import CompararActasPaso
from backend.pasos.descargas_paralelas_paso import DescargasParalelasPaso
from backend.workflows.login_workflow import LoginProyectoWorkflow
from backend.workflows.workflow_base import WorkflowBase


def pasos_negocio():
    """Pasos propios de 'pagos', sin login."""
    return [
        DescargasParalelasPaso(),
        CompararActasPaso(),
        CargarPagosPaso(),
    ]

class PagosWorkflow(WorkflowBase):
    def __init__(self, navegador):
        login = LoginProyectoWorkflow(navegador)
        pasos = [*login.pasos, *pasos_negocio()]
        super().__init__(navegador, pasos)
