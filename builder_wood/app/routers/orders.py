from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from builder_wood.app.database import get_db
from builder_wood.app import models
from builder_wood.app.auth import get_current_user, get_current_active_seller

router = APIRouter(prefix="/orders", tags=["orders"])


class OrderItemIn(BaseModel):
    wood_id: int
    quantity: int = Field(gt=0)


class OrderCreateIn(BaseModel):
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    delivery_address: str
    distance_km: Decimal = Field(ge=0)
    delivery_method: str = "aboboyaa"
    notes: Optional[str] = None
    items: List[OrderItemIn] = Field(min_length=1)


class OrderStatusUpdateIn(BaseModel):
    status: str


class DisputeIn(BaseModel):
    reason: str


class OrderItemOut(BaseModel):
    order_item_id: int
    wood_id: int
    wood_name_snap: str
    size_snap: str
    quantity: int
    price_at_purchase: Decimal

    class Config: from_attributes = True


class OrderOut(BaseModel):
    order_id: int
    status: str
    total_pieces: int
    subtotal: Decimal
    delivery_fee: Decimal
    total_price: Decimal
    currency: str
    vehicle_id: Optional[int]
    delivery_address: str
    order_date: datetime | None = None
    items: List[OrderItemOut] = []

    class Config: from_attributes = True


def _allocate_vehicle(db: Session, total_pieces: int, distance_km: Decimal, preferred_method: str):
    method_to_name = {"pickup": None, "aboboyaa": "Aboboyaa", "pickup-truck": "Small pickup", "truck": "Large truck"}
    if preferred_method == "pickup": return None, Decimal("0.00")
    preferred_name = method_to_name.get(preferred_method)
    if preferred_name:
        v = (db.query(models.Vehicle).filter(models.Vehicle.name == preferred_name).filter(
            models.Vehicle.is_active == True).first())
        if v and total_pieces <= v.max_pieces and distance_km <= v.max_distance_km:
            return v, Decimal(str(round(float(v.base_fee + v.rate_per_km * distance_km), 2)))
    candidates = (db.query(models.Vehicle).filter(models.Vehicle.is_active == True).filter(
        models.Vehicle.max_pieces >= total_pieces).filter(models.Vehicle.max_distance_km >= distance_km).order_by(
        models.Vehicle.max_pieces.asc()).all())
    if not candidates: raise HTTPException(400, "No vehicle can deliver this order")
    v = candidates[0]
    return v, Decimal(str(round(float(v.base_fee + v.rate_per_km * distance_km), 2)))


@router.get("/seller/orders", response_model=List[OrderOut])
def get_seller_orders(db: Session = Depends(get_db), current_seller: models.User = Depends(get_current_active_seller)):
    return db.query(models.Order).options(joinedload(models.Order.items)).filter(
        models.Order.seller_id == current_seller.user_id).order_by(models.Order.order_date.desc()).all()


@router.get("/my-orders", response_model=List[OrderOut])
def get_my_orders(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return db.query(models.Order).options(joinedload(models.Order.items)).filter(
        models.Order.customer_id == current_user.user_id).order_by(models.Order.order_date.desc()).all()


@router.post("/", response_model=List[OrderOut], status_code=201)
def create_order(payload: OrderCreateIn, db: Session = Depends(get_db),
                 current_user: models.User = Depends(get_current_user)):
    items_by_seller = {}
    for line in payload.items:
        wood = db.query(models.Wood).filter(models.Wood.wood_id == line.wood_id, models.Wood.is_available == True,
                                            models.Wood.deleted_at.is_(None)).first()
        if not wood: raise HTTPException(404, "Wood not found")
        if wood.stock_quantity < line.quantity: raise HTTPException(400, f"Only {wood.stock_quantity} in stock")
        if wood.seller_id not in items_by_seller: items_by_seller[wood.seller_id] = []
        items_by_seller[wood.seller_id].append({"wood": wood, "quantity": line.quantity})

    created_orders = []
    for seller_id, items in items_by_seller.items():
        total_pieces = sum(i["quantity"] for i in items)
        subtotal = sum(i["wood"].price_per_piece * i["quantity"] for i in items)
        vehicle, delivery_fee = _allocate_vehicle(db, total_pieces, payload.distance_km, payload.delivery_method)
        total_price = subtotal + delivery_fee

        order = models.Order(
            customer_id=current_user.user_id, seller_id=seller_id, delivery_address=payload.delivery_address,
            distance_km=payload.distance_km, vehicle_id=vehicle.vehicle_id if vehicle else None,
            total_pieces=total_pieces, subtotal=subtotal, delivery_fee=delivery_fee, total_price=total_price,
            currency="GHS", status="pending", notes=payload.notes,
            platform_commission=total_price * Decimal("0.05"),
            seller_payout_amount=total_price * Decimal("0.95"),
            is_funds_released=False
        )
        db.add(order)
        db.flush()

        for item in items:
            wood = item["wood"]
            wood.stock_quantity -= item["quantity"]  # Lock stock
            db.add(models.OrderItem(order_id=order.order_id, wood_id=wood.wood_id, wood_name_snap=wood.local_name,
                                    size_snap=wood.size_label, quantity=item["quantity"],
                                    price_at_purchase=wood.price_per_piece))

        db.add(
            models.OrderStatusHistory(order_id=order.order_id, old_status=None, new_status="pending", changed_by=None))
        db.add(models.PaymentTransaction(order_id=order.order_id,
                                         reference=f"BW-{order.order_id}-{uuid.uuid4().hex[:6].upper()}",
                                         provider="paystack", amount=total_price, currency="GHS", status="pending"))
        created_orders.append(order)

    if payload.delivery_address:
        current_user.address = payload.delivery_address
        current_user.location = payload.delivery_address

    db.query(models.CartItem).filter(models.CartItem.user_id == current_user.user_id).delete()
    db.commit()
    return db.query(models.Order).options(joinedload(models.Order.items)).filter(
        models.Order.order_id.in_([o.order_id for o in created_orders])).all()


@router.patch("/{order_id}/status", response_model=OrderOut)
def update_order_status(order_id: int, payload: OrderStatusUpdateIn, db: Session = Depends(get_db),
                        current_seller: models.User = Depends(get_current_active_seller)):
    order = db.query(models.Order).filter(models.Order.order_id == order_id,
                                          models.Order.seller_id == current_seller.user_id).first()
    if not order: raise HTTPException(404, "Order not found")

    old_status = order.status
    order.status = payload.status

    if payload.status == "delivered":
        order.auto_confirm_at = datetime.utcnow() + timedelta(hours=48)  # 48 Hour Timer

    db.add(models.OrderStatusHistory(order_id=order.order_id, old_status=old_status, new_status=payload.status,
                                     changed_by=current_seller.user_id))
    db.commit()
    return db.query(models.Order).options(joinedload(models.Order.items)).filter(
        models.Order.order_id == order.order_id).first()


@router.post("/{order_id}/dispute")
def open_dispute(order_id: int, payload: DisputeIn, db: Session = Depends(get_db),
                 current_user: models.User = Depends(get_current_user)):
    order = db.query(models.Order).filter(models.Order.order_id == order_id,
                                          models.Order.customer_id == current_user.user_id).first()
    if not order: raise HTTPException(404, "Order not found")
    if order.status not in ["paid", "confirmed", "shipped", "delivered"]: raise HTTPException(400,
                                                                                              "Cannot dispute this order")
    if order.is_funds_released: raise HTTPException(400, "Funds already released")

    order.status = "disputed"
    order.dispute_reason = payload.reason
    order.disputed_at = datetime.utcnow()
    order.auto_confirm_at = None  # Stop timer

    db.add(models.OrderStatusHistory(order_id=order.order_id, old_status="delivered", new_status="disputed",
                                     changed_by=current_user.user_id))
    db.commit()
    return {"status": "success", "message": "Dispute opened."}


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    order = db.query(models.Order).options(joinedload(models.Order.items)).filter(
        models.Order.order_id == order_id).first()
    if not order: raise HTTPException(404, "Order not found")
    if order.customer_id != current_user.user_id and order.seller_id != current_user.user_id and current_user.role != "admin": raise HTTPException(
        403, "Not authorized")
    return order