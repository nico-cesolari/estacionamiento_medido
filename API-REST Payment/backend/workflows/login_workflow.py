from backend.workflows.workflow_base import WorkflowBase
from backend.pasos.login_semyt_paso import LoginSEMyTPaso
from backend.pasos.login_sigi_paso import LoginSIGIPaso

class LoginProyectoWorkflow(WorkflowBase):
    def __init__(self, navegador):
        pasos = [
            LoginSEMyTPaso(),
            LoginSIGIPaso(),
        ]
        super().__init__(navegador, pasos)
