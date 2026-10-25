from typing import Optional

from sqlalchemy.orm import Session

from builder_wood.app import models

from .. import models
from ..models import Vehicle

def choose_vehicle(db: Session, quantity: int, timber_length_m: float, distance_km: float):
    # Prefer the smallest vehicle that safely satisfies all configured constraints.
    vehicles = db.query(Vehicle).filter(Vehicle.active.is_(True)).all()
    eligible = [
        v for v in vehicles
        if quantity <= v.max_pieces
        and timber_length_m <= v.max_length_m
        and distance_km <= v.max_distance_km
    ]
    # Aboboyaa's 5 km cap is enforced even if its database setting is accidentally higher.
    eligible = [
        v for v in eligible
        if not (v.name.strip().lower() == "aboboyaa" and distance_km > 5)
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda v: (v.max_pieces, v.base_fee + v.rate_per_km * distance_km))
    return eligible[0]

def calculate_delivery_fee(vehicle: Vehicle, distance_km: float) -> float:
    return round(vehicle.base_fee + vehicle.rate_per_km * distance_km, 2)


def allocate_vehicle(
    db: Session, total_pieces: int, distance_km: float
) -> tuple[Optional[models.Vehicle], float]:
    """
    Pick the smallest vehicle that can carry the load over the distance,
    and compute the delivery fee = base_fee + rate_per_km * distance.
    Returns (vehicle, fee). If no vehicle fits, returns (None, 0.0).
    """
    candidates = (
        db.query(models.Vehicle)
        .filter(models.Vehicle.max_pieces >= total_pieces)
        .filter(models.Vehicle.max_distance_km >= distance_km)
        .order_by(models.Vehicle.max_pieces.asc())
        .all()
    )

    if not candidates:
        return None, 0.0

    vehicle = candidates[0]
    fee = vehicle.base_fee + vehicle.rate_per_km * distance_km
    return vehicle, round(fee, 2)