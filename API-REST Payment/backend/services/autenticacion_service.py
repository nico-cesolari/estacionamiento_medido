# services/autenticacion_service.py
# -----------------------------------------------------------------------------
# Orquesta el login en los dos sistemas. Único lugar que decide "sesión
# activa vs. hay que loguearse" — antes esa decisión estaba duplicada (y
# desincronizada) en LoginSEMyTPaso, pagos_runner_async y
# descargas_paralelas_paso.
# -----------------------------------------------------------------------------
from backend.models.credenciales import Credenciales
from backend.pages.login_page import LoginPage

class AutenticacionService:
    def __init__(self, login_page: LoginPage, log=print):
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
        if await self.login_page.sesion_sigi_activa():
            self.log("✅ [SIGI] Sesión existente detectada. Login omitido.")
            return
        self.log("❌ [SIGI] No hay sesión activa. Iniciando login CiDi...")
        await self.login_page.iniciar_sesion_sigi_cidi(credenciales, rol)
        self.log("✅ [SIGI] Login completado.")