import os

from backend.configs import config
from backend.configs import sesiones
from backend.models.claves_contexto import ClavesContexto
from backend.models.credenciales import Credenciales
from sistemas.semyt.pages.login_page import LoginSemytPage
from backend.pasos.paso_base import PasoBase
from backend.services.autenticacion_service import AutenticacionService
from backend.utils.utils import Utilidades


class LoginSEMyTPaso(PasoBase):
    async def ejecutar(self, contexto):
        self.iniciar_paso()
        contexto_guardado = contexto.obtener(ClavesContexto.CONTEXTO_SEMYT)
        if contexto_guardado is not None:
            contexto.contexto_navegador = contexto_guardado
        else:
            contexto.contexto_navegador = await self._crear_contexto(contexto.navegador)
            contexto.guardar(ClavesContexto.CONTEXTO_SEMYT, contexto.contexto_navegador)
        pagina = await contexto.contexto_navegador.new_page()
        login_page = LoginSemytPage(pagina)
        autenticacion = AutenticacionService(login_page, log=print)
        print("Abriendo SEMyT...")
        await login_page.abrir_semyt_con_sesion()
        credenciales = Credenciales(
            usuario=config.SEMYT_USUARIO,
            contrasena=config.SEMYT_PASSWORD,
        )

        await autenticacion.asegurar_sesion_semyt(credenciales)
        Utilidades.asegurar_carpeta(sesiones.RUTA_SESION_SEMYT)
        await contexto.contexto_navegador.storage_state(
            path=sesiones.RUTA_SESION_SEMYT
        )
        await contexto.contexto_navegador.storage_state(
            path=sesiones.RUTA_SESION_COMPARTIDA
        )
        contexto.guardar(
            ClavesContexto.PAGINA_SEMYT,
            pagina,
        )
        self.finalizar_paso()

    async def _crear_contexto(self, navegador):
        ruta = (
            sesiones.ruta_contexto_inicial()
            or (
                sesiones.RUTA_SESION_SEMYT
                if os.path.exists(sesiones.RUTA_SESION_SEMYT)
                else None
            )
        )
        if ruta:
            return await navegador.new_context(
                accept_downloads=True,
                storage_state=ruta,
            )
        return await navegador.new_context(
            accept_downloads=True,
        )