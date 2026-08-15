# sistemas/semyt/pages/login_page.py
# -----------------------------------------------------------------------------
# Page Object async: login y detección de sesión activa en SEMyT.
#
# Movido desde "API-REST Payment/backend/pages/login_page.py". Ese
# archivo tenía UNA sola clase `LoginPage` mezclando SEMyT y SIGI -- se
# separa acá la mitad de SEMyT. La mitad de SIGI se moverá a
# sistemas/sigi/pages/login_page.py en la próxima etapa; hasta entonces
# sigue viviendo en el proyecto original (ver shim en
# API-REST Payment/backend/pages/login_page.py).
# -----------------------------------------------------------------------------
from urllib.parse import urlsplit

from sistemas.semyt.rutas import URL_SEMYT, semyt_login_obligatorio
from sistemas.comun.playwright_utils import asentar_sesion  # noqa: F401  (re-exportado por compat)
# ^ arreglado: antes apuntaba a "sistemas.comun.playwright_utils" cuando el
# archivo todavía vivía en sistemas/semyt/comun/ (no existía sistemas/comun/
# como carpeta real). Ahora sí existe -- ver sistemas/comun/playwright_utils.py.

class LoginSemytPage:
    def __init__(self, page):
        self.page = page

    def url_inicio_semyt(self) -> str:
        partes = urlsplit(URL_SEMYT)
        return f"{partes.scheme}://{partes.netloc}/#/inicio"

    async def abrir_semyt_con_sesion(self):
        await self.page.goto(self.url_inicio_semyt(), wait_until="domcontentloaded", timeout=60000)

    async def sesion_semyt_activa(self) -> bool:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
            await self._esperar_indicador_sesion_o_login_semyt()

            campos_login = await self.page.locator("input[type='text'], input[type='password']").count()
            boton_ingresar = await self.page.get_by_role("button", name="Ingresar").count()
            if campos_login > 0 or boton_ingresar > 0 or "#/login" in self.page.url:
                return False

            token = await self.page.evaluate("() => localStorage.getItem('token')")
            if token:
                return True

            return "ciudad.villamaria.gob.ar" in self.page.url and "#/login" not in self.page.url
        except Exception as e:
            print(f"❌ No se pudo verificar la sesión guardada de SEMyT: {type(e).__name__}: {e}")
            return False

    async def _esperar_indicador_sesion_o_login_semyt(self):
        try:
            await self.page.wait_for_function(
                """() => {
                    const tieneToken = Boolean(localStorage.getItem('token'));
                    const estaEnLogin = window.location.href.includes('#/login');
                    const camposLogin = document.querySelectorAll(
                        "input[type='text'], input[type='password']"
                    ).length > 0;
                    const botonIngresar = Array.from(document.querySelectorAll('button'))
                        .some((boton) => boton.textContent.trim() === 'Ingresar');
                    return tieneToken || estaEnLogin || camposLogin || botonIngresar;
                }""",
                timeout=5000,
            )
        except Exception:
            pass

    async def iniciar_sesion_semyt(self, credenciales) -> None:
        """`credenciales`: objeto con .usuario y .contrasena (ej.
        models.Credenciales de API-REST Payment, o cualquier otro con
        esos dos atributos -- no se importa el tipo acá a propósito, para
        no atar este módulo compartido al modelo de un solo proyecto)."""
        semyt_login_obligatorio()  # falla explícito y claro si falta SEMYT_LOGIN
        from sistemas.semyt.rutas import SEMYT_LOGIN
        await self.page.goto(SEMYT_LOGIN, wait_until="domcontentloaded", timeout=60000)
        await self.page.locator("input[type='text']").fill(credenciales.usuario)
        await self.page.locator("input[type='password']").fill(credenciales.contrasena)
        await self.page.get_by_role("button", name="Ingresar").click()
        await self._esperar_login_completado_semyt()

    async def _esperar_login_completado_semyt(self):
        await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        try:
            await self.page.wait_for_function(
                """() => {
                    const tieneToken = Boolean(localStorage.getItem('token'));
                    const salioDelLogin = !window.location.href.includes('#/login');
                    return tieneToken || salioDelLogin;
                }""",
                timeout=5000,
            )
        except Exception:
            pass