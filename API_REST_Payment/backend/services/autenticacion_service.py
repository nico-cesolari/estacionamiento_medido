# services/autenticacion_service.py
# -----------------------------------------------------------------------------
# Orquesta el login en los dos sistemas. Único lugar que decide "sesión
# activa vs. hay que loguearse" — antes esa decisión estaba duplicada (y
# desincronizada) en LoginSEMyTPaso, pagos_runner_async y
# descargas_paralelas_paso.
# -----------------------------------------------------------------------------
from typing import Union

from backend.models.credenciales import Credenciales
from app.services.sistemas.semyt.pages.login_page import LoginSemytPage
from app.services.sistemas.sigi.pages.login_page import LoginSigiPage

class AutenticacionService:
    # Antes recibía un único "LoginPage" con los métodos de SEMyT y de SIGI
    # combinados. Ese archivo se separó en dos Page Objects (uno por
    # sistema, ver sistemas/semyt/pages/login_page.py y
    # sistemas/sigi/pages/login_page.py) -- acá se sigue aceptando
    # cualquiera de los dos, porque cada método de este servicio sólo usa
    # los del sistema que le corresponde (asegurar_sesion_semyt usa el
    # objeto SEMyT, asegurar_sesion_sigi usa el objeto SIGI).
    def __init__(self, login_page: Union[LoginSemytPage, LoginSigiPage], log=print):
        self.login_page = login_page
        self.log = log

    async def asegurar_sesion_semyt(self, credenciales: Credenciales):
        """Asume que la página ya está posicionada (ver
        LoginPage.abrir_semyt_con_sesion, llamado por quien arma el
        contexto). Si detecta que no hay sesión, hace el login completo."""
        if await self.login_page.sesion_semyt_activa():
            self.log("✅ [SEMyT] Sesión existente detectada. Login omitido.")
            return
        self.log("❌ [SEMyT] No hay sesión activa. Iniciando login...")
        await self.login_page.iniciar_sesion_semyt(credenciales)
        self.log("✅ [SEMyT] Login completado.")

    async def asegurar_sesion_sigi(self, credenciales: Credenciales, rol: str):
        await self.login_page.abrir_sigi_login()
        if await self.login_page.sesion_sigi_activa(rol):
            self.log("✅ [SIGI] Sesión existente detectada. Login omitido.")
            return
        self.log("❌ [SIGI] No hay sesión activa. Iniciando login CiDi...")
        await self.login_page.iniciar_sesion_sigi_cidi(credenciales, rol)
        self.log("✅ [SIGI] Login completado.")