# sistemas/sigi/pages/login_page.py
# -----------------------------------------------------------------------------
# Page Object async: login (vía CiDi) y detección de sesión activa en SIGI.
#
# RECUPERADO desde el historial de git (commit f6eb2ef,
# "API-REST Payment/backend/pages/login_page.py"), que tenía la mitad de
# SEMyT Y la mitad de SIGI juntas en una sola clase `LoginPage`. Cuando se
# separó la mitad de SEMyT hacia sistemas/semyt/pages/login_page.py
# (LoginSemytPage), el archivo original se borró completo -- la mitad de
# SIGI no había quedado movida a ningún lado todavía, así que se perdió
# hasta este momento. Este archivo la repone tal cual estaba, adaptada al
# mismo estilo que LoginSemytPage (self-contenida, sin importar el
# `config`/`Credenciales` de un solo proyecto).
#
# Diferencia respecto al original: `sesion_sigi_activa` e
# `iniciar_sesion_sigi_cidi` recibían `config.ROL_A_SELECCIONAR` importado
# directo de API-REST Payment. Acá `rol` se pasa como parámetro (como ya
# hacía `iniciar_sesion_sigi_cidi`), para no atar este módulo compartido a
# la configuración de un solo proyecto -- mismo criterio que
# LoginSemytPage.iniciar_sesion_semyt con `credenciales`.
# -----------------------------------------------------------------------------
from sistemas.comun.playwright_utils import asentar_sesion
from sistemas.sigi.rutas import SIGI_LOGIN, sigi_login_obligatorio


class LoginSigiPage:
    def __init__(self, page):
        self.page = page

    async def abrir_sigi_login(self):
        sigi_login_obligatorio()  # falla explícito y claro si falta SIGI_LOGIN
        await self.page.goto(SIGI_LOGIN, wait_until="domcontentloaded", timeout=60000)

    def _boton_ingresar_con_cidi(self):
        return self.page.get_by_role("main").get_by_role("button", name="Ingresar con CiDi")

    async def sesion_sigi_activa(self, rol: str) -> bool:
        """Además de decir si ya hay sesión, si hace falta reconfirmar el
        rol (la SPA a veces lo vuelve a pedir aunque el login ya esté
        hecho), lo hace acá mismo.

        `rol`: texto exacto del botón de rol a seleccionar (ej. variable
        ROL_SESION / config.ROL_A_SELECCIONAR de quien llame)."""
        boton_cidi = self._boton_ingresar_con_cidi()
        sin_boton_cidi = await boton_cidi.count() == 0
        if not sin_boton_cidi:
            boton_cidi_general = self.page.get_by_role("button", name="Ingresar con CiDi")
            sin_boton_cidi = await boton_cidi_general.count() == 0

        if sin_boton_cidi:
            boton_rol = self.page.get_by_role("button", name=rol)
            if await boton_rol.count() > 0:
                await boton_rol.click()
                await asentar_sesion(self.page)
        return sin_boton_cidi

    async def iniciar_sesion_sigi_cidi(self, credenciales, rol: str):
        """`credenciales`: objeto con .usuario y .contrasena (ver
        LoginSemytPage.iniciar_sesion_semyt para el mismo criterio)."""
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