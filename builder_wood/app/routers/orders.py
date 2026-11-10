from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional
import os
import logging
import uuid

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from builder_wood.app.database import get_db
from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_user, get_current_active_seller, get_current_admin
from builder_wood.app.config import settings
from builder_wood.app.distance import DistanceError, estimate_distance_from_address

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["orders"])

MIN_DISTANCE_KM = Decimal("1.0")
COMMISSION_RATE = Decimal("0.05")


# ============================================================
# Request/Response schemas
# ============================================================
class OrderItemIn(BaseModel):
    wood_id: int
    quantity: int = Field(gt=0)


class OrderCreateIn(BaseModel):
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    delivery_address: str
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

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    order_id: int
    status: str
    payment_status: Optional[str] = None
    delivery_status: Optional[str] = None
    settlement_status: Optional[str] = None
    total_pieces: int
    subtotal: Decimal
    delivery_fee: Decimal
    total_price: Decimal
    currency: str
    vehicle_id: Optional[int] = None
    delivery_address: str
    order_date: Optional[datetime] = None
    items: List[OrderItemOut] = []

    class Config:
        from_attributes = True


# ============================================================
# Vehicle allocation
# ============================================================
def _allocate_vehicle(db: Session, total_pieces: int, distance_km: Decimal, preferred_method: str):
    if preferred_method == "pickup":
        return None, Decimal("0.00")

    active = db.query(models.Vehicle).filter(models.Vehicle.is_active.is_(True)).all()
    if not active:
        raise HTTPException(400, "No delivery vehicles configured. Choose pickup.")

    method_to_substr = {
        "aboboyaa": "aboboyaa",
        "pickup-truck": "pickup",
        "truck": "truck",
    }
    preferred_substr = method_to_substr.get(preferred_method)

    def _fee(v):
        return Decimal(str(round(float(v.base_fee) + float(v.rate_per_km) * float(distance_km), 2)))

    if preferred_substr:
        for v in active:
            if preferred_substr.lower() not in v.name.lower():
                continue
            if total_pieces > v.max_pieces:
                raise HTTPException(400, f"{v.name} can carry up to {v.max_pieces} pieces, but this order has {total_pieces}.")
            if distance_km > v.max_distance_km:
                raise HTTPException(400, f"{v.name} can deliver up to {v.max_distance_km} km, but your address is {distance_km} km away.")
            return v, _fee(v)

    candidates = [v for v in active if v.max_pieces >= total_pieces and v.max_distance_km >= distance_km]
    if not candidates:
        raise HTTPException(400, f"No vehicle can deliver {total_pieces} pieces over {distance_km} km.")
    v = min(candidates, key=lambda x: x.max_pieces)
    return v, _fee(v)


# ============================================================
# Release order to WALLET (no Paystack)
# ============================================================
def _initiate_seller_transfer(db: Session, order: models.Order) -> str:
    """
    Release order payout to the seller's WALLET (not Paystack).

    The Paystack transfer happens later, only when the seller
    requests a withdrawal (via admin_wallet.approve_withdrawal).
    """
    from builder_wood.app.wallet_service import (
        release_order_to_wallet,
        auto_create_withdrawal_if_eligible,
    )

    reference = release_order_to_wallet(db, order)
    auto_create_withdrawal_if_eligible(db, order.seller_id)
    return reference


# ============================================================
# Distance quote
# ============================================================
@router.post("/estimate-distance", response_model=schemas.DistanceEstimateOut)
def estimate_distance(payload: schemas.DistanceEstimateIn, db: Session = Depends(get_db)):
    try:
        result = estimate_distance_from_address(payload.address, db)
    except DistanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "distance_km": result.distance_km,
        "geocoded_address": result.geocoded_address,
        "from_cache": result.from_cache,
    }


# ============================================================
# List orders
# ============================================================
@router.get("/seller/orders", response_model=List[OrderOut])
def get_seller_orders(
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    return (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(models.Order.seller_id == current_seller.user_id)
        .order_by(models.Order.order_date.desc())
        .all()
    )


@router.get("/my-orders", response_model=List[OrderOut])
def get_my_orders(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(models.Order.customer_id == current_user.user_id)
        .order_by(models.Order.order_date.desc())
        .all()
    )


# ============================================================
# Create order
# ============================================================
@router.post("/", response_model=List[OrderOut], status_code=201)
def create_order(
    payload: OrderCreateIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if payload.delivery_method == "pickup":
        effective_distance = Decimal("0")
    else:
        try:
            dist = estimate_distance_from_address(payload.delivery_address, db)
            effective_distance = max(dist.distance_km, MIN_DISTANCE_KM)
        except DistanceError as exc:
            raise HTTPException(400, f"Delivery distance failed: {exc}") from exc

    items_by_seller = {}
    for line in payload.items:
        wood = (
            db.query(models.Wood)
            .filter(
                models.Wood.wood_id == line.wood_id,
                models.Wood.is_available.is_(True),
                models.Wood.deleted_at.is_(None),
            )
            .first()
        )
        if not wood:
            raise HTTPException(404, "Wood not found")
        if wood.stock_quantity < line.quantity:
            raise HTTPException(400, f"Only {wood.stock_quantity} of {wood.local_name} in stock")
        items_by_seller.setdefault(wood.seller_id, []).append({"wood": wood, "quantity": line.quantity})

    created_orders = []
    for seller_id, items in items_by_seller.items():
        total_pieces = sum(i["quantity"] for i in items)
        subtotal = sum(i["wood"].price_per_piece * i["quantity"] for i in items)
        vehicle, delivery_fee = _allocate_vehicle(db, total_pieces, effective_distance, payload.delivery_method)
        total_price = subtotal + delivery_fee

        order = models.Order(
            customer_id=current_user.user_id,
            seller_id=seller_id,
            delivery_address=payload.delivery_address,
            distance_km=effective_distance,
            vehicle_id=vehicle.vehicle_id if vehicle else None,
            total_pieces=total_pieces,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            total_price=total_price,
            currency="GHS",
            status="pending",
            notes=payload.notes,
            platform_commission=total_price * COMMISSION_RATE,
            seller_payout_amount=total_price * (1 - COMMISSION_RATE),
            is_funds_released=False,
        )
        db.add(order)
        db.flush()

        for item in items:
            wood = item["wood"]
            wood.stock_quantity -= item["quantity"]
            db.add(models.OrderItem(
                order_id=order.order_id,
                wood_id=wood.wood_id,
                wood_name_snap=wood.local_name,
                size_snap=wood.size_label,
                quantity=item["quantity"],
                price_at_purchase=wood.price_per_piece,
            ))

        db.add(models.OrderStatusHistory(
            order_id=order.order_id, old_status=None,
            new_status="pending", changed_by=None,
        ))
        db.add(models.PaymentTransaction(
            order_id=order.order_id,
            reference=f"BW-{order.order_id}-{uuid.uuid4().hex[:6].upper()}",
            provider="paystack", amount=total_price,
            currency="GHS", status="pending",
        ))
        created_orders.append(order)

    if payload.delivery_address:
        current_user.address = payload.delivery_address
        current_user.location = payload.delivery_address

    db.query(models.CartItem).filter(models.CartItem.user_id == current_user.user_id).delete()
    db.commit()

    return (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(models.Order.order_id.in_([o.order_id for o in created_orders]))
        .all()
    )


# ============================================================
# Update order status
# ============================================================
@router.patch("/{order_id}/status", response_model=OrderOut)
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdateIn,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    order = (
        db.query(models.Order)
        .filter(
            models.Order.order_id == order_id,
            models.Order.seller_id == current_seller.user_id,
        )
        .first()
    )
    if not order:
        raise HTTPException(404, "Order not found")

    old_status = order.status
    order.status = payload.status

    if payload.status == "shipped":
        order.delivery_status = "shipped"
    if payload.status == "delivered":
        order.delivery_status = "delivered"
        order.auto_confirm_at = datetime.utcnow() + timedelta(hours=48)
        order.settlement_status = "eligible"
        order.settlement_eligible_at = order.auto_confirm_at

    db.add(models.OrderStatusHistory(
        order_id=order.order_id, old_status=old_status,
        new_status=payload.status, changed_by=current_seller.user_id,
    ))
    db.commit()
    return (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(models.Order.order_id == order.order_id)
        .first()
    )


# ============================================================
# Admin release funds → credits WALLET (no Paystack)
# ============================================================
@router.post("/{order_id}/admin-release", response_model=schemas.SettlementStatusOut)
def admin_release_funds(
    order_id: int,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(get_current_admin),
):
    order = db.query(models.Order).filter(models.Order.order_id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found.")
    if order.payment_status != "paid":
        raise HTTPException(400, "Order has not been paid.")
    if order.delivery_status != "delivered":
        raise HTTPException(400, "Seller has not marked this order as delivered yet.")

    _initiate_seller_transfer(db, order)
    db.refresh(order)

    return {
        "order_id": order.order_id,
        "payment_status": order.payment_status,
        "delivery_status": order.delivery_status,
        "settlement_status": order.settlement_status,
        "settlement_eligible_at": order.settlement_eligible_at,
        "funds_released_at": order.funds_released_at,
        "transfer_reference": order.transfer_reference,
        "seller_payout_amount": order.seller_payout_amount,
        "platform_commission": order.platform_commission,
    }


# ============================================================
# Buyer confirms delivery → credits WALLET (no Paystack)
# ============================================================
@router.post("/{order_id}/confirm-delivery", response_model=schemas.SettlementStatusOut)
def buyer_confirm_delivery(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    order = (
        db.query(models.Order)
        .filter(
            models.Order.order_id == order_id,
            models.Order.customer_id == current_user.user_id,
        )
        .first()
    )
    if not order:
        raise HTTPException(404, "Order not found.")
    if order.delivery_status != "delivered":
        raise HTTPException(400, "Order is not marked as delivered yet.")
    if order.settlement_status in ("transferring", "settled"):
        raise HTTPException(400, "Settlement already in progress or complete.")

    order.settlement_status = "eligible"
    order.settlement_eligible_at = datetime.utcnow()
    _initiate_seller_transfer(db, order)
    db.refresh(order)

    return {
        "order_id": order.order_id,
        "payment_status": order.payment_status,
        "delivery_status": order.delivery_status,
        "settlement_status": order.settlement_status,
        "settlement_eligible_at": order.settlement_eligible_at,
        "funds_released_at": order.funds_released_at,
        "transfer_reference": order.transfer_reference,
        "seller_payout_amount": order.seller_payout_amount,
        "platform_commission": order.platform_commission,
    }


# ============================================================
# Settlement status
# ============================================================
@router.get("/{order_id}/settlement", response_model=schemas.SettlementStatusOut)
def get_settlement_status(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    order = db.query(models.Order).filter(models.Order.order_id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found.")
    if (order.customer_id != current_user.user_id
            and order.seller_id != current_user.user_id
            and current_user.role != "admin"):
        raise HTTPException(403, "Not authorized")
    return {
        "order_id": order.order_id,
        "payment_status": order.payment_status,
        "delivery_status": order.delivery_status,
        "settlement_status": order.settlement_status,
        "settlement_eligible_at": order.settlement_eligible_at,
        "funds_released_at": order.funds_released_at,
        "transfer_reference": order.transfer_reference,
        "seller_payout_amount": order.seller_payout_amount,
        "platform_commission": order.platform_commission,
    }


# ============================================================
# Disputes
# ============================================================
@router.post("/{order_id}/dispute")
def open_dispute(
    order_id: int,
    payload: DisputeIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    order = (
        db.query(models.Order)
        .filter(
            models.Order.order_id == order_id,
            models.Order.customer_id == current_user.user_id,
        )
        .first()
    )
    if not order:
        raise HTTPException(404, "Order not found")
    if order.status not in ["paid", "confirmed", "shipped", "delivered"]:
        raise HTTPException(400, "Cannot dispute this order")
    if order.is_funds_released:
        raise HTTPException(400, "Funds already released")

    order.status = "disputed"
    order.dispute_reason = payload.reason
    order.disputed_at = datetime.utcnow()
    order.auto_confirm_at = None
    order.frozen_payout = True
    order.frozen_at = datetime.utcnow()

    db.add(models.OrderStatusHistory(
        order_id=order.order_id, old_status="delivered",
        new_status="disputed", changed_by=current_user.user_id,
    ))
    db.commit()
    return {"status": "success", "message": "Dispute opened."}


# ============================================================
# Get single order
# ============================================================
@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    order = (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(models.Order.order_id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(404, "Order not found")
    if (order.customer_id != current_user.user_id
            and order.seller_id != current_user.user_id
            and current_user.role != "admin"):
        raise HTTPException(403, "Not authorized")
    return order