from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from builder_wood.app.database import get_db
from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_user, get_current_active_seller, get_current_admin
from builder_wood.app.distance import DistanceError, estimate_distance_from_address

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["orders"])

MIN_DISTANCE_KM = Decimal("1.0")
COMMISSION_RATE = Decimal("0.05")
MONEY_PLACES = Decimal("0.01")


# ============================================================
# Request/Response schemas (unchanged)
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
        fee = Decimal(str(v.base_fee)) + Decimal(str(v.rate_per_km)) * Decimal(str(distance_km))
        return fee.quantize(MONEY_PLACES)

    if preferred_substr:
        for v in active:
            if preferred_substr.lower() not in v.name.lower():
                continue
            if total_pieces > v.max_pieces:
                raise HTTPException(400, f"{v.name} can carry up to {v.max_pieces} pieces, but this order has {total_pieces}.")
            if Decimal(str(distance_km)) > Decimal(str(v.max_distance_km)):
                raise HTTPException(400, f"{v.name} can deliver up to {v.max_distance_km} km, but your address is {distance_km} km away.")
            return v, _fee(v)

    candidates = [
        v for v in active
        if v.max_pieces >= total_pieces and Decimal(str(v.max_distance_km)) >= Decimal(str(distance_km))
    ]
    if not candidates:
        raise HTTPException(400, f"No vehicle can deliver {total_pieces} pieces over {distance_km} km.")
    v = min(candidates, key=lambda x: x.max_pieces)
    return v, _fee(v)


# ============================================================
# Release order to WALLET (unchanged)
# ============================================================
def _initiate_seller_transfer(db: Session, order: models.Order) -> str:
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
# Shared query helper
# ============================================================
def _order_query(db: Session):
    # joinedload(items) → one LEFT JOIN, one result set.
    # MySQL handles this efficiently with the FK index on order_items.order_id.
    return db.query(models.Order).options(joinedload(models.Order.items))


# ============================================================
# List orders
# ============================================================
@router.get("/seller/orders", response_model=List[OrderOut])
def get_seller_orders(
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    return (
        _order_query(db)
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
        _order_query(db)
        .filter(models.Order.customer_id == current_user.user_id)
        .order_by(models.Order.order_date.desc())
        .all()
    )


# ============================================================
# Create order — MySQL-safe batching and row locks
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
            effective_distance = max(Decimal(str(dist.distance_km)), MIN_DISTANCE_KM)
        except DistanceError as exc:
            raise HTTPException(400, f"Delivery distance failed: {exc}") from exc

    # ------------------------------------------------------------------
    # 1. Aggregate quantities per wood_id (a cart may repeat a product).
    # ------------------------------------------------------------------
    requested_qty: Dict[int, int] = {}
    for line in payload.items:
        requested_qty[line.wood_id] = requested_qty.get(line.wood_id, 0) + line.quantity

    if not requested_qty:
        raise HTTPException(400, "Cart is empty.")

    requested_ids = list(requested_qty.keys())

    # ------------------------------------------------------------------
    # 2. One SELECT with FOR UPDATE.
    #
    # MySQL (InnoDB):
    #   SELECT ... WHERE wood_id IN (...) FOR UPDATE
    # locks every matching row in the same transaction. That prevents two
    # concurrent checkouts from both reading stock=1 and both decrementing.
    #
    # InnoDB only applies FOR UPDATE on the rows that satisfy the WHERE,
    # so we filter on wood_id first. is_available / deleted_at are checked
    # afterwards in Python to keep the lock set as small as possible.
    # ------------------------------------------------------------------
    woods = (
        db.query(models.Wood)
        .filter(models.Wood.wood_id.in_(requested_ids))
        .with_for_update()
        .all()
    )
    woods_by_id: Dict[int, models.Wood] = {w.wood_id: w for w in woods}

    # ------------------------------------------------------------------
    # 3. Validate every line before mutating anything.
    # ------------------------------------------------------------------
    items_by_seller: Dict[int, List[dict]] = {}
    for wood_id, qty in requested_qty.items():
        wood = woods_by_id.get(wood_id)
        if not wood or wood.deleted_at is not None or not wood.is_available:
            raise HTTPException(404, f"Wood {wood_id} not found or unavailable")
        if wood.stock_quantity < qty:
            raise HTTPException(
                400,
                f"Only {wood.stock_quantity} of {wood.local_name} in stock",
            )
        items_by_seller.setdefault(wood.seller_id, []).append(
            {"wood": wood, "quantity": qty}
        )

    created_order_ids: List[int] = []

    for seller_id, items in items_by_seller.items():
        total_pieces = sum(i["quantity"] for i in items)
        subtotal = sum(
            (Decimal(str(i["wood"].price_per_piece)) * i["quantity"]) for i in items
        ).quantize(MONEY_PLACES)

        vehicle, delivery_fee = _allocate_vehicle(
            db, total_pieces, effective_distance, payload.delivery_method
        )
        total_price = (subtotal + delivery_fee).quantize(MONEY_PLACES)

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
            platform_commission=(total_price * COMMISSION_RATE).quantize(MONEY_PLACES),
            seller_payout_amount=(total_price * (1 - COMMISSION_RATE)).quantize(MONEY_PLACES),
            is_funds_released=False,
        )
        db.add(order)
        db.flush()  # assign order_id

        for item in items:
            wood = item["wood"]
            wood.stock_quantity -= item["quantity"]
            db.add(models.OrderItem(
                order_id=order.order_id,
                wood_id=wood.wood_id,
                wood_name_snap=wood.local_name,
                size_snap=getattr(wood, "size_label", None) or "",
                quantity=item["quantity"],
                price_at_purchase=wood.price_per_piece,
            ))

        db.add(models.OrderStatusHistory(
            order_id=order.order_id,
            old_status=None,
            new_status="pending",
            changed_by=None,
        ))
        db.add(models.PaymentTransaction(
            order_id=order.order_id,
            reference=f"BW-{order.order_id}-{uuid.uuid4().hex[:6].upper()}",
            provider="paystack",
            amount=total_price,
            currency="GHS",
            status="pending",
        ))
        created_order_ids.append(order.order_id)

    if payload.delivery_address:
        current_user.address = payload.delivery_address
        current_user.location = payload.delivery_address

    # ------------------------------------------------------------------
    # 4. Bulk-delete the cart.
    #
    # MySQL + SQLAlchemy: the default synchronize_session=True makes
    # SQLAlchemy SELECT every cart row so it can update in-memory objects.
    # That's wasted work — the caller doesn't hold cart rows, and we're
    # about to return orders. synchronize_session=False skips it.
    # ------------------------------------------------------------------
    db.query(models.CartItem).filter(
        models.CartItem.user_id == current_user.user_id
    ).delete(synchronize_session=False)

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("create_order commit failed for user %s", current_user.user_id)
        raise

    return (
        _order_query(db)
        .filter(models.Order.order_id.in_(created_order_ids))
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
        now = datetime.now(timezone.utc)
        order.delivery_status = "delivered"
        order.auto_confirm_at = now + timedelta(hours=48)
        order.settlement_status = "eligible"
        order.settlement_eligible_at = order.auto_confirm_at

    db.add(models.OrderStatusHistory(
        order_id=order.order_id,
        old_status=old_status,
        new_status=payload.status,
        changed_by=current_seller.user_id,
    ))
    db.commit()

    return (
        _order_query(db)
        .filter(models.Order.order_id == order.order_id)
        .first()
    )


# ============================================================
# Admin release funds
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
# Buyer confirms delivery
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
    order.settlement_eligible_at = datetime.now(timezone.utc)
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

    now = datetime.now(timezone.utc)
    order.status = "disputed"
    order.dispute_reason = payload.reason
    order.disputed_at = now
    order.auto_confirm_at = None
    order.frozen_payout = True
    order.frozen_at = now

    db.add(models.OrderStatusHistory(
        order_id=order.order_id,
        old_status="delivered",
        new_status="disputed",
        changed_by=current_user.user_id,
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
        _order_query(db)
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