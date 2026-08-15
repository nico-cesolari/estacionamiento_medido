# backend/workflows/workflow_base.py

from pathlib import Path
from typing import Callable

from backend.models.contexto_ejecucion import ContextoEjecucion
from backend.orquestador.excepciones import (
    EjecucionCancelada,
    NoHayActasParaActualizar,
    RangoDeFechasInvalido,
)
from backend.utils.utils import Utilidades


class WorkflowBase:
    def __init__(self, navegador, pasos):
        self.navegador = navegador
        self.pasos = pasos

    async def ejecutar(
        self,
        log: Callable[[str], None] = lambda _: None,
        archivo_fn: Callable[[str], None] | None = None,
        cerrar_navegador_al_final: bool = True,
    ) -> ContextoEjecucion:

        contexto = ContextoEjecucion(
            navegador=self.navegador,
            _log_fn=log,
            _archivo_fn=archivo_fn or (lambda _: None),
        )
        nombre_paso_actual = None

        try:
            for paso in self.pasos:
                nombre_paso_actual = paso.__class__.__name__
                log(f"▶ [{nombre_paso_actual}] Iniciando...")
                await paso.ejecutar(contexto)
                log(f"✓ [{nombre_paso_actual}] Completado.")
        except EjecucionCancelada:
            log("🛑 Cancelación recibida. Limpiando archivos generados en esta corrida...")
            self._limpiar_archivos(contexto, log)
            raise

        except NoHayActasParaActualizar as e:
            log("❌ ERROR en la descarga")
            log(f"ℹ {e}")

        except RangoDeFechasInvalido as e:
            log("❌ ERROR en la descarga")
            log("⚠ Motivo: Rango de fechas inválido.")
            log(str(e))

        except Exception as e:
            log(f"❌ Error en el paso [{nombre_paso_actual}]")
            log(f"{type(e).__name__}: {e}")
            raise

        finally:
            if cerrar_navegador_al_final:
                await self.navegador.close()
        return contexto

    @staticmethod
    def _limpiar_archivos(
        contexto: ContextoEjecucion,
        log: Callable[[str], None],
    ) -> None:
        for ruta in contexto.archivos_creados:
            if not ruta:
                continue
            try:
                ruta = Path(ruta)
                if ruta.exists():
                    ruta.unlink()
                    log(f"🧹 Archivo eliminado: {Utilidades.ruta_para_log(str(ruta))}")
            except OSError as e:
                log(f"❌ No se pudo eliminar {Utilidades.ruta_para_log(str(ruta))}: {e}")
