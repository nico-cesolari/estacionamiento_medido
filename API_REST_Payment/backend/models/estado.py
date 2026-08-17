from dataclasses import dataclass
from typing import Optional


@dataclass
class EstadoDescarga:
    ultima_fecha_procesada: Optional[str] = None

    def tiene_fecha_guardada(self) -> bool:
        return bool(self.ultima_fecha_procesada)
