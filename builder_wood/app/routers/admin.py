from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List
from pathlib import Path

from builder_wood.app.database import get_db
from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_admin
from builder_wood.app.config import settings

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=_TEMPLATES_DIR)


# ---------------------------------------------------------------------------
# Admin dashboard page (HTML)
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def admin_dashboard_page(request: Request):
    return _templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={"shop_name": settings.APP_NAME},
    )


# --- USER MANAGEMENT ---
@router.get("/pending-sellers", response_model=List[schemas.UserOut])
def get_pending_sellers(db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    return db.query(models.User).filter(models.User.role == "seller", models.User.is_active == False).all()

@router.get("/active-sellers", response_model=List[schemas.UserOut])
def get_active_sellers(db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    return db.query(models.User).filter(models.User.role == "seller", models.User.is_active == True).all()

@router.get("/customers", response_model=List[schemas.UserOut])
def get_customers(db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    return db.query(models.User).filter(models.User.role == "customer").all()

@router.patch("/users/{user_id}/toggle-active", response_model=schemas.UserOut)
def toggle_user_active(user_id: int, db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user: raise HTTPException(404, "User not found")
    if user.role == "admin": raise HTTPException(400, "Cannot deactivate admin")
    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)
    return user


# --- PAYOUTS ---
@router.get("/orders/pending-payouts")
def get_pending_payouts(db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    orders = db.query(models.Order).filter(models.Order.status == "delivered", models.Order.is_funds_released == False).all()
    return [{"order_id": o.order_id, "seller_id": o.seller_id, "payout": float(o.seller_payout_amount or 0), "total": float(o.total_price)} for o in orders]

@router.post("/orders/{order_id}/release-funds")
def release_funds(order_id: int, db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    order = db.query(models.Order).filter(models.Order.order_id == order_id).first()
    if not order: raise HTTPException(404, "Order not found")
    if order.status != "delivered": raise HTTPException(400, "Must be delivered")
    if order.is_funds_released: raise HTTPException(400, "Already released")
    order.is_funds_released = True
    db.commit()
    return {"status": "success"}

# --- DISPUTES ---
@router.get("/orders/disputes")
def get_disputed_orders(db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    orders = db.query(models.Order).filter(models.Order.status == "disputed").all()
    return [{"order_id": o.order_id, "buyer_id": o.customer_id, "seller_id": o.seller_id, "reason": o.dispute_reason, "total": float(o.total_price)} for o in orders]

@router.post("/orders/{order_id}/resolve-dispute")
def resolve_dispute(order_id: int, action: str, db: Session = Depends(get_db), admin: models.User = Depends(get_current_admin)):
    order = db.query(models.Order).filter(models.Order.order_id == order_id).first()
    if not order or order.status != "disputed": raise HTTPException(404, "Active dispute not found")

    if action == "refund":
        order.status = "cancelled"
        for item in order.items:
            wood = db.query(models.Wood).filter(models.Wood.wood_id == item.wood_id).first()
            if wood: wood.stock_quantity += item.quantity
    elif action == "release":
        order.status = "delivered"
        order.is_funds_released = True
    else:
        raise HTTPException(400, "Invalid action")

    db.add(models.OrderStatusHistory(order_id=order.order_id, old_status="disputed", new_status=order.status, changed_by=admin.user_id))
    db.commit()
    return {"status": "success", "new_status": order.status}


# ============================================================
# FUNDS LEDGER
# ============================================================

@router.get("/funds/summary", response_model=schemas.FundsSummaryOut)
def funds_summary(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Aggregate numbers: money collected, held, settled, commission."""
    paid_orders = (
        db.query(models.Order)
        .filter(models.Order.payment_status == "paid")
        .all()
    )

    held = [o for o in paid_orders if o.settlement_status != "settled"]
    settled = [o for o in paid_orders if o.settlement_status == "settled"]
    failed = [o for o in paid_orders if o.transfer_failed_reason]

    return {
        "total_collected": float(sum(o.total_price for o in paid_orders)),
        "total_held": float(sum(o.total_price for o in held)),
        "total_settled": float(sum(o.total_price for o in settled)),
        "total_commission_pending": float(sum(o.platform_commission or 0 for o in held)),
        "total_commission_earned": float(sum(o.platform_commission or 0 for o in settled)),
        "pending_payouts_count": len(held),
        "failed_transfers_count": len(failed),
    }


@router.get("/funds/incoming", response_model=schemas.FundsIncomingOut)
def funds_incoming(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """All paid orders where seller hasn't been settled yet."""
    orders = (
        db.query(models.Order)
        .filter(
            models.Order.payment_status == "paid",
            models.Order.settlement_status != "settled",
        )
        .order_by(models.Order.order_date.desc())
        .all()
    )

    result = []
    for o in orders:
        customer = db.query(models.User).filter(models.User.user_id == o.customer_id).first()
        seller = db.query(models.User).filter(models.User.user_id == o.seller_id).first()

        items = [
            {
                "wood_name": i.wood_name_snap,
                "size": i.size_snap,
                "quantity": i.quantity,
                "price": float(i.price_at_purchase),
            }
            for i in o.items
        ]

        result.append({
            "order_id": o.order_id,
            "customer_name": customer.name if customer else "Unknown",
            "seller_name": seller.name if seller else "Unknown",
            "items": items,
            "total_price": float(o.total_price),
            "platform_commission": float(o.platform_commission or 0),
            "seller_payout_amount": float(o.seller_payout_amount or 0),
            "payment_status": o.payment_status or "unpaid",
            "settlement_status": o.settlement_status or "not_eligible",
            "paid_at": o.order_date,
            "settlement_eligible_at": o.settlement_eligible_at,
        })

    return {
        "orders": result,
        "totals": {
            "total_held": sum(r["total_price"] for r in result),
            "total_commission_pending": sum(r["platform_commission"] for r in result),
            "total_owed_to_sellers": sum(r["seller_payout_amount"] for r in result),
        },
    }


@router.get("/funds/settled", response_model=schemas.FundsIncomingOut)
def funds_settled(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """All paid orders where seller has been settled."""
    orders = (
        db.query(models.Order)
        .filter(
            models.Order.payment_status == "paid",
            models.Order.settlement_status == "settled",
        )
        .order_by(models.Order.funds_released_at.desc())
        .all()
    )

    result = []
    for o in orders:
        customer = db.query(models.User).filter(models.User.user_id == o.customer_id).first()
        seller = db.query(models.User).filter(models.User.user_id == o.seller_id).first()

        items = [
            {
                "wood_name": i.wood_name_snap,
                "size": i.size_snap,
                "quantity": i.quantity,
                "price": float(i.price_at_purchase),
            }
            for i in o.items
        ]

        result.append({
            "order_id": o.order_id,
            "customer_name": customer.name if customer else "Unknown",
            "seller_name": seller.name if seller else "Unknown",
            "items": items,
            "total_price": float(o.total_price),
            "platform_commission": float(o.platform_commission or 0),
            "seller_payout_amount": float(o.seller_payout_amount or 0),
            "payment_status": o.payment_status or "unpaid",
            "settlement_status": o.settlement_status or "not_eligible",
            "paid_at": o.order_date,
            "settlement_eligible_at": o.funds_released_at,
        })

    return {
        "orders": result,
        "totals": {
            "total_held": 0,
            "total_commission_pending": 0,
            "total_owed_to_sellers": 0,
        },
    }


@router.get("/funds/by-seller", response_model=schemas.FundsBySellerOut)
def funds_by_seller(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Grouped by seller: how much each is owed vs. paid."""
    sellers = (
        db.query(models.User)
        .filter(models.User.role == "seller")
        .order_by(models.User.name)
        .all()
    )

    result = []
    for s in sellers:
        paid = (
            db.query(models.Order)
            .filter(
                models.Order.seller_id == s.user_id,
                models.Order.payment_status == "paid",
            )
            .all()
        )
        pending = [o for o in paid if o.settlement_status != "settled"]
        settled = [o for o in paid if o.settlement_status == "settled"]

        result.append({
            "seller_id": s.user_id,
            "seller_name": s.name,
            "orders_pending": len(pending),
            "total_owed": float(sum(o.seller_payout_amount or 0 for o in pending)),
            "orders_settled": len(settled),
            "total_settled": float(sum(o.seller_payout_amount or 0 for o in settled)),
        })

    return {"sellers": result}


@router.get("/funds/failed", response_model=schemas.FundsFailedOut)
def funds_failed(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Transfers that failed and need admin attention."""
    orders = (
        db.query(models.Order)
        .filter(
            models.Order.transfer_failed_reason.isnot(None),
            models.Order.settlement_status != "settled",
        )
        .order_by(models.Order.order_date.desc())
        .all()
    )

    result = []
    for o in orders:
        seller = db.query(models.User).filter(models.User.user_id == o.seller_id).first()
        result.append({
            "order_id": o.order_id,
            "seller_name": seller.name if seller else "Unknown",
            "seller_payout_amount": float(o.seller_payout_amount or 0),
            "transfer_reference": o.transfer_reference,
            "transfer_failed_reason": o.transfer_failed_reason,
            "settlement_status": o.settlement_status or "not_eligible",
        })

    return {"failed_transfers": result}