from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from builder_wood.app.database import get_db
from builder_wood.app import models, schemas

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.get("/", response_model=List[schemas.VehicleOut])
def list_vehicles(db: Session = Depends(get_db)):
    return db.query(models.Vehicle).all()


@router.get("/{vehicle_id}", response_model=schemas.VehicleOut)
def get_vehicle(vehicle_id: int, db: Session = Depends(get_db)):
    vehicle = db.query(models.Vehicle).filter(models.Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


@router.post("/", response_model=schemas.VehicleOut, status_code=201)
def create_vehicle(payload: schemas.VehicleCreate, db: Session = Depends(get_db)):
    vehicle = models.Vehicle(**payload.model_dump())
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle