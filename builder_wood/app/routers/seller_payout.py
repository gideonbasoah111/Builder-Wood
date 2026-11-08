"""
Seller payout account registration.

Supports two recipient types:
- Bank (GHIPSS): bank account number + bank code
- Mobile Money: mobile number + telco code (MTN, Telecel, AT)

Both use Paystack /transferrecipient to create a recipient_code that
_initiate_seller_transfer() uses for payouts.
"""
import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session

from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_active_seller
from builder_wood.app.config import settings
from builder_wood.app.database import get_db

router = APIRouter(prefix="/seller/payout", tags=["seller-payout"])

PAYSTACK_RECIPIENT_URL = "https://api.paystack.co/transferrecipient"
PAYSTACK_BANK_URL = "https://api.paystack.co/bank"


class PayoutAccountIn(BaseModel):
    type: str = Field(..., pattern="^(bank|mobile_money)$")
    account_number: str = Field(..., min_length=6, max_length=20)
    account_name: str = Field(..., min_length=3, max_length=150)
    bank_code: Optional[str] = None  # required for bank
    telco_code: Optional[str] = None  # required for mobile_money


@router.get("/account")
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


@router.get("/options")
def get_payout_options():
    """Returns banks and mobile money telcos for the seller form."""
    secret = settings.PAYSTACK_SECRET_KEY
    if not secret:
        raise HTTPException(500, "Paystack not configured")

    headers = {"Authorization": f"Bearer {secret}"}

    try:
        banks_resp = requests.get(
            PAYSTACK_BANK_URL,
            params={"currency": "GHS", "type": "ghipss"},
            headers=headers,
            timeout=15,
        )
        momo_resp = requests.get(
            PAYSTACK_BANK_URL,
            params={"currency": "GHS", "type": "mobile_money"},
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(502, f"Paystack unreachable: {exc}")

    banks_data = banks_resp.json()
    momo_data = momo_resp.json()

    return {
        "banks": [
            {"name": b["name"], "code": b["code"]}
            for b in (banks_data.get("data") or [])
        ],
        "mobile_money": [
            {"name": m["name"], "code": m["code"]}
            for m in (momo_data.get("data") or [])
        ],
    }


@router.post("/account")
def set_payout_account(
    payload: PayoutAccountIn,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    """Register or update the seller's payout account with Paystack."""
    if not settings.PAYSTACK_SECRET_KEY:
        raise HTTPException(500, "Paystack not configured on server")

    seller = db.query(models.User).filter(
        models.User.user_id == current_seller.user_id
    ).first()

    # Validate required fields based on type
    if payload.type == "bank":
        if not payload.bank_code:
            raise HTTPException(400, "bank_code is required for bank account")
        recipient_payload = {
            "type": "ghipss",
            "name": payload.account_name,
            "account_number": payload.account_number,
            "bank_code": payload.bank_code,
            "currency": "GHS",
        }
        display_name = payload.bank_code
    elif payload.type == "mobile_money":
        if not payload.telco_code:
            raise HTTPException(400, "telco_code is required for mobile money")
        recipient_payload = {
            "type": "mobile_money",
            "name": payload.account_name,
            "account_number": payload.account_number,
            "bank_code": payload.telco_code,
            "currency": "GHS",
        }
        display_name = payload.telco_code
    else:
        raise HTTPException(400, "type must be 'bank' or 'mobile_money'")

    try:
        resp = requests.post(
            PAYSTACK_RECIPIENT_URL,
            headers={
                "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            },
            json=recipient_payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise HTTPException(502, f"Paystack unreachable: {exc}")

    data = resp.json()
    if not data.get("status") or not data.get("data", {}).get("recipient_code"):
        msg = data.get("message", "Unknown error")
        raise HTTPException(400, f"Paystack rejected the account: {msg}")

    seller.paystack_recipient_code = data["data"]["recipient_code"]
    seller.payout_bank_name = display_name
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