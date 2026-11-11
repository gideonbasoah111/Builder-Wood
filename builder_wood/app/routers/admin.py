from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, case
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

from builder_wood.app.database import get_db
from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_admin
from builder_wood.app.config import settings

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=_TEMPLATES_DIR)


# ============================================================
# Dashboard page
# ============================================================
@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def admin_dashboard_page(request: Request):
    return _templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={"shop_name": settings.APP_NAME},
    )


# ============================================================
# Shared helpers
# ============================================================
def _get_user_name_map(db: Session, user_ids: set) -> dict:
    """Batch-fetch user names in one query."""
    if not user_ids:
        return {}
    rows = (
        db.query(models.User.user_id, models.User.name)
        .filter(models.User.user_id.in_(user_ids))
        .all()
    )
    return {r.user_id: r.name for r in rows}


def _money(v) -> float:
    """None-safe float for money fields."""
    return float(v) if v is not None else 0.0


def _iso(v) -> Optional[str]:
    return v.isoformat() if v else None


def _serialize_order_item(i: models.OrderItem) -> dict:
    return {
        "wood_name": i.wood_name_snap,
        "size": i.size_snap,
        "quantity": i.quantity,
        "price": _money(i.price_at_purchase),
    }


# ============================================================
# USER MANAGEMENT
# ============================================================
@router.get("/pending-sellers", response_model=List[schemas.UserOut])
def get_pending_sellers(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    return (
        db.query(models.User)
        .filter(models.User.role == "seller", models.User.is_active.is_(False))
        .all()
    )


@router.get("/active-sellers", response_model=List[schemas.UserOut])
def get_active_sellers(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    return (
        db.query(models.User)
        .filter(models.User.role == "seller", models.User.is_active.is_(True))
        .all()
    )


@router.get("/customers", response_model=List[schemas.UserOut])
def get_customers(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    return db.query(models.User).filter(models.User.role == "customer").all()


@router.patch("/users/{user_id}/toggle-active", response_model=schemas.UserOut)
def toggle_user_active(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.role == "admin":
        raise HTTPException(400, "Cannot deactivate admin")
    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)
    return user


# ============================================================
# ORDERS
# ============================================================
@router.get("/orders/all")
def get_all_orders(
    limit: int = 100,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """
    2 queries:
      1. Orders with items eagerly loaded.
      2. User names for every customer/seller in the result set.
    """
    orders = (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .order_by(models.Order.order_date.desc())
        .limit(limit)
        .all()
    )

    user_ids = set()
    for o in orders:
        user_ids.add(o.customer_id)
        user_ids.add(o.seller_id)
    name_map = _get_user_name_map(db, user_ids)

    return [
        {
            "order_id": o.order_id,
            "customer_name": name_map.get(o.customer_id, "Unknown"),
            "seller_name": name_map.get(o.seller_id, "Unknown"),
            "seller_id": o.seller_id,
            "customer_id": o.customer_id,
            "status": o.status,
            "payment_status": o.payment_status,
            "delivery_status": o.delivery_status,
            "settlement_status": o.settlement_status,
            "total_price": _money(o.total_price),
            "seller_payout_amount": _money(o.seller_payout_amount),
            "platform_commission": _money(o.platform_commission),
            "order_date": _iso(o.order_date),
            "auto_confirm_at": _iso(o.auto_confirm_at),
            "funds_released_at": _iso(o.funds_released_at),
            "transfer_reference": o.transfer_reference,
            "transfer_failed_reason": o.transfer_failed_reason,
            "items": [_serialize_order_item(i) for i in o.items],
        }
        for o in orders
    ]


@router.get("/orders/awaiting-release")
def get_orders_awaiting_release(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """
    2 queries:
      1. Eligible orders (paid + delivered, not settled/transferring).
      2. User names + recipient flags for every seller in the result.
    """
    orders = (
        db.query(models.Order)
        .filter(
            models.Order.payment_status == "paid",
            models.Order.delivery_status == "delivered",
            models.Order.settlement_status.notin_(("settled", "transferring")),
        )
        .order_by(models.Order.auto_confirm_at.asc())
        .all()
    )

    user_ids = set()
    seller_ids = set()
    for o in orders:
        user_ids.add(o.customer_id)
        user_ids.add(o.seller_id)
        seller_ids.add(o.seller_id)

    # One query for names + recipient flags together.
    recipient_map: Dict[int, bool] = {}
    if user_ids:
        rows = (
            db.query(
                models.User.user_id,
                models.User.name,
                models.User.paystack_recipient_code,
            )
            .filter(models.User.user_id.in_(user_ids))
            .all()
        )
        name_map = {r.user_id: r.name for r in rows}
        recipient_map = {r.user_id: bool(r.paystack_recipient_code) for r in rows}
    else:
        name_map = {}

    return [
        {
            "order_id": o.order_id,
            "customer_name": name_map.get(o.customer_id, "Unknown"),
            "seller_name": name_map.get(o.seller_id, "Unknown"),
            "seller_payout_amount": _money(o.seller_payout_amount),
            "total_price": _money(o.total_price),
            "auto_confirm_at": _iso(o.auto_confirm_at),
            "seller_has_recipient": recipient_map.get(o.seller_id, False),
        }
        for o in orders
    ]


@router.post("/orders/{order_id}/release-funds-v2")
def admin_release_funds_v2(
    order_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    from builder_wood.app.routers.orders import _initiate_seller_transfer

    order = db.query(models.Order).filter(models.Order.order_id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found.")
    if order.payment_status != "paid":
        raise HTTPException(400, "Order has not been paid.")
    if order.delivery_status != "delivered":
        raise HTTPException(400, "Order must be marked delivered by the seller first.")

    reference = _initiate_seller_transfer(db, order)
    return {
        "status": "transfer_initiated",
        "order_id": order.order_id,
        "reference": reference,
        "message": "Funds will be released once Paystack confirms the transfer.",
    }


@router.post("/orders/{order_id}/alert-seller")
def alert_seller(
    order_id: int,
    reason: str = "delivery_delayed",
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    order = db.query(models.Order).filter(models.Order.order_id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    return {"status": "not_implemented", "message": "Notifications coming soon"}


# ============================================================
# FUNDS SUMMARY — one aggregate query
# ============================================================
@router.get("/funds/summary", response_model=schemas.FundsSummaryOut)
def funds_summary(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """
    1 aggregate query. All CASE-based SUMs are pushed into SQL.
    """
    row = (
        db.query(
            func.sum(models.Order.total_price).label("total_collected"),
            func.sum(case(
                (models.Order.settlement_status == "settled", models.Order.total_price),
                else_=0,
            )).label("total_settled"),
            func.sum(case(
                (models.Order.settlement_status != "settled", models.Order.total_price),
                else_=0,
            )).label("total_held"),
            func.sum(case(
                (models.Order.settlement_status == "settled", models.Order.platform_commission),
                else_=0,
            )).label("commission_earned"),
            func.sum(case(
                (models.Order.settlement_status != "settled", models.Order.platform_commission),
                else_=0,
            )).label("commission_pending"),
            func.sum(case(
                (models.Order.settlement_status != "settled", 1),
                else_=0,
            )).label("pending_count"),
            func.sum(case(
                (models.Order.transfer_failed_reason.isnot(None), 1),
                else_=0,
            )).label("failed_count"),
        )
        .filter(models.Order.payment_status == "paid")
        .first()
    )

    return {
        "total_collected": _money(row.total_collected),
        "total_held": _money(row.total_held),
        "total_settled": _money(row.total_settled),
        "total_commission_pending": _money(row.commission_pending),
        "total_commission_earned": _money(row.commission_earned),
        "pending_payouts_count": int(row.pending_count or 0),
        "failed_transfers_count": int(row.failed_count or 0),
    }


@router.get("/funds/incoming", response_model=schemas.FundsIncomingOut)
def funds_incoming(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """
    2 queries: orders + items (joinedload), then user names.
    Totals are computed in Python from the same rows.
    """
    orders = (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(
            models.Order.payment_status == "paid",
            models.Order.settlement_status != "settled",
        )
        .order_by(models.Order.order_date.desc())
        .all()
    )

    user_ids = set()
    for o in orders:
        user_ids.add(o.customer_id)
        user_ids.add(o.seller_id)
    name_map = _get_user_name_map(db, user_ids)

    result = []
    total_held = 0.0
    total_commission_pending = 0.0
    total_owed_to_sellers = 0.0

    for o in orders:
        total_price = _money(o.total_price)
        commission = _money(o.platform_commission)
        payout = _money(o.seller_payout_amount)

        total_held += total_price
        total_commission_pending += commission
        total_owed_to_sellers += payout

        result.append({
            "order_id": o.order_id,
            "customer_name": name_map.get(o.customer_id, "Unknown"),
            "seller_name": name_map.get(o.seller_id, "Unknown"),
            "items": [_serialize_order_item(i) for i in o.items],
            "total_price": total_price,
            "platform_commission": commission,
            "seller_payout_amount": payout,
            "payment_status": o.payment_status or "unpaid",
            "settlement_status": o.settlement_status or "not_eligible",
            "paid_at": o.order_date,
            "settlement_eligible_at": o.settlement_eligible_at,
        })

    return {
        "orders": result,
        "totals": {
            "total_held": total_held,
            "total_commission_pending": total_commission_pending,
            "total_owed_to_sellers": total_owed_to_sellers,
        },
    }


@router.get("/funds/by-seller", response_model=schemas.FundsBySellerOut)
def funds_by_seller(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """
    2 queries:
      1. All sellers.
      2. All paid orders for those sellers (grouped in Python).
    """
    sellers = (
        db.query(models.User)
        .filter(models.User.role == "seller")
        .order_by(models.User.name)
        .all()
    )
    if not sellers:
        return {"sellers": []}

    seller_ids = [s.user_id for s in sellers]

    all_orders = (
        db.query(models.Order)
        .filter(
            models.Order.seller_id.in_(seller_ids),
            models.Order.payment_status == "paid",
        )
        .all()
    )

    by_seller: Dict[int, List[models.Order]] = defaultdict(list)
    for o in all_orders:
        by_seller[o.seller_id].append(o)

    result = []
    for s in sellers:
        orders = by_seller.get(s.user_id, [])

        ready = [
            o for o in orders
            if o.delivery_status == "delivered"
            and o.settlement_status not in ("settled", "transferring")
        ]
        in_progress = [
            o for o in orders
            if o.delivery_status != "delivered"
            and o.settlement_status != "settled"
        ]
        settled = [o for o in orders if o.settlement_status == "settled"]

        result.append({
            "seller_id": s.user_id,
            "seller_name": s.name,
            "seller_has_recipient": bool(s.paystack_recipient_code),
            "orders_pending": len(ready),
            "total_owed": float(sum(_money(o.seller_payout_amount) for o in ready)),
            "orders_in_progress": len(in_progress),
            "total_in_progress": float(sum(_money(o.seller_payout_amount) for o in in_progress)),
            "orders_settled": len(settled),
            "total_settled": float(sum(_money(o.seller_payout_amount) for o in settled)),
            "ready_orders": [
                {
                    "order_id": o.order_id,
                    "total_price": _money(o.total_price),
                    "seller_payout_amount": _money(o.seller_payout_amount),
                    "delivery_address": o.delivery_address,
                    "auto_confirm_at": _iso(o.auto_confirm_at),
                    "order_date": _iso(o.order_date),
                }
                for o in sorted(ready, key=lambda x: x.order_date or datetime.min)
            ],
        })

    return {"sellers": result}


@router.get("/funds/settled", response_model=schemas.FundsIncomingOut)
def funds_settled(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """2 queries: settled orders + names."""
    orders = (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(
            models.Order.payment_status == "paid",
            models.Order.settlement_status == "settled",
        )
        .order_by(models.Order.funds_released_at.desc())
        .limit(200)
        .all()
    )

    user_ids = set()
    for o in orders:
        user_ids.add(o.customer_id)
        user_ids.add(o.seller_id)
    name_map = _get_user_name_map(db, user_ids)

    return {
        "orders": [
            {
                "order_id": o.order_id,
                "customer_name": name_map.get(o.customer_id, "Unknown"),
                "seller_name": name_map.get(o.seller_id, "Unknown"),
                "items": [_serialize_order_item(i) for i in o.items],
                "total_price": _money(o.total_price),
                "platform_commission": _money(o.platform_commission),
                "seller_payout_amount": _money(o.seller_payout_amount),
                "payment_status": o.payment_status or "unpaid",
                "settlement_status": o.settlement_status or "not_eligible",
                "paid_at": o.order_date,
                "settlement_eligible_at": o.funds_released_at,
            }
            for o in orders
        ],
        "totals": {"total_held": 0, "total_commission_pending": 0, "total_owed_to_sellers": 0},
    }


@router.get("/funds/failed", response_model=schemas.FundsFailedOut)
def funds_failed(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """2 queries: failed orders + seller names."""
    orders = (
        db.query(models.Order)
        .filter(
            models.Order.transfer_failed_reason.isnot(None),
            models.Order.settlement_status != "settled",
        )
        .order_by(models.Order.order_date.desc())
        .limit(100)
        .all()
    )

    seller_ids = {o.seller_id for o in orders}
    name_map = _get_user_name_map(db, seller_ids)

    return {
        "failed_transfers": [
            {
                "order_id": o.order_id,
                "seller_name": name_map.get(o.seller_id, "Unknown"),
                "seller_payout_amount": _money(o.seller_payout_amount),
                "transfer_reference": o.transfer_reference,
                "transfer_failed_reason": o.transfer_failed_reason,
                "settlement_status": o.settlement_status or "not_eligible",
            }
            for o in orders
        ]
    }


# ============================================================
# DISPUTES
# ============================================================
@router.get("/orders/disputes")
def get_disputed_orders(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    orders = (
        db.query(models.Order)
        .filter(models.Order.status == "disputed")
        .limit(100)
        .all()
    )
    return [
        {
            "order_id": o.order_id,
            "buyer_id": o.customer_id,
            "seller_id": o.seller_id,
            "reason": o.dispute_reason,
            "total": _money(o.total_price),
        }
        for o in orders
    ]


@router.post("/orders/{order_id}/resolve-dispute")
def resolve_dispute(
    order_id: int,
    action: str,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """
    Previously: 1 query per OrderItem to restore stock (N+1).
    Now: 1 query to fetch all affected Woods with FOR UPDATE, then batch-update.
    """
    order = (
        db.query(models.Order)
        .options(joinedload(models.Order.items))
        .filter(models.Order.order_id == order_id)
        .first()
    )
    if not order or order.status != "disputed":
        raise HTTPException(404, "Active dispute not found")

    if action not in ("refund", "release"):
        raise HTTPException(400, "Invalid action")

    if action == "refund":
        # Fetch all affected woods in one query, locked for update.
        wood_ids = [item.wood_id for item in order.items]
        if wood_ids:
            woods = (
                db.query(models.Wood)
                .filter(models.Wood.wood_id.in_(wood_ids))
                .with_for_update()
                .all()
            )
            woods_by_id = {w.wood_id: w for w in woods}
            for item in order.items:
                wood = woods_by_id.get(item.wood_id)
                if wood:
                    wood.stock_quantity += item.quantity
        order.status = "cancelled"

    elif action == "release":
        order.status = "delivered"
        order.is_funds_released = True

    db.add(models.OrderStatusHistory(
        order_id=order.order_id,
        old_status="disputed",
        new_status=order.status,
        changed_by=admin.user_id,
    ))

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {"status": "success", "new_status": order.status}