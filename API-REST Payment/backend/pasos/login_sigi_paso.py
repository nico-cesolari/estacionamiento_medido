# pasos/login_sigi_paso.py
import os

from backend.configs import config
from backend.configs import sesiones
from backend.models.claves_contexto import ClavesContexto
from backend.models.credenciales import Credenciales
from sistemas.sigi.pages.login_page import LoginSigiPage
from backend.pasos.paso_base import PasoBase
from backend.services.autenticacion_service import AutenticacionService
from backend.utils.utils import Utilidades


class LoginSIGIPaso(PasoBase):
    async def ejecutar(self, contexto):
        self.iniciar_paso()
        if contexto.contexto_navegador is None:
            contexto.contexto_navegador = await self._crear_contexto(contexto.navegador)
            contexto.guardar(ClavesContexto.CONTEXTO_SIGI, contexto.contexto_navegador)

        page = await contexto.contexto_navegador.new_page()
        login_page = LoginSigiPage(page)
        autenticacion = AutenticacionService(login_page, log=print)

        print("Abriendo sitio municipalidad (CiDi)...")
        credenciales = Credenciales(usuario=config.SIGI_USUARIO, contrasena=config.SIGI_PASSWORD)
        await autenticacion.asegurar_sesion_sigi(credenciales, config.ROL_A_SELECCIONAR)

        Utilidades.asegurar_carpeta(sesiones.RUTA_SESION_SIGI)
        await contexto.contexto_navegador.storage_state(path=sesiones.RUTA_SESION_SIGI)
        await contexto.contexto_navegador.storage_state(path=sesiones.RUTA_SESION_COMPARTIDA)

        contexto.guardar(ClavesContexto.PAGINA_SIGI, page)
        self.finalizar_paso()

    async def _crear_contexto(self, navegador):
        ruta = sesiones.ruta_contexto_inicial() or (
            sesiones.RUTA_SESION_SIGI if os.path.exists(sesiones.RUTA_SESION_SIGI) else None
        )
        if ruta:
            return await navegador.new_context(accept_downloads=True, storage_state=ruta)
        return await navegador.new_context(accept_downloads=True)