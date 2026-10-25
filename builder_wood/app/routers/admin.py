from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from builder_wood.app.database import get_db
from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_admin

router = APIRouter(prefix="/admin", tags=["admin"])

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
            if wood: wood.stock_quantity += item.quantity # Return stock
    elif action == "release":
        order.status = "delivered"
        order.is_funds_released = True
    else:
        raise HTTPException(400, "Invalid action")

    db.add(models.OrderStatusHistory(order_id=order.order_id, old_status="disputed", new_status=order.status, changed_by=admin.user_id))
    db.commit()
    return {"status": "success", "new_status": order.status}