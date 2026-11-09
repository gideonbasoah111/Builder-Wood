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
    w = db.query(models.WithdrawalRequest).filter(
        models.WithdrawalRequest.withdrawal_id == withdrawal_id
    ).with_for_update().first()

    if not w:
        raise HTTPException(404, "Withdrawal not found")
    if w.status != "pending":
        raise HTTPException(400, f"Cannot approve withdrawal in status: {w.status}")

    seller = db.query(models.User).filter(models.User.user_id == w.seller_id).first()
    if not seller or not seller.paystack_recipient_code:
        raise HTTPException(400, "Seller has no payout account")

    reference = f"BW-WD-{w.withdrawal_id}-{uuid.uuid4().hex[:8]}".lower()
    amount_pesewas = int((Decimal(str(w.amount)) * 100).quantize(Decimal("1")))

    # ---------- DEMO MODE: skip Paystack ----------
    if os.getenv("DEMO_MODE", "false").lower() == "true":
        w.status = "completed"
        w.paystack_transfer_ref = f"DEMO-{reference}"
        w.processed_at = datetime.utcnow()
        w.notes = (w.notes or "") + "\n[DEMO MODE] Simulated transfer"
        db.commit()
        return {
            "status": "completed",
            "withdrawal_id": w.withdrawal_id,
            "reference": f"DEMO-{reference}",
            "message": "DEMO MODE — transfer simulated",
        }

    # ---------- REAL Paystack transfer ----------
    try:
        resp = requests.post(
            PAYSTACK_TRANSFER_URL,
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
            json={
                "source": "balance",
                "amount": amount_pesewas,
                "recipient": seller.paystack_recipient_code,
                "reason": f"Builder Wood withdrawal #{w.withdrawal_id}",
                "reference": reference,
            },
            timeout=20,
        )
        data = resp.json()
    except requests.RequestException as exc:
        raise HTTPException(502, f"Paystack unreachable: {exc}")

    if not data.get("status"):
        w.status = "failed"
        w.notes = (w.notes or "") + f"\nFailed: {data.get('message')}"
        db.commit()
        raise HTTPException(400, f"Transfer failed: {data.get('message')}")

    w.status = "processing"
    w.paystack_transfer_ref = reference
    w.processed_at = datetime.utcnow()
    db.commit()
    return {
        "status": "processing",
        "withdrawal_id": w.withdrawal_id,
        "reference": reference,
    }


@router.post("/withdrawals/{withdrawal_id}/reject")
def reject_withdrawal(
    withdrawal_id: int,
    reason: str = "Rejected by admin",
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Reject a withdrawal — refunds the amount to the seller's wallet."""
    w = db.query(models.WithdrawalRequest).filter(
        models.WithdrawalRequest.withdrawal_id == withdrawal_id
    ).with_for_update().first()

    if not w:
        raise HTTPException(404, "Withdrawal not found")
    if w.status != "pending":
        raise HTTPException(400, f"Cannot reject withdrawal in status: {w.status}")

    credit_wallet(
        db,
        seller_id=w.seller_id,
        amount=Decimal(str(w.amount)),
        withdrawal_id=w.withdrawal_id,
        description=f"Refund for rejected withdrawal #{w.withdrawal_id}",
    )

    w.status = "rejected"
    w.processed_at = datetime.utcnow()
    w.notes = (w.notes or "") + f"\nRejected: {reason}"
    db.commit()
    return {"status": "rejected", "withdrawal_id": w.withdrawal_id}