# services/actas_service.py
# -----------------------------------------------------------------------------
# Único lugar que sabe descargar un Excel de actas (SEMyT), sea para el
# incremental de "multas" o para cualquiera de los dos rangos de cruce de
# "pagos". Antes esta lógica estaba reescrita en pagos_runner_async.py y en
# descargas_paralelas_paso.py, cada una con su propio manejo de errores
# (una de las copias ni siquiera distinguía RangoDeFechasInvalido).
# -----------------------------------------------------------------------------
import os

from backend.orquestador.excepciones import NoHayActasParaActualizar, RangoDeFechasInvalido
from backend.pages.exportar_actas_page import (
    ExportarActasPage,
    CartelSinActasSemyt,
    CartelFechaInvalidaSemyt,
)
from backend.services.estado_service import EstadoService
from backend.services.excel_service import ExcelService
from backend.utils import fechas as utilidades_fecha
from backend.utils.utils import Utilidades


class ActasService:
    def __init__(
        self,
        exportar_actas_page: ExportarActasPage,
        estado_service: EstadoService,
        excel_service: ExcelService,
        carpeta_descargas: str,
        fecha_desde_forzada: str = None,
        fecha_hasta_forzada: str = None,
        log=print,
    ):
        self.exportar_actas_page = exportar_actas_page
        self.estado_service = estado_service
        self.excel_service = excel_service
        self.carpeta_descargas = carpeta_descargas
        self.fecha_desde_forzada = fecha_desde_forzada
        self.fecha_hasta_forzada = fecha_hasta_forzada
        self.log = log

    async def descargar_actas_pendientes(
        self, renombrar_por_rango: bool = True, nombre_archivo: str | None = None,
    ) -> str:
        os.makedirs(self.carpeta_descargas, exist_ok=True)

        fecha_desde = self._calcular_fecha_desde()
        fecha_hasta = self._calcular_fecha_hasta()

        self.log(f"Fecha Desde: {utilidades_fecha.fecha_a_texto(fecha_desde)}")
        self.log(f"Fecha Hasta: {utilidades_fecha.fecha_a_texto(fecha_hasta)}")

        await self.exportar_actas_page.abrir()
        await self.exportar_actas_page.completar_fechas(
            fecha_desde.strftime(utilidades_fecha.FORMATO_ALMACENAMIENTO),
            fecha_hasta.strftime(utilidades_fecha.FORMATO_ALMACENAMIENTO),
        )

        self.log("Descargando Excel...")
        try:
            descarga = await self.exportar_actas_page.descargar()
        except CartelSinActasSemyt as e:
            raise NoHayActasParaActualizar(str(e)) from None
        except CartelFechaInvalidaSemyt as e:
            raise RangoDeFechasInvalido(str(e)) from None

        if nombre_archivo is None:
            nombre_archivo = (
                f"MULTAS_VENCIDAS_DESDE_{fecha_desde:%Y%m%d}_HASTA_{fecha_hasta:%Y%m%d}.xlsx"
            )

        ruta_destino = await self._guardar_descarga(descarga, nombre_archivo)

        try:
            self.excel_service.validar_que_haya_actas(ruta_destino)
        except ValueError:
            self._eliminar_descarga_sin_actas(ruta_destino)
            raise NoHayActasParaActualizar("No se encontraron actas vencidas para este periodo.")

        if renombrar_por_rango:
            fecha_primera_acta, fecha_ultima_acta = self.excel_service.obtener_rango_fechas_actas(ruta_destino)
            ruta_destino = self._renombrar_descarga_por_rango_actas(ruta_destino, fecha_primera_acta, fecha_ultima_acta)

        return ruta_destino

    def confirmar_carga_exitosa(self, ruta_excel: str):
        self._actualizar_estado(ruta_excel)

    # --- helpers privados (sin cambios de lógica respecto a la versión sync) ---

    def _calcular_fecha_desde(self):
        if self.fecha_desde_forzada:
            return utilidades_fecha.texto_a_fecha(self.fecha_desde_forzada)
        estado = self.estado_service.leer()
        if estado.tiene_fecha_guardada():
            return utilidades_fecha.texto_a_fecha(estado.ultima_fecha_procesada, utilidades_fecha.FORMATO_ALMACENAMIENTO)
        return self._pedir_fecha_inicial_al_usuario()

    def _calcular_fecha_hasta(self):
        if self.fecha_hasta_forzada:
            return utilidades_fecha.texto_a_fecha(self.fecha_hasta_forzada)
        return utilidades_fecha.fecha_y_hora_actual()

    def _pedir_fecha_inicial_al_usuario(self):
        self.log("❌ No hay fecha previa guardada (primera ejecución).")
        fecha_texto = input("Ingresá la Fecha Desde inicial (dd/mm/aaaa): ").strip()
        if not utilidades_fecha.es_formato_valido(fecha_texto):
            raise ValueError(f"Formato de fecha inválido: '{fecha_texto}'. Debe ser 'dd/mm/aaaa'.")
        return utilidades_fecha.texto_a_fecha(fecha_texto)

    async def _guardar_descarga(self, descarga, nombre_archivo: str) -> str:
        ruta = os.path.join(self.carpeta_descargas, nombre_archivo)
        await descarga.save_as(ruta)
        self.log(f"Archivo descargado en: {Utilidades.ruta_para_log(ruta)}")
        return ruta

    def _renombrar_descarga_por_rango_actas(self, ruta_actual, fecha_primera, fecha_ultima) -> str:
        nombre_archivo = f"actas_{fecha_primera.strftime('%Y%m%d')}_{fecha_ultima.strftime('%Y%m%d')}.xlsx"
        ruta_nueva = self._ruta_disponible(os.path.join(self.carpeta_descargas, nombre_archivo))
        os.replace(ruta_actual, ruta_nueva)
        self.log(
            "Excel renombrado según actas: "
            f"primera {utilidades_fecha.fecha_a_texto(fecha_primera)}, "
            f"última {utilidades_fecha.fecha_a_texto(fecha_ultima)}."
        )
        self.log(f"Archivo final: {Utilidades.ruta_para_log(ruta_nueva)}")
        return ruta_nueva

    def _ruta_disponible(self, ruta: str) -> str:
        if not os.path.exists(ruta):
            return ruta
        base, extension = os.path.splitext(ruta)
        contador = 2
        while True:
            ruta_con_sufijo = f"{base}_{contador}{extension}"
            if not os.path.exists(ruta_con_sufijo):
                return ruta_con_sufijo
            contador += 1

    def _eliminar_descarga_sin_actas(self, ruta_excel: str):
        try:
            if ruta_excel and os.path.exists(ruta_excel):
                os.remove(ruta_excel)
                self.log("❌ No hay actas para actualizar. Se eliminó el Excel descargado.")
        except OSError as error:
            self.log(f"❌ No hay actas para actualizar, pero no se pudo eliminar el Excel: {error}")

    def _actualizar_estado(self, ruta_excel: str):
        try:
            proxima_fecha = self.excel_service.obtener_proxima_fecha_desde(ruta_excel)
            self.estado_service.guardar(proxima_fecha.strftime(utilidades_fecha.FORMATO_ALMACENAMIENTO))
            self.log(f"Próxima 'Fecha Desde' guardada para la siguiente corrida: {utilidades_fecha.fecha_a_texto(proxima_fecha)}")
        except ValueError as error:
            self.log(f"❌ No se pudo calcular la próxima fecha automáticamente ({error}).")
            self.log("❌ El estado NO se actualizó; revisar el Excel manualmente.")