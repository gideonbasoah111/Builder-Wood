"""Admin wallet + withdrawal management."""
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from builder_wood.app.database import get_db
from builder_wood.app import models
from builder_wood.app.auth import get_current_admin
from builder_wood.app.config import settings
from builder_wood.app.wallet_service import credit_wallet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-wallet"])

PAYSTACK_TRANSFER_URL = "https://api.paystack.co/transfer"
PAYSTACK_TRANSFER_VERIFY_URL = "https://api.paystack.co/transfer"  # GET /transfer/:id

TERMINAL_STATUSES = {"completed", "failed", "rejected"}


# ============================================================
# Request bodies
# ============================================================
class RejectBody(BaseModel):
    reason: str = Field(default="Rejected by admin", max_length=500)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _paystack_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _serialize(w: models.WithdrawalRequest) -> dict:
    """Single source of truth for withdrawal JSON shape."""
    return {
        "withdrawal_id": w.withdrawal_id,
        "seller_id": w.seller_id,
        "seller_name": w.seller.name if w.seller else "Unknown",
        "seller_phone": w.seller.phone_number if w.seller else None,
        "amount": str(w.amount),          # preserve decimal precision
        "currency": getattr(w, "currency", "GHS"),
        "status": w.status,
        "payout_method": w.payout_method,
        "payout_account": w.payout_account,
        "paystack_transfer_ref": w.paystack_transfer_ref,
        "paystack_transfer_code": getattr(w, "paystack_transfer_code", None),
        "requested_at": w.requested_at.isoformat() if w.requested_at else None,
        "processed_at": w.processed_at.isoformat() if w.processed_at else None,
        "processed_by": getattr(w, "processed_by", None),
        "notes": w.notes,
        "seller_has_recipient": bool(w.seller and w.seller.paystack_recipient_code),
    }


# ============================================================
# GET /admin/withdrawals
# ============================================================
@router.get("/withdrawals")
def list_withdrawals(
    status: Optional[str] = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    query = (
        db.query(models.WithdrawalRequest)
        .options(joinedload(models.WithdrawalRequest.seller))
    )
    if status:
        query = query.filter(models.WithdrawalRequest.status == status)

    withdrawals = (
        query.order_by(models.WithdrawalRequest.requested_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [_serialize(w) for w in withdrawals]


# ============================================================
# POST /admin/withdrawals/{id}/approve
# ============================================================
@router.post("/withdrawals/{withdrawal_id}/approve")
def approve_withdrawal(
    withdrawal_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    # Row lock prevents two admins approving the same withdrawal concurrently.
    w = (
        db.query(models.WithdrawalRequest)
        .filter(models.WithdrawalRequest.withdrawal_id == withdrawal_id)
        .with_for_update()
        .first()
    )

    if not w:
        raise HTTPException(404, "Withdrawal not found")
    if w.status != "pending":
        raise HTTPException(400, f"Cannot approve withdrawal in status: {w.status}")

    seller = (
        db.query(models.User)
        .filter(models.User.user_id == w.seller_id)
        .first()
    )
    if not seller or not seller.paystack_recipient_code:
        raise HTTPException(400, "Seller has no payout account on file")

    # Idempotency: reuse a previously generated reference so a retry after a
    # network timeout cannot create a second transfer (Paystack dedupes on it).
    reference = (
        w.paystack_transfer_ref
        or f"BW-WD-{w.withdrawal_id}-{uuid.uuid4().hex[:8]}".lower()
    )
    amount_pesewas = int((Decimal(str(w.amount)) * 100).quantize(Decimal("1")))

    if amount_pesewas <= 0:
        raise HTTPException(400, "Invalid withdrawal amount")

    # Persist reference BEFORE the external call so it survives a crash/timeout.
    w.paystack_transfer_ref = reference
    w.processed_by = admin.user_id
    db.commit()

    if os.getenv("DEMO_MODE", "false").lower() == "true":
        w.status = "completed"
        w.paystack_transfer_ref = f"DEMO-{reference}"
        w.processed_at = _now()
        w.notes = (w.notes or "") + f"\n[DEMO MODE] Simulated transfer by admin #{admin.user_id}"
        db.commit()
        return {
            "status": "completed",
            "withdrawal_id": w.withdrawal_id,
            "reference": f"DEMO-{reference}",
            "message": "DEMO MODE — transfer simulated",
        }

    try:
        resp = requests.post(
            PAYSTACK_TRANSFER_URL,
            headers=_paystack_headers(),
            json={
                "source": "balance",
                "amount": amount_pesewas,
                "recipient": seller.paystack_recipient_code,
                "reason": f"Builder Wood withdrawal #{w.withdrawal_id}",
                "reference": reference,
            },
            timeout=30,
        )
        data = resp.json()
    except requests.RequestException as exc:
        # Status stays 'pending' but the reference is saved, so re-running this
        # endpoint is safe: Paystack returns the original transfer for a dup ref.
        logger.error(f"Paystack unreachable for withdrawal {w.withdrawal_id}: {exc}")
        raise HTTPException(
            502,
            "Paystack unreachable. Re-run approve — the same reference will be reused.",
        )

    # Duplicate reference means the first attempt actually landed.
    if not data.get("status") and "duplicate" in str(data.get("message", "")).lower():
        w.status = "processing"
        w.processed_at = _now()
        w.notes = (w.notes or "") + "\nDuplicate reference detected; treating as submitted."
        db.commit()
        return {
            "status": "processing",
            "withdrawal_id": w.withdrawal_id,
            "reference": reference,
            "message": "Transfer was already submitted with this reference.",
        }

    if not data.get("status"):
        msg = data.get("message", "Unknown error")
        w.status = "failed"
        w.processed_at = _now()
        w.notes = (w.notes or "") + f"\nFailed: {msg}"
        db.commit()

        # Return the funds — the seller's balance must not be lost.
        credit_wallet(
            db,
            seller_id=w.seller_id,
            amount=Decimal(str(w.amount)),
            withdrawal_id=w.withdrawal_id,
            description=f"Refund for failed transfer #{w.withdrawal_id}",
        )
        db.commit()

        raise HTTPException(400, f"Transfer failed: {msg}")

    payload = data.get("data", {})
    w.status = "processing"
    w.processed_at = _now()
    if hasattr(w, "paystack_transfer_code"):
        w.paystack_transfer_code = payload.get("integration_code") or payload.get("id")
    db.commit()

    return {
        "status": "processing",
        "withdrawal_id": w.withdrawal_id,
        "reference": reference,
        "paystack_id": payload.get("id"),
    }


# ============================================================
# POST /admin/withdrawals/{id}/reject
# ============================================================
@router.post("/withdrawals/{withdrawal_id}/reject")
def reject_withdrawal(
    withdrawal_id: int,
    payload: RejectBody,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    now = _now()

    # Atomic conditional update: exactly one caller can flip pending -> rejected.
    # A second concurrent admin sees rowcount 0 and cannot trigger a double refund.
    rows = (
        db.query(models.WithdrawalRequest)
        .filter(
            models.WithdrawalRequest.withdrawal_id == withdrawal_id,
            models.WithdrawalRequest.status == "pending",
        )
        .update(
            {
                "status": "rejected",
                "processed_at": now,
                "processed_by": admin.user_id,
                "notes": func.concat(
                    func.coalesce(models.WithdrawalRequest.notes, ""),
                    f"\nRejected by admin #{admin.user_id}: {payload.reason}",
                ),
            },
            synchronize_session=False,
        )
    )

    if rows == 0:
        db.rollback()
        existing = (
            db.query(models.WithdrawalRequest)
            .filter(models.WithdrawalRequest.withdrawal_id == withdrawal_id)
            .first()
        )
        if not existing:
            raise HTTPException(404, "Withdrawal not found")
        raise HTTPException(
            400, f"Cannot reject withdrawal in status: {existing.status}"
        )

    w = (
        db.query(models.WithdrawalRequest)
        .filter(models.WithdrawalRequest.withdrawal_id == withdrawal_id)
        .first()
    )

    # Status is durably 'rejected', so crediting cannot double-fire from a race.
    credit_wallet(
        db,
        seller_id=w.seller_id,
        amount=Decimal(str(w.amount)),
        withdrawal_id=w.withdrawal_id,
        description=f"Refund for rejected withdrawal #{w.withdrawal_id}",
    )
    db.commit()

    return {"status": "rejected", "withdrawal_id": w.withdrawal_id}


# ============================================================
# POST /admin/withdrawals/{id}/sync   (poll Paystack for 'processing')
# ============================================================
@router.post("/withdrawals/{withdrawal_id}/sync")
def sync_withdrawal_status(
    withdrawal_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Move 'processing' -> 'completed'/'failed' using Paystack's own state."""
    w = (
        db.query(models.WithdrawalRequest)
        .filter(models.WithdrawalRequest.withdrawal_id == withdrawal_id)
        .with_for_update()
        .first()
    )
    if not w:
        raise HTTPException(404, "Withdrawal not found")
    if w.status != "processing":
        return {"status": w.status, "changed": False}

    transfer_code = getattr(w, "paystack_transfer_code", None)
    if not transfer_code:
        raise HTTPException(400, "No Paystack transfer code recorded; check manually")

    try:
        resp = requests.get(
            f"{PAYSTACK_TRANSFER_VERIFY_URL}/{transfer_code}",
            headers=_paystack_headers(),
            timeout=20,
        )
        data = resp.json()
    except requests.RequestException as exc:
        raise HTTPException(502, f"Paystack unreachable: {exc}")

    ps_status = (data.get("data") or {}).get("status")
    if ps_status == "DONE":
        w.status = "completed"
        db.commit()
    elif ps_status in ("FAILED", "REVERSED"):
        w.status = "failed"
        w.notes = (w.notes or "") + f"\nPaystack reported {ps_status}"
        db.commit()
        credit_wallet(
            db,
            seller_id=w.seller_id,
            amount=Decimal(str(w.amount)),
            withdrawal_id=w.withdrawal_id,
            description=f"Refund for failed transfer #{w.withdrawal_id}",
        )
        db.commit()
    else:
        return {"status": "processing", "changed": False, "paystack_status": ps_status}

    return {"status": w.status, "changed": True}


# ============================================================
# GET /admin/wallet/summary
# ============================================================
@router.get("/wallet/summary")
def wallet_summary(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    def total(status_value: str) -> str:
        value = (
            db.query(func.coalesce(func.sum(models.WithdrawalRequest.amount), 0))
            .filter(models.WithdrawalRequest.status == status_value)
            .scalar()
        )
        return str(value)

    return {
        "pending_count": db.query(models.WithdrawalRequest)
        .filter(models.WithdrawalRequest.status == "pending")
        .count(),
        "pending_total": total("pending"),
        "processing_total": total("processing"),
        "completed_total": total("completed"),
        "failed_total": total("failed"),
        "rejected_total": total("rejected"),
    }