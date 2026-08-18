from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..schemas import schemas

from ..models import models

from ..services import registros as registros_service
from ..services import exportacion
from ..database import get_db

router = APIRouter(prefix="/api/registros", tags=["registros"])

@router.get("", response_model=schemas.RegistrosPage)
def listar_registros(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    estado_sigemi: Optional[models.EstadoSigemi] = None,
    estado_semyt: Optional[models.EstadoSemyt] = None,
    estado_sigi: Optional[models.EstadoSigi] = None,
    motivo_archivo: Optional[str] = None,   # <-- ya no son 2 params tipados por enum, es 1 string libre
    juzgado: Optional[int] = None,
    expediente: Optional[str] = None,
    acta: Optional[str] = None,
    causa: Optional[str] = None,
    patente: Optional[str] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    consistencia: Optional[str] = None,
    solo_duplicadas: Optional[bool] = None,
    solo_reescritas: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    resultados, total, total_pages = registros_service.buscar_registros(
        db,
        page=page,
        page_size=page_size,
        estado_sigemi=estado_sigemi.value if estado_sigemi else None,
        estado_semyt=estado_semyt.value if estado_semyt else None,
        estado_sigi=estado_sigi.value if estado_sigi else None,
        motivo_archivo=motivo_archivo,
        juzgado=juzgado,
        expediente=expediente,
        acta=acta,
        causa=causa,
        patente=patente,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        consistencia=consistencia,
        solo_duplicadas=solo_duplicadas,
        solo_reescritas=solo_reescritas,
    )
    return schemas.RegistrosPage(
        total=total, page=page, page_size=page_size,
        total_pages=total_pages, resultados=resultados,
    )


@router.get("/filtros", response_model=schemas.FiltrosOptions)
def opciones_de_filtro():
    return schemas.FiltrosOptions(
        estados_sigemi=[e.value for e in models.EstadoSigemi],
        motivos_archivo_sigemi=[e.value for e in models.MotivoArchivoSigemi],
        estados_semyt=[e.value for e in models.EstadoSemyt],
        estados_sigi=[e.value for e in models.EstadoSigi],
        motivos_archivo_sigi=[e.value for e in models.MotivoArchivoSigi],
    )


@router.patch("/{registro_id}", response_model=schemas.RegistroOut)
def actualizar_registro(registro_id: int, cambios: schemas.RegistroUpdate, db: Session = Depends(get_db)):
    registro = registros_service.actualizar_estados(db, registro_id, cambios.model_dump(exclude_unset=True))
    if not registro:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    return registro


@router.post("/{registro_id}/refrescar", response_model=schemas.RegistroOut)
def refrescar_registro(registro_id: int, db: Session = Depends(get_db)):
    """
    Placeholder para el botón de refrescar (icono circular) de cada fila.
    Acá es donde, más adelante, engancharías una consulta en vivo contra
    SIGEMI / SEMyT / SIGI (o contra tu proyectoJuzgado) para traer el
    estado más reciente de ese expediente puntual.
    """
    registro = registros_service.obtener_registro(db, registro_id)
    if not registro:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    # TODO: reemplazar por la consulta real a los 3 sistemas de origen.
    return registro


# ---------------------------------------------------------------------------
# Exportar Actas: reporte .txt con filtros libres (por cualquier campo del
# acta, en modo "coincide" o "no coincide") + rango de fechas del acta,
# incluye todos los datos menos la foto del vehículo.
# ---------------------------------------------------------------------------

@router.get("/exportar/campos", response_model=schemas.CamposExportablesResponse)
def campos_exportables():
    """Lista de campos disponibles para armar filtros del reporte, con su tipo."""
    enum_por_campo = {
        "estado_sigemi": models.EstadoSigemi,
        "motivo_archivo_sigemi": models.MotivoArchivoSigemi,
        "estado_semyt": models.EstadoSemyt,
        "estado_sigi": models.EstadoSigi,
        "motivo_archivo_sigi": models.MotivoArchivoSigi,
    }
    campos = []
    for clave, info in exportacion.CAMPOS_EXPORTABLES.items():
        opciones = None
        if info["tipo"] == "estado":
            opciones = [e.value for e in enum_por_campo[clave]]
        campos.append(
            schemas.CampoExportable(
                campo=clave, etiqueta=info["etiqueta"], tipo=info["tipo"], opciones=opciones
            )
        )
    return schemas.CamposExportablesResponse(campos=campos)


@router.post("/exportar/contar", response_model=schemas.ExportarConteo)
def exportar_contar(body: schemas.ExportarRequest, db: Session = Depends(get_db)):
    """Cuenta cuántas actas coinciden con los filtros, para mostrar antes de descargar."""
    total = exportacion.contar_para_exportar(
        db,
        [f.model_dump() for f in body.filtros],
        fecha_desde=body.fecha_desde,
        fecha_hasta=body.fecha_hasta,
    )
    return schemas.ExportarConteo(total=total)


@router.post("/exportar/txt")
def exportar_txt(body: schemas.ExportarRequest, db: Session = Depends(get_db)):
    """Genera y descarga el reporte .txt con todos los datos de cada acta (sin la foto)."""
    filtros = [f.model_dump() for f in body.filtros]
    registros = exportacion.buscar_para_exportar(
        db, filtros, fecha_desde=body.fecha_desde, fecha_hasta=body.fecha_hasta
    )
    contenido = exportacion.generar_reporte_txt(
        registros, filtros, fecha_desde=body.fecha_desde, fecha_hasta=body.fecha_hasta
    )
    nombre = f"actas_estacionamiento_medido_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    return PlainTextResponse(
        content=contenido,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
    
@router.get("/exportar/consistencia-sigi")
def exportar_consistencia_sigi(db: Session = Depends(get_db)):
    """Reporte .txt: EXPEDIENTE|NUMERO_ACTA|ESTADO_SEMYT|ESTADO_SIGEMI|
    ESTADO_SIGI|CONSISTENCIA|DETERMINACION_FINAL, sólo para actas con
    estado_sigi cargado. DETERMINACION_FINAL = 'Archivar' cuando
    corresponde (ver services/consistencia.py::REGLAS_ARCHIVAR_SIGI)."""
    registros = exportacion.buscar_para_consistencia_sigi(db)
    contenido = exportacion.generar_reporte_consistencia_sigi(registros)
    nombre = f"Inconsistencia_SIGI_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    return PlainTextResponse(
        content=contenido,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
    
@router.get("/exportar/reescritas-sigi")
def exportar_reescritas_sigi(db: Session = Depends(get_db)):
    """Reporte .txt: actas Reescritas (mismo vehículo, mismo día y misma
    dirección, con número de acta distinto). Marca REESCRITA=True, aclara
    si la fila es la acta Original o la Nueva que la reescribió, y
    DETERMINACION_FINAL=Archivar siempre."""
    registros = exportacion.buscar_para_reescritas_sigi(db)
    if not registros:
        raise HTTPException(status_code=404, detail="No hay actas inconsistentes")
    contenido = exportacion.generar_reporte_reescritas_sigi(registros)
    nombre = f"Reescritas_SIGI_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    return PlainTextResponse(
        content=contenido,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/exportar/duplicadas-sigi")
def exportar_duplicadas_sigi(db: Session = Depends(get_db)):
    """Reporte .txt: actas Duplicadas (mismo número de acta en más de un
    registro). Marca DUPLICADA=True, aclara si la fila es la Original o
    la Nueva/duplicada, y DETERMINACION_FINAL=Archivar siempre."""
    registros = exportacion.buscar_para_duplicadas_sigi(db)
    if not registros:
        raise HTTPException(status_code=404, detail="No hay actas inconsistentes")
    contenido = exportacion.generar_reporte_duplicadas_sigi(registros)
    nombre = f"Duplicadas_SIGI_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    return PlainTextResponse(
        content=contenido,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )