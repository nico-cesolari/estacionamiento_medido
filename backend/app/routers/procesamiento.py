from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..pasos.procesar_actas_semyt import procesar_actas_semyt

router = APIRouter(prefix="/api/procesamiento", tags=["procesamiento"])

@router.post("/semyt")
async def disparar_procesamiento_semyt(db: Session = Depends(get_db)):
    return await procesar_actas_semyt(db)