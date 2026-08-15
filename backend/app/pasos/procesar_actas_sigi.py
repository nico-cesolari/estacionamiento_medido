# """
# Paso combinado: alta + actualización de actas SIGI en una sola sesión de
# navegador (evita loguearse dos veces). No tiene lógica propia de negocio
# ni de scraping: sólo abre la sesión y delega en:

#   - backend/alta/llenar_actas_sigi.py::ejecutar_alta
#         completa expediente/estado de los registros que todavía no
#         pasaron por SIGI (busca por Patente y desambigua por Nº de acta).

#   - backend/update/actualizar_actas_sigi.py::ejecutar_actualizacion
#         revisa si cambió el estado de los registros que ya tienen
#         expediente (busca por Nº de expediente).

#   - backend/app/reglas/reglas_sigi.py
#         mapeos de texto -> Enum y reglas de negocio, usadas por los dos
#         pasos de arriba.

# Pensado para llamarse desde un endpoint o un scheduler, pasándole una
# sesión de DB ya abierta (mismo patrón que el resto de crud.py).
# """
# from sqlalchemy.orm import Session

# # AJUSTAR: import relativo porque este archivo vive DENTRO del paquete
# # "app" (backend/app/pasos/navegador.py es un módulo hermano). "alta" y
# # "update", en cambio, son paquetes de primer nivel fuera de "app"
# # (backend/{app,alta,update}/), por eso se importan en absoluto -- requiere
# # backend/ en PYTHONPATH. Adaptar si la estructura real del proyecto es otra.
# from .navegador import PaginaConSesion
# from alta.llenar_actas_sigi import ejecutar_alta, URL_SIGI, ARCHIVO_SESION
# from update.actualizar_actas_sigi import ejecutar_actualizacion


# async def procesar_actas_sigi(db: Session) -> dict:
#     """
#     Punto de entrada del paso combinado. Primero completa expediente/
#     estado de las actas que todavía no lo tienen (alta), después revisa si
#     cambió el estado de las que ya lo tienen (actualización) -- incluidas
#     las que acaban de recibir su expediente en el paso anterior, así se
#     corrobora su estado sin esperar a la próxima corrida -- reutilizando
#     la MISMA sesión de navegador para las dos cosas. Devuelve un resumen
#     para loguear/exponer en el endpoint.
#     """
#     async with PaginaConSesion(ARCHIVO_SESION, URL_SIGI) as page:
#         resumen_alta = await ejecutar_alta(db, page)
#         resumen_actualizacion = await ejecutar_actualizacion(db, page)

#     db.commit()

#     return {
#         "alta": resumen_alta,
#         "actualizacion": resumen_actualizacion,
#     }