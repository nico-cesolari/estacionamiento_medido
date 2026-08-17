# pasos/descargar_pagos_completo_em_paso.py
# -----------------------------------------------------------------------------
# Único dueño de "cómo se descarga el TXT de pagos desde el SIGI" dentro de
# DescargasParalelasPaso. Se ejecuta como una de las 3 tareas corridas con
# asyncio.gather, por eso no extiende PasoBase ni recibe el ContextoEjecucion
# completo: solo necesita la página del SIGI ya logueada.
# -----------------------------------------------------------------------------
from backend.configs import config
from backend.services.pagos_service import PagosService

class DescargarPagosCompletoEmPaso:
    async def ejecutar(self, pagina) -> str:
        service = PagosService(config.CARPETA_DESCARGAS_PAGOS, log=print)
        try:
            return await service.descargar_txt_pagos(pagina)
        finally:
            await pagina.close()