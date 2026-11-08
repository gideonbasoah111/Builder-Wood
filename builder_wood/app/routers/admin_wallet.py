"""Admin wallet + withdrawal management."""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
import uuid
import os
from datetime import datetime

import requests

from builder_wood.app.database import get_db
from builder_wood.app import models
from builder_wood.app.auth import get_current_admin
from builder_wood.app.config import settings
from builder_wood.app.wallet_service import credit_wallet

router = APIRouter(prefix="/admin", tags=["admin-wallet"])

PAYSTACK_TRANSFER_URL = "https://api.paystack.co/transfer"


@router.get("/withdrawals")
def list_withdrawals(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    query = db.query(models.WithdrawalRequest).join(
        models.User, models.WithdrawalRequest.seller_id == models.User.user_id
    )
    if status:
        query = query.filter(models.WithdrawalRequest.status == status)

    withdrawals = query.order_by(models.WithdrawalRequest.requested_at.desc()).all()

    return [
        {
            "withdrawal_id": w.withdrawal_id,
            "seller_id": w.seller_id,
            "seller_name": w.seller.name if w.seller else "Unknown",
            "seller_phone": w.seller.phone_number if w.seller else None,
            "amount": float(w.amount),
            "status": w.status,
            "payout_method": w.payout_method,
            "payout_account": w.payout_account,
            "paystack_transfer_ref": w.paystack_transfer_ref,
            "requested_at": w.requested_at.isoformat() if w.requested_at else None,
            "processed_at": w.processed_at.isoformat() if w.processed_at else None,
            "notes": w.notes,
            "seller_has_recipient": bool(w.seller and w.seller.paystack_recipient_code),
        }
        for w in withdrawals
    ]


@router.post("/withdrawals/{withdrawal_id}/approve")
def approve_withdrawal(
    withdrawal_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Approve a withdrawal and trigger Paystack transfer."""
    w = db.query(models