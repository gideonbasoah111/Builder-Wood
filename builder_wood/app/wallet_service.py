
"""
Wallet service — all wallet money movements go through this module.

Rules:
- Every credit/debit creates a WalletTransaction audit row.
- Wallet balance must never become negative.
- Admin order release credits the seller wallet, NOT Paystack.
- Withdrawal request debits/reserves wallet funds.
- Paystack transfer is handled separately during admin approval.
- This module never commits; the calling route owns the transaction.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from builder_wood.app import models


MONEY_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value, field_name: str = "Amount") -> Decimal:
    """Convert and validate a monetary value to two decimal places."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, f"{field_name} is invalid.")

    if not amount.is_finite():
        raise HTTPException(400, f"{field_name} must be a finite number.")

    amount = amount.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)

    if amount <= ZERO:
        raise HTTPException(400, f"{field_name} must be greater than zero.")

    return amount


def _now():
    return datetime.now(timezone.utc)


def _lock_seller(db: Session, seller_id: int):
    """
    Lock the seller row before creating or accessing their wallet.

    Locking the existing user row also serializes first-time wallet creation,
    where there is no wallet row available to lock yet.
    """
    seller = (
        db.query(models.User)
        .filter(models.User.user_id == seller_id)
        .with_for_update()
        .first()
    )

    if not seller:
        raise HTTPException(404, "Seller account not found.")

    if seller.role != "seller":
        raise HTTPException(400, "Wallet owner is not a seller.")

    if not seller.is_active:
        raise HTTPException(400, "Seller account is inactive.")

    return seller


def get_or_create_wallet(
    db: Session,
    seller_id: int,
    *,
    lock_seller: bool = True,
) -> models.SellerWallet:
    """
    Get a seller wallet or create it on first access.

    The seller row is locked first to prevent two concurrent requests from
    creating duplicate wallets for the same seller.
    """
    if lock_seller:
        _lock_seller(db, seller_id)

    wallet = (
        db.query(models.SellerWallet)
        .filter(models.SellerWallet.seller_id == seller_id)
        .with_for_update()
        .first()
    )

    if wallet:
        return wallet

    wallet = models.SellerWallet(
        seller_id=seller_id,
        balance=ZERO,
        total_earned=ZERO,
        total_withdrawn=ZERO,
    )

    db.add(wallet)
    db.flush()

    return wallet


def credit_wallet(
    db: Session,
    seller_id: int,
    amount: Decimal,
    order_id: Optional[int] = None,
    description: str = "",
    reference: Optional[str] = None,
) -> models.SellerWallet:
    """Credit a seller wallet and create its audit transaction."""

    amount = _money(amount, "Credit amount")

    wallet = get_or_create_wallet(db, seller_id)

    current_balance = Decimal(str(wallet.balance or ZERO))
    new_balance = (current_balance + amount).quantize(MONEY_PLACES)

    wallet.balance = new_balance
    wallet.total_earned = (
        Decimal(str(wallet.total_earned or ZERO)) + amount
    ).quantize(MONEY_PLACES)

    transaction = models.WalletTransaction(
        wallet_id=wallet.wallet_id,
        type="credit",
        amount=amount,
        balance_after=new_balance,
        order_id=order_id,
        reference=reference or f"CREDIT-{uuid.uuid4().hex}",
        description=(
            description or f"Wallet credit of GH₵{amount:.2f}"
        )[:255],
    )

    db.add(transaction)
    db.flush()

    return wallet


def debit_wallet(
    db: Session,
    seller_id: int,
    amount: Decimal,
    withdrawal_id: Optional[int] = None,
    description: str = "",
    reference: Optional[str] = None,
) -> models.SellerWallet:
    """Debit a seller wallet and create its audit transaction."""

    amount = _money(amount, "Debit amount")

    wallet = get_or_create_wallet(db, seller_id)

    current_balance = Decimal(str(wallet.balance or ZERO))

    if current_balance < amount:
        raise HTTPException(
            400,
            (
                f"Insufficient balance. Available: "
                f"GH₵{current_balance:.2f}, requested: GH₵{amount:.2f}"
            ),
        )

    new_balance = (current_balance - amount).quantize(MONEY_PLACES)

    if new_balance < ZERO:
        raise HTTPException(400, "Wallet balance cannot become negative.")

    wallet.balance = new_balance

    # This tracks money debited from the wallet for withdrawal requests.
    # It does not independently prove that a bank transfer succeeded.
    wallet.total_withdrawn = (
        Decimal(str(wallet.total_withdrawn or ZERO)) + amount
    ).quantize(MONEY_PLACES)

    transaction = models.WalletTransaction(
        wallet_id=wallet.wallet_id,
        type="debit",
        amount=amount,
        balance_after=new_balance,
        withdrawal_id=withdrawal_id,
        reference=reference or f"DEBIT-{uuid.uuid4().hex}",
        description=(
            description or f"Wallet debit of GH₵{amount:.2f}"
        )[:255],
    )

    db.add(transaction)
    db.flush()

    return wallet


def release_order_to_wallet(
    db: Session,
    order: models.Order,
) -> str:
    """
    Release an eligible order payout into the seller's wallet.

    This is a wallet credit, not a Paystack transfer.

    The order is locked before checking its settlement state. This prevents
    concurrent admin requests from crediting the same order twice.

    The caller must commit the transaction.
    """

    locked_order = (
        db.query(models.Order)
        .filter(models.Order.order_id == order.order_id)
        .with_for_update()
        .first()
    )

    if not locked_order:
        raise HTTPException(404, "Order not found.")

    # Idempotency: an already-settled order must never be credited again.
    if locked_order.settlement_status == "settled":
        return locked_order.transfer_reference or ""

    if locked_order.is_funds_released:
        raise HTTPException(
            409,
            "Order release state is inconsistent. Review this order manually.",
        )

    if locked_order.payment_status != "paid":
        raise HTTPException(400, "Order has not been paid.")

    if locked_order.delivery_status != "delivered":
        raise HTTPException(400, "Order has not been delivered.")

    if locked_order.frozen_payout:
        raise HTTPException(
            409,
            "Order payout is frozen because of an active dispute.",
        )

    # Also protect against an existing credit transaction if order state
    # was accidentally left incomplete by an earlier operation.
    existing_credit = (
        db.query(models.WalletTransaction)
        .filter(
            models.WalletTransaction.order_id == locked_order.order_id,
            models.WalletTransaction.type == "credit",
        )
        .first()
    )

    if existing_credit:
        raise HTTPException(
            409,
            "A wallet credit already exists for this order. Manual review required.",
        )

    amount = _money(
        locked_order.seller_payout_amount,
        "Seller payout amount",
    )

    reference = (
        f"WALLET-ORDER-{locked_order.order_id}-{uuid.uuid4().hex[:12]}"
    )

    credit_wallet(
        db=db,
        seller_id=locked_order.seller_id,
        amount=amount,
        order_id=locked_order.order_id,
        description=f"Seller payout for order #{locked_order.order_id}",
        reference=reference,
    )

    locked_order.settlement_status = "settled"
    locked_order.transfer_reference = reference
    locked_order.funds_released_at = _now()
    locked_order.is_funds_released = True
    locked_order.transfer_failed_reason = None

    db.flush()

    # Keep the caller's supplied ORM object synchronized.
    order.settlement_status = locked_order.settlement_status
    order.transfer_reference = locked_order.transfer_reference
    order.funds_released_at = locked_order.funds_released_at
    order.is_funds_released = locked_order.is_funds_released
    order.transfer_failed_reason = None

    return reference


def create_withdrawal_request(
    db: Session,
    seller_id: int,
    amount: Decimal,
    notes: Optional[str] = None,
) -> models.WithdrawalRequest:
    """
    Create a seller withdrawal request.

    Funds are debited from the available wallet balance immediately.
    Paystack must not be called from this function.

    The caller must commit the transaction.
    """

    amount = _money(amount, "Withdrawal amount")

    seller = _lock_seller(db, seller_id)
    wallet = get_or_create_wallet(db, seller_id, lock_seller=False)

    minimum = Decimal(str(wallet.min_withdrawal or ZERO))

    if amount < minimum:
        raise HTTPException(
            400,
            f"Minimum withdrawal is GH₵{minimum:.2f}.",
        )

    current_balance = Decimal(str(wallet.balance or ZERO))

    if current_balance < amount:
        raise HTTPException(
            400,
            f"Insufficient balance. Available: GH₵{current_balance:.2f}.",
        )

    if not seller.paystack_recipient_code:
        raise HTTPException(
            400,
            "No verified payout account is registered.",
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

    # Link the exact debit transaction to this withdrawal.
    debit_wallet(
        db=db,
        seller_id=seller_id,
        amount=amount,
        withdrawal_id=withdrawal.withdrawal_id,
        description=f"Withdrawal request #{withdrawal.withdrawal_id}",
        reference=f"WITHDRAWAL-{withdrawal.withdrawal_id}",
    )

    db.flush()

    return withdrawal


def auto_create_withdrawal_if_eligible(
    db: Session,
    seller_id: int,
) -> Optional[models.WithdrawalRequest]:
    """
    Create an automatic withdrawal when the wallet reaches its threshold.

    No Paystack transfer is initiated here.

    If the seller has no payout recipient, funds remain in the wallet.
    The caller must commit the transaction.
    """

    seller = _lock_seller(db, seller_id)
    wallet = get_or_create_wallet(db, seller_id, lock_seller=False)

    threshold = Decimal(
        str(wallet.auto_withdrawal_threshold or ZERO)
    )
    balance = Decimal(str(wallet.balance or ZERO))

    if threshold <= ZERO or balance < threshold:
        return None

    if not seller.paystack_recipient_code:
        return None

    if balance < Decimal(str(wallet.min_withdrawal or ZERO)):
        return None

    return create_withdrawal_request(
        db=db,
        seller_id=seller_id,
        amount=balance,
        notes="Automatic withdrawal — wallet threshold reached.",
    )
