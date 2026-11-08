"""Seller wallet API."""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from builder_wood.app.database import get_db
from builder_wood.app import models
from builder_wood.app.auth import get_current_active_seller
from builder_wood.app.wallet_service import (
    get_or_create_wallet,
    create_withdrawal_request,
)

router = APIRouter(prefix="/seller/wallet", tags=["seller-wallet"])


class WithdrawalRequestIn(BaseModel):
    amount: Decimal = Field(gt=0)
    notes: Optional[str] = None


@router.get("/")
def get_my_wallet(
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    wallet = get_or_create_wallet(db, current_seller.user_id)

    pending_withdrawals = (
        db.query(models.WithdrawalRequest)
        .filter(
            models.WithdrawalRequest.seller_id == current_seller.user_id,
            models.WithdrawalRequest.status.in_(["pending", "approved", "processing"]),
        )
        .all()
    )

    return {
        "balance": float(wallet.balance),
        "total_earned": float(wallet.total_earned),
        "total_withdrawn": float(wallet.total_withdrawn),
        "min_withdrawal": float(wallet.min_withdrawal),
        "auto_withdrawal_threshold": float(wallet.auto_withdrawal_threshold),
        "pending_withdrawals_count": len(pending_withdrawals),
        "pending_withdrawals_total": float(sum(w.amount for w in pending_withdrawals)),
        "has_payout_account": bool(current_seller.paystack_recipient_code),
    }


@router.get("/transactions")
def list_transactions(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    wallet = get_or_create_wallet(db, current_seller.user_id)
    txs = (
        db.query(models.WalletTransaction)
        .filter(models.WalletTransaction.wallet_id == wallet.wallet_id)
        .order_by(models.WalletTransaction.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "transaction_id": t.transaction_id,
            "type": t.type,
            "amount": float(t.amount),
            "balance_after": float(t.balance_after),
            "order_id": t.order_id,
            "withdrawal_id": t.withdrawal_id,
            "description": t.description,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in txs
    ]


@router.post("/withdrawals")
def request_withdrawal(
    payload: WithdrawalRequestIn,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    withdrawal = create_withdrawal_request(
        db,
        seller_id=current_seller.user_id,
        amount=payload.amount,
        notes=payload.notes,
    )
    return {
        "withdrawal_id": withdrawal.withdrawal_id,
        "amount": float(withdrawal.amount),
        "status": withdrawal.status,
        "requested_at": withdrawal.requested_at.isoformat() if withdrawal.requested_at else None,
    }


@router.get("/withdrawals")
def list_my_withdrawals(
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    withdrawals = (
        db.query(models.WithdrawalRequest)
        .filter(models.WithdrawalRequest.seller_id == current_seller.user_id)
        .order_by(models.WithdrawalRequest.requested_at.desc())
        .all()
    )
    return [
        {
            "withdrawal_id": w.withdrawal_id,
            "amount": float(w.amount),
            "status": w.status,
            "payout_method": w.payout_method,
            "payout_account": w.payout_account,
            "requested_at": w.requested_at.isoformat() if w.requested_at else None,
            "processed_at": w.processed_at.isoformat() if w.processed_at else None,
            "notes": w.notes,
        }
        for w in withdrawals
    ]