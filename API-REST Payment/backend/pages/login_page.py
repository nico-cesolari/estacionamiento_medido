# pages/login_page.py
# -----------------------------------------------------------------------------
# Page Object async: encapsula TODOS los selectores para iniciar sesión en
# los dos sistemas y para detectar si ya hay una sesión activa. Es la ÚNICA
# fuente de verdad de estos selectores — antes existían 3 heurísticas
# distintas repartidas entre login_semyt_paso.py, pagos_runner_async.py y
# descargas_paralelas_paso.py.
# -----------------------------------------------------------------------------
from urllib.parse import urlsplit

from backend.configs import rutas, config
from backend.models.credenciales import Credenciales
from backend.utils.reintentos import asentar_sesion


class LoginPage:
    def __init__(self, page):
        self.page = page

    # --- SEMyT -----------------------------------------------------------

    def url_inicio_semyt(self) -> str:
        partes = urlsplit(rutas.SEMYT_LOGIN)
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

    async def iniciar_sesion_semyt(self, credenciales: Credenciales):
        await self.page.goto(rutas.SEMYT_LOGIN, wait_until="domcontentloaded", timeout=60000)
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

    # --- SIGI ----------------------------------------------------

    async def abrir_sigi_login(self):
        await self.page.goto(rutas.SIGI_LOGIN, wait_until="domcontentloaded", timeout=60000)

    def _boton_ingresar_con_cidi(self):
        boton_en_main = self.page.get_by_role("main").get_by_role("button", name="Ingresar con CiDi")
        return boton_en_main

    async def sesion_sigi_activa(self) -> bool:
        """Además de decir si ya hay sesión, si hace falta reconfirmar el
        rol (la SPA a veces lo vuelve a pedir aunque el login ya esté
        hecho), lo hace acá mismo."""
        boton_cidi = self._boton_ingresar_con_cidi()
        sin_boton_cidi = await boton_cidi.count() == 0
        if not sin_boton_cidi:
            boton_cidi_general = self.page.get_by_role("button", name="Ingresar con CiDi")
            sin_boton_cidi = await boton_cidi_general.count() == 0

        if sin_boton_cidi:
            boton_rol = self.page.get_by_role("button", name=config.ROL_A_SELECCIONAR)
            if await boton_rol.count() > 0:
                await boton_rol.click()
                await asentar_sesion(self.page)
        return sin_boton_cidi

    async def iniciar_sesion_sigi_cidi(self, credenciales: Credenciales, rol: str):
        boton_cidi = self.page.get_by_role("button", name="Ingresar con CiDi")
        await boton_cidi.first.click()
        await self.page.wait_for_selector("text=CUIL", timeout=30000)

        campos = self.page.locator("input")
        await campos.nth(2).fill(credenciales.usuario)
        await campos.nth(4).fill(credenciales.contrasena)
        await self.page.get_by_role("button", name="INGRESAR").click()

        boton_rol = self.page.get_by_role("button", name=rol)
        await boton_rol.wait_for(timeout=15000)
        await boton_rol.click()
        await asentar_sesion(self.page)