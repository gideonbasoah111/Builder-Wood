# builder_wood/app/routers/payments.py

import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from builder_wood.app import models
from builder_wood.app.auth import get_current_user
from builder_wood.app.config import settings
from builder_wood.app.database import get_db


router = APIRouter(prefix="/payments", tags=["Payments"])

PAYSTACK_INITIALIZE_URL = "https://api.paystack.co/transaction/initialize"
PAYSTACK_VERIFY_URL = "https://api.paystack.co/transaction/verify/"
PAYSTACK_TRANSFER_URL = "https://api.paystack.co/transfer"

_session = requests.Session()


def get_paystack_secret() -> str:
    """Read the Paystack secret key from application settings."""
    secret = getattr(settings, "PAYSTACK_SECRET_KEY", None)
    if not secret:
        raise HTTPException(500, "Paystack secret key is not configured.")
    return secret


def amount_to_pesewas(amount) -> int:
    """Convert a GHS amount to pesewas without using float arithmetic."""
    value = Decimal(str(amount))
    return int((value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def verify_with_paystack(reference: str) -> dict:
    """Verify a transaction directly with Paystack's API."""
    secret = get_paystack_secret()
    try:
        response = _session.get(
            f"{PAYSTACK_VERIFY_URL}{quote(reference, safe='')}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException:
        raise HTTPException(502, "Could not verify payment with Paystack.")
    if not result.get("status") or not isinstance(result.get("data"), dict):
        raise HTTPException(502, "Paystack could not verify this transaction.")
    return result["data"]


def validate_successful_payment(
    gateway_data: dict,
    transaction: models.PaymentTransaction,
) -> bool:
    """Check the verified payment against the stored transaction."""
    return (
        gateway_data.get("status") == "success"
        and gateway_data.get("reference") == transaction.reference
        and str(gateway_data.get("currency", "")).upper() == transaction.currency.upper()
        and gateway_data.get("amount") == amount_to_pesewas(transaction.amount)
    )


def record_payment_success(
    db: Session,
    transaction: models.PaymentTransaction,
    gateway_data: dict,
) -> None:
    """Record a verified payment without downgrading order progress."""
    order = (
        db.query(models.Order)
        .filter(models.Order.order_id == transaction.order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(404, "Order not found.")
    if transaction.status == "success":
        return
    if order.status == "cancelled":
        raise HTTPException(409, "This order has been cancelled.")

    transaction.status = "success"
    transaction.provider = "paystack"
    transaction.gateway_response = json.dumps(gateway_data, default=str)

    order.payment_status = "paid"

    if order.status == "pending":
        order.status = "confirmed"
        db.add(models.OrderStatusHistory(
            order_id=order.order_id,
            old_status="pending",
            new_status="confirmed",
            changed_by=None,
        ))
    db.commit()


class PaymentInitIn(BaseModel):
    order_id: int


@router.post("/initialize")
def initialize_payment(
    payload: PaymentInitIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = (
        db.query(models.Order)
        .filter(
            models.Order.order_id == payload.order_id,
            models.Order.customer_id == current_user.user_id,
        )
        .first()
    )
    if not order:
        raise HTTPException(404, "Order not found.")
    if order.status != "pending":
        raise HTTPException(400, f"Cannot pay for order in status {order.status}.")
    if not current_user.email:
        raise HTTPException(400, "Account must have an email.")

    transaction = (
        db.query(models.PaymentTransaction)
        .filter(
            models.PaymentTransaction.order_id == order.order_id,
            models.PaymentTransaction.status == "pending",
        )
        .order_by(models.PaymentTransaction.transaction_id.desc())
        .first()
    )
    if not transaction:
        raise HTTPException(404, "No pending payment transaction.")

    if (
        Decimal(str(transaction.amount)) != Decimal(str(order.total_price))
        or transaction.currency.upper() != order.currency.upper()
    ):
        raise HTTPException(409, "Order total and payment amount mismatch.")

    secret = get_paystack_secret()
    base_url = str(settings.BASE_URL).rstrip("/")
    transaction.provider = "paystack"
    db.commit()

    body = {
        "email": current_user.email,
        "amount": amount_to_pesewas(transaction.amount),
        "currency": transaction.currency.upper(),
        "reference": transaction.reference,
        "callback_url": f"{base_url}/payments/callback",
        "channels": ["card", "mobile_money"],
        "metadata": {
            "order_id": order.order_id,
            "transaction_id": transaction.transaction_id,
            "customer_id": current_user.user_id,
        },
    }

    try:
        response = _session.post(
            PAYSTACK_INITIALIZE_URL,
            json=body,
            headers={"Authorization": f"Bearer {secret}"},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException:
        raise HTTPException(502, "Could not initialize payment.")

    if not result.get("status") or not result.get("data", {}).get("authorization_url"):
        raise HTTPException(502, "Paystack did not return a checkout URL.")

    return {
        "order_id": order.order_id,
        "reference": transaction.reference,
        "authorization_url": result["data"]["authorization_url"],
        "access_code": result["data"].get("access_code"),
    }


@router.get("/callback")
def payment_callback(reference: str, db: Session = Depends(get_db)):
    transaction = (
        db.query(models.PaymentTransaction)
        .filter(models.PaymentTransaction.reference == reference)
        .first()
    )
    if not transaction:
        raise HTTPException(404, "Payment reference not found.")

    gateway_data = verify_with_paystack(reference)

    if not validate_successful_payment(gateway_data, transaction):
        print(
            f"[payments] validation failed: "
            f"expected amount={amount_to_pesewas(transaction.amount)} "
            f"got={gateway_data.get('amount')}, "
            f"expected currency={transaction.currency} "
            f"got={gateway_data.get('currency')}, "
            f"gateway status={gateway_data.get('status')}"
        )
        return RedirectResponse(
            url=f"{str(settings.BASE_URL).rstrip('/')}/order/{transaction.order_id}?payment=failed",
            status_code=303,
        )

    order_id = transaction.order_id
    try:
        record_payment_success(db, transaction, gateway_data)
    except Exception:
        db.rollback()
        raise

    return RedirectResponse(
        url=f"{str(settings.BASE_URL).rstrip('/')}/order/{order_id}?payment=success",
        status_code=303,
    )


@router.post("/webhook")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    secret = get_paystack_secret()
    raw_body = await request.body()
    received = request.headers.get("x-paystack-signature")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()

    if not received or not hmac.compare_digest(received, expected):
        raise HTTPException(401, "Invalid signature.")

    try:
        event = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid payload.")

    event_type = event.get("event")
    data = event.get("data") or {}

    # Handle transfer confirmations (escrow settlement)
    if event_type in ("transfer.success", "transfer.failed", "transfer.reversed"):
        await _handle_transfer_event(db, event_type, data)
        return JSONResponse({"status": "ok"})

    # Handle charge success (order payment)
    if event_type != "charge.success":
        return JSONResponse({"status": "ignored"})

    reference = data.get("reference")
    if not reference:
        raise HTTPException(400, "No reference.")

    transaction = (
        db.query(models.PaymentTransaction)
        .filter(models.PaymentTransaction.reference == reference)
        .first()
    )
    if not transaction:
        return JSONResponse({"status": "ignored"})

    gateway_data = verify_with_paystack(reference)
    if not validate_successful_payment(gateway_data, transaction):
        return JSONResponse({"status": "payment_not_verified"})

    try:
        record_payment_success(db, transaction, gateway_data)
    except Exception:
        db.rollback()
        raise

    return JSONResponse({"status": "success"})


async def _handle_transfer_event(db: Session, event_type: str, data: dict):
    """Update order settlement status when Paystack reports a transfer result."""
    reference = data.get("reference")
    if not reference:
        return

    order = (
        db.query(models.Order)
        .filter(models.Order.transfer_reference == reference)
        .first()
    )
    if not order:
        return

    if event_type == "transfer.success":
        order.settlement_status = "settled"
        order.funds_released_at = datetime.utcnow()
        order.is_funds_released = True
    elif event_type in ("transfer.failed", "transfer.reversed"):
        order.settlement_status = "not_eligible"
        order.transfer_failed_reason = data.get("reason") or event_type

    db.commit()