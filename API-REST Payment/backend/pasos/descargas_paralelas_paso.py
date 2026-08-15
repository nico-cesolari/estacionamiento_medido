# pasos/descargas_paralelas_paso.py
# -----------------------------------------------------------------------------
# Baja EN PARALELO (asyncio.gather real) las 3 descargas de "pagos":
#   1. TXT de pagos (SIGI)
#   2. Excel SIGEMI (rango viejo)
#   3. Excel SIGI (rango nuevo)
# -----------------------------------------------------------------------------
import asyncio
import os
import time

from backend.configs import causas, config
from backend.configs import sesiones
from backend.models.claves_contexto import ClavesContexto
from backend.orquestador.excepciones import NoHayActasParaActualizar
from backend.pages.exportar_actas_page import ExportarActasPage
from backend.pasos.paso_base import PasoBase
from backend.services.actas_service import ActasService
from backend.services.estado_service import EstadoService
from backend.services.excel_service import ExcelService
from backend.services.pagos_service import PagosService
from backend.utils import fechas as utilidades_fecha
from backend.pages.login_page import LoginPage
from backend.pasos.descargar_pagos_completo_em_paso import DescargarPagosCompletoEmPaso

NOMBRE_SIGEMI = "MULTAS_SIGEMI_CRUCE.xlsx"
NOMBRE_SIGI = "MULTAS_SIGI_CRUCE.xlsx"


class DescargasParalelasPaso(PasoBase):
    async def ejecutar(self, contexto):
        self.iniciar_paso()

        if not os.path.exists(sesiones.RUTA_SESION_COMPARTIDA):
            raise RuntimeError(
                "No se encontró la sesión guardada (sesion_general.json). "
                "¿Corrió el login antes de este paso?"
            )

        contexto_navegador = contexto.contexto_navegador
        pagina_sigi = contexto.pagina_sigi
        pagina_semyt_viejas = await contexto_navegador.new_page()
        pagina_semyt_nuevas = await contexto_navegador.new_page()
        url_inicio_semyt = LoginPage(pagina_semyt_viejas).url_inicio_semyt()
        await pagina_semyt_viejas.goto(url_inicio_semyt, wait_until="domcontentloaded", timeout=60000)
        await pagina_semyt_nuevas.goto(url_inicio_semyt, wait_until="domcontentloaded", timeout=60000)
        print("\n📤 Arrancando las 3 descargas en paralelo (mismo browser, páginas separadas)...")
        inicio = time.perf_counter()

        resultados = await asyncio.gather(
            self._con_tiempo("pagos_txt", self._descargar_pagos(pagina_sigi)),
            self._con_tiempo("actas_sigemi", self._descargar_excel(
                pagina_semyt_viejas, NOMBRE_SIGEMI, "SIGEMI (rango viejo)",
                fecha_desde_texto=config.FECHA_INICIO_EM,
                fecha_hasta_texto=utilidades_fecha.fecha_a_texto(causas.FECHA_HASTA_MULTAS_VENCIDAS_VIEJAS),
            )),
            self._con_tiempo("actas_sigi", self._descargar_excel(
                pagina_semyt_nuevas, NOMBRE_SIGI, "SIGI (rango nuevo)",
                fecha_desde_texto=utilidades_fecha.fecha_a_texto(causas.FECHA_CAMBIO_SISTEMA),
                fecha_hasta_texto=None,
            )),
            return_exceptions=True,
        )
        print(f"⏱ TOTAL descargas en paralelo: {time.perf_counter() - inicio:.1f}s")

        await pagina_semyt_viejas.close()
        await pagina_semyt_nuevas.close()

        ruta_txt, ruta_sigemi, ruta_sigi = self._procesar_resultados(resultados)

        contexto.guardar(ClavesContexto.RUTA_TXT_PAGOS, ruta_txt)
        contexto.registrar_archivo(ruta_txt)

        contexto.guardar(ClavesContexto.RUTA_EXCEL_ACTAS_CRUCE_SIGEMI, ruta_sigemi)
        if ruta_sigemi:
            contexto.registrar_archivo(ruta_sigemi)

        contexto.guardar(ClavesContexto.RUTA_EXCEL_ACTAS_CRUCE_SIGI, ruta_sigi)
        if ruta_sigi:
            contexto.registrar_archivo(ruta_sigi)

        if not ruta_sigemi and not ruta_sigi:
            print(
                "❌ Ninguno de los dos rangos tiene multas vencidas ahora mismo. "
                "El cruce sigue con lo que haya en el TXT de pagos."
            )

        self.finalizar_paso()

    # --- las 3 tareas delegan TODO el detalle a services/pages compartidos ---

    async def _descargar_pagos(self, pagina) -> str:
        service = PagosService(config.CARPETA_DESCARGAS_PAGOS, log=print)
        try:
            return await service.descargar_txt_pagos(pagina)
        finally:
            await pagina.close()

    async def _descargar_excel(self, pagina, nombre_archivo, etiqueta, fecha_desde_texto, fecha_hasta_texto):
        actas_service = ActasService(
            exportar_actas_page=ExportarActasPage(pagina),
            estado_service=EstadoService(config.ARCHIVO_ESTADO),
            excel_service=ExcelService(),
            carpeta_descargas=config.CARPETA_CACHE_ACTAS_CRUCE,
            fecha_desde_forzada=fecha_desde_texto,
            fecha_hasta_forzada=fecha_hasta_texto,
            log=print,
        )
        os.makedirs(config.CARPETA_CACHE_ACTAS_CRUCE, exist_ok=True)
        try:
            return await actas_service.descargar_actas_pendientes(renombrar_por_rango=False, nombre_archivo=nombre_archivo)
        except NoHayActasParaActualizar as e:
            print(f"❌ {etiqueta}: {e}")
            raise

    # --- utilidades de orquestación (sin lógica de negocio) ---

    @staticmethod
    async def _con_tiempo(nombre, corutina):
        inicio = time.perf_counter()
        try:
            resultado = await corutina
            print(f"⏱ [{nombre}] completado en {time.perf_counter() - inicio:.1f}s")
            return resultado
        except Exception as error:
            print(f"⏱ [{nombre}] falló a los {time.perf_counter() - inicio:.1f}s -> {type(error).__name__}: {error}")
            raise

    @staticmethod
    def _procesar_resultados(resultados):
        """'No hay actas en ese rango' no es un error real: queda en None
        y el cruce sigue con lo que sí haya. El TXT de pagos SÍ es
        obligatorio: si falló, se relanza."""
        def _separar(r):
            return (None, r) if isinstance(r, Exception) else (r, None)

        ruta_txt, error_txt = _separar(resultados[0])
        ruta_sigemi, error_sigemi = _separar(resultados[1])
        ruta_sigi, error_sigi = _separar(resultados[2])

        if isinstance(error_sigemi, NoHayActasParaActualizar):
            error_sigemi = None
        if isinstance(error_sigi, NoHayActasParaActualizar):
            error_sigi = None

        if error_txt:
            raise error_txt
        if error_sigemi:
            raise error_sigemi
        if error_sigi:
            raise error_sigi

        return ruta_txt, ruta_sigemi, ruta_sigi