import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from builder_wood.app.config import settings
from builder_wood.app.database import get_db
from builder_wood.app import models
from builder_wood.app.auth import get_current_user

router = APIRouter(prefix="/payments", tags=["payments"])

PAYSTACK_URL = "https://api.paystack.co/transaction/initialize"
PAYSTACK_VERIFY_URL = "https://api.paystack.co/transaction/verify/"

class PaymentInitIn(BaseModel):
    order_id: int

@router.post("/initialize")
def initialize_payment(
    payload: PaymentInitIn, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    order = db.query(models.Order).filter(models.Order.order_id == payload.order_id).first()
    if not order:
        raise HTTPException(404, "Order not found")
    
    if order.customer_id != current_user.user_id:
        raise HTTPException(403, "Not authorized to pay for this order")

    transaction = db.query(models.PaymentTransaction).filter(
        models.PaymentTransaction.order_id == order.order_id
    ).first()
    
    if not transaction:
        raise HTTPException(404, "Payment transaction record not found")
        
    if transaction.status == "success":
        raise HTTPException(400, "This order has already been paid")

    amount_in_pesewas = int(float(order.total_price) * 100)
    
    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "email": current_user.email or "buyer@builderwood.com",
        "amount": amount_in_pesewas,
        "currency": "GHS",
        "reference": transaction.reference,
        "callback_url": f"{settings.BASE_URL}/payments/callback"
    }
    
    response = requests.post(PAYSTACK_URL, json=data, headers=headers)
    res_data = response.json()
    
    if not res_data.get("status"):
        raise HTTPException(400, f"Paystack error: {res_data.get('message')}")
        
    return {
        "status": "success",
        "authorization_url": res_data["data"]["authorization_url"]
    }

@router.get("/callback")
def payment_callback(reference: str, db: Session = Depends(get_db)):
    transaction = db.query(models.PaymentTransaction).filter(
        models.PaymentTransaction.reference == reference
    ).first()
    
    if not transaction:
        return RedirectResponse(url="/my-orders?error=TransactionNotFound")

    headers = {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}
    response = requests.get(f"{PAYSTACK_VERIFY_URL}{reference}", headers=headers)
    res_data = response.json()
    
    if res_data.get("status") and res_data["data"]["status"] == "success":
        transaction.status = "success"
        transaction.gateway_response = str(res_data)
        
        order = db.query(models.Order).filter(models.Order.order_id == transaction.order_id).first()
        if order:
            order.status = "confirmed"
            
        db.commit()
        return RedirectResponse(url=f"/order/{transaction.order_id}?payment=success")
    else:
        transaction.status = "failed"
        transaction.gateway_response = str(res_data)
        db.commit()
        return RedirectResponse(url=f"/order/{transaction.order_id}?payment=failed")