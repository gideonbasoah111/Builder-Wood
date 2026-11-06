"""Seller payout account registration via Paystack recipients."""
import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_active_seller
from builder_wood.app.config import settings
from builder_wood.app.database import get_db

router = APIRouter(prefix="/seller/payout", tags=["seller-payout"])

PAYSTACK_RECIPIENT_URL = "https://api.paystack.co/transferrecipient"


@router.get("/account", response_model=schemas.PayoutAccountOut)
def get_payout_account(
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    seller = db.query(models.User).filter(
        models.User.user_id == current_seller.user_id
    ).first()
    return {
        "paystack_recipient_code": seller.paystack_recipient_code,
        "payout_bank_name": seller.payout_bank_name,
        "payout_account_number": seller.payout_account_number,
        "payout_account_name": seller.payout_account_name,
        "is_ready": bool(seller.paystack_recipient_code),
    }


@router.post("/account", response_model=schemas.PayoutAccountOut)
def set_payout_account(
    payload: schemas.PayoutAccountIn,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    """Register or update the seller's payout bank account."""
    if not settings.PAYSTACK_SECRET_KEY:
        raise HTTPException(500, "Paystack not configured on server")

    seller = db.query(models.User).filter(
        models.User.user_id == current_seller.user_id
    ).first()

    try:
        resp = requests.post(
            PAYSTACK_RECIPIENT_URL,
            headers={
                "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "type": "ghipss",  # Ghana bank account
                "name": payload.account_name,
                "account_number": payload.account_number,
                "bank_code": payload.bank_name,  # seller passes the bank code
                "currency": "GHS",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise HTTPException(502, f"Paystack unreachable: {exc}")

    data = resp.json()
    if not data.get("status") or not data.get("data", {}).get("recipient_code"):
        raise HTTPException(
            400,
            f"Paystack rejected the account: {data.get('message', 'unknown error')}",
        )

    seller.paystack_recipient_code = data["data"]["recipient_code"]
    seller.payout_bank_name = payload.bank_name
    seller.payout_account_number = payload.account_number
    seller.payout_account_name = payload.account_name
    db.commit()
    db.refresh(seller)

    return {
        "paystack_recipient_code": seller.paystack_recipient_code,
        "payout_bank_name": seller.payout_bank_name,
        "payout_account_number": seller.payout_account_number,
        "payout_account_name": seller.payout_account_name,
        "is_ready": True,
    }