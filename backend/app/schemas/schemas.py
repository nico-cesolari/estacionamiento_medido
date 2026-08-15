from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel, ConfigDict

from ..models.models import EstadoSigemi, EstadoSemyt, EstadoSigi, MotivoArchivoSigemi, MotivoArchivoSigi


class RegistroOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    juzgado: Optional[int] = None
    expediente: Optional[str] = None
    acta: str
    causa: Optional[str] = None
    patente: str
    direccion: Optional[str] = None
    fecha_hora: Optional[datetime] = None
    foto_url: Optional[str] = None
    estado_sigemi: Optional[EstadoSigemi] = None
    motivo_archivo_sigemi: Optional[MotivoArchivoSigemi] = None
    estado_semyt: Optional[EstadoSemyt] = None
    estado_sigi: Optional[EstadoSigi] = None
    motivo_archivo_sigi: Optional[MotivoArchivoSigi] = None
    fecha_cobro_sigemi: Optional[datetime] = None
    fecha_cobro_sigi: Optional[datetime] = None

    # Campos calculados (no viven en la tabla): comparan el "resultado" del
    # Ver crud.calcular_consistencia() para la lógica y las categorías.
    consistente: Optional[bool] = None
    # True si existe otra fila con la misma `acta` (dos expedientes/estados para la misma
    # acta). Ver anotar_duplicadas().
    es_duplicada: Optional[bool] = None

class RegistroUpdate(BaseModel):
    """Para el PATCH cuando alguien cambia un estado desde el combo del frontend."""
    estado_sigemi: Optional[EstadoSigemi] = None
    motivo_archivo_sigemi: Optional[MotivoArchivoSigemi] = None
    estado_semyt: Optional[EstadoSemyt] = None
    estado_sigi: Optional[EstadoSigi] = None
    motivo_archivo_sigi: Optional[MotivoArchivoSigi] = None


class RegistrosPage(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    resultados: List[RegistroOut]


class FiltrosOptions(BaseModel):
    """Valores posibles para llenar los combos de Estado en el frontend."""
    estados_sigemi: List[str]
    motivos_archivo_sigemi: List[str]
    estados_semyt: List[str]
    estados_sigi: List[str]
    motivos_archivo_sigi: List[str]


# ---------------------------------------------------------------------------
# Exportar Actas (reporte .txt con filtros libres por cualquier campo)
# ---------------------------------------------------------------------------

class CampoExportable(BaseModel):
    campo: str
    etiqueta: str
    tipo: str  # "texto" | "estado" | "fecha"
    opciones: Optional[List[str]] = None  # sólo para tipo "estado"


class CamposExportablesResponse(BaseModel):
    campos: List[CampoExportable]


class FiltroExport(BaseModel):
    campo: str
    modo: str = "coincide"  # "coincide" | "no_coincide"
    valor: str


class ExportarRequest(BaseModel):
    filtros: List[FiltroExport] = []
    # Rango de fechas del acta (fecha_hora), independiente de los filtros
    # libres de arriba. Ambos son opcionales y se pueden usar solos o juntos.
    fecha_desde: Optional[date] = None
    fecha_hasta: Optional[date] = None


class ExportarConteo(BaseModel):
    total: int