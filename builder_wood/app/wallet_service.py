"""
Wallet service — all wallet money movements go through this module.

Rules:
- Every credit/debit creates a WalletTransaction row (audit log)
- Balance never goes below 0
- Admin release = wallet credit (NOT Paystack)
- Withdrawal request = wallet debit (with balance check)
- Paystack transfer only fires on admin approval of withdrawal
"""
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from builder_wood.app import models


def get_or_create_wallet(db: Session, seller_id: int) -> models.SellerWallet:
    """Get the seller's wallet, or create it on first access."""
    wallet = (
        db.query(models.SellerWallet)
        .filter(models.SellerWallet.seller_id == seller_id)
        .with_for_update()
        .first()
    )
    if not wallet:
        wallet = models.SellerWallet(
            seller_id=seller_id,
            balance=Decimal("0"),
            total_earned=Decimal("0"),
            total_withdrawn=Decimal("0"),
        )
        db.add(wallet)
        db.flush()
    return wallet


def credit_wallet(
    db: Session,
    seller_id: int,
    amount: Decimal,
    order_id: int = None,
    description: str = "",
    reference: str = None,
) -> models.SellerWallet:
    """Add money to seller wallet (e.g., release order payout)."""
    if amount <= 0:
        raise HTTPException(400, "Credit amount must be positive.")

    wallet = get_or_create_wallet(db, seller_id)

    wallet.balance += amount
    wallet.total_earned += amount

    db.add(models.WalletTransaction(
        wallet_id=wallet.wallet_id,
        type="credit",
        amount=amount,
        balance_after=wallet.balance,
        order_id=order_id,
        reference=reference or f"CREDIT-{uuid.uuid4().hex[:8]}",
        description=description or f"Credit of GH₵{amount}",
    ))
    db.flush()
    return wallet


def debit_wallet(
    db: Session,
    seller_id: int,
    amount: Decimal,
    withdrawal_id: int = None,
    description: str = "",
    reference: str = None,
) -> models.SellerWallet:
    """Remove money from seller wallet (e.g., withdrawal processing)."""
    if amount <= 0:
        raise HTTPException(400, "Debit amount must be positive.")

    wallet = get_or_create_wallet(db, seller_id)

    if wallet.balance < amount:
        raise HTTPException(
            400,
            f"Insufficient balance. Available: GH₵{wallet.balance:.2f}, "
            f"requested: GH₵{amount:.2f}",
        )

    wallet.balance -= amount
    wallet.total_withdrawn += amount

    db.add(models.WalletTransaction(
        wallet_id=wallet.wallet_id,
        type="debit",
        amount=amount,
        balance_after=wallet.balance,
        withdrawal_id=withdrawal_id,
        reference=reference or f"DEBIT-{uuid.uuid4().hex[:8]}",
        description=description or f"Debit of GH₵{amount}",
    ))
    db.flush()
    return wallet


def release_order_to_wallet(db: Session, order: models.Order) -> str:
    """
    Move an order's seller_payout_amount into the seller's wallet.
    No Paystack call here — that happens at withdrawal time.
    """
    if order.settlement_status == "settled":
        return order.transfer_reference or ""

    if order.payment_status != "paid":
        raise HTTPException(400, "Order not paid.")

    if order.delivery_status != "delivered":
        raise HTTPException(400, "Order not delivered yet.")

    if order.frozen_payout:
        raise HTTPException(400, "Order payout is frozen (dispute open).")

    amount = Decimal(str(order.seller_payout_amount or 0))
    if amount <= 0:
        raise HTTPException(400, "No payout amount.")

    reference = f"WALLET-CREDIT-{order.order_id}-{uuid.uuid4().hex[:8]}"

    credit_wallet(
        db,
        seller_id=order.seller_id,
        amount=amount,
        order_id=order.order_id,
        description=f"Order #{order.order_id} payout",
        reference=reference,
    )

    order.settlement_status = "settled"
    order.transfer_reference = reference
    order.funds_released_at = datetime.utcnow()
    order.is_funds_released = True
    db.commit()
    return reference


def create_withdrawal_request(
    db: Session,
    seller_id: int,
    amount: Decimal,
    notes: str = None,
) -> models.WithdrawalRequest:
    """Seller-initiated withdrawal. Debits wallet, creates request."""
    wallet = get_or_create_wallet(db, seller_id)

    if amount < wallet.min_withdrawal:
        raise HTTPException(
            400,
            f"Minimum withdrawal is GH₵{wallet.min_withdrawal:.2f}",
        )

    if wallet.balance < amount:
        raise HTTPException(
            400,
            f"Insufficient balance. Available: GH₵{wallet.balance:.2f}",
        )

    seller = db.query(models.User).filter(models.User.user_id == seller_id).first()
    if not seller or not seller.paystack_recipient_code:
        raise HTTPException(400, "No payout account registered.")

    debit_wallet(
        db,
        seller_id=seller_id,
        amount=amount,
        description=f"Withdrawal of GH₵{amount}",
    )

    withdrawal = models.WithdrawalRequest(
        seller_id=seller_id,
        amount=amount,
        status="pending",
        payout_method=seller.payout_bank_name or "unknown",
        payout_account=seller.payout_account_number,
        notes=notes,
    )
    db.add(withdrawal)
    db.flush()

    tx = (
        db.query(models.WalletTransaction)
        .filter(models.WalletTransaction.wallet_id == wallet.wallet_id)
        .order_by(models.WalletTransaction.transaction_id.desc())
        .first()
    )
    if tx:
        tx.withdrawal_id = withdrawal.withdrawal_id

    db.commit()
    return withdrawal


def auto_create_withdrawal_if_eligible(db: Session, seller_id: int):
    """Auto-create withdrawal if balance exceeds threshold."""
    wallet = get_or_create_wallet(db, seller_id)
    if wallet.balance >= wallet.auto_withdrawal_threshold:
        try:
            create_withdrawal_request(
                db,
                seller_id=seller_id,
                amount=wallet.balance,
                notes="Auto-withdrawal (threshold reached)",
            )
        except HTTPException:
            pass