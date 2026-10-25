
# builder_wood/app/routers/payments.py

import hashlib
import hmac
import json
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


def get_paystack_secret() -> str:
    """Read the Paystack secret key from application settings."""
    secret = getattr(settings, "PAYSTACK_SECRET_KEY", None)

    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Paystack secret key is not configured.",
        )

    return secret


def amount_to_pesewas(amount) -> int:
    """Convert a GHS amount to pesewas without using float arithmetic."""
    value = Decimal(str(amount))
    return int(
        (value * Decimal("100")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def verify_with_paystack(reference: str) -> dict:
    """Verify a transaction directly with Paystack's API."""
    secret = get_paystack_secret()

    try:
        response = requests.get(
            f"{PAYSTACK_VERIFY_URL}{quote(reference, safe='')}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()

    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail="Could not verify payment with Paystack. Please try again.",
        )

    if not result.get("status") or not isinstance(result.get("data"), dict):
        raise HTTPException(
            status_code=502,
            detail="Paystack could not verify this transaction.",
        )

    return result["data"]


def validate_successful_payment(
    gateway_data: dict,
    transaction: models.PaymentTransaction,
) -> bool:
    """Check the verified payment against the stored transaction."""
    return (
        gateway_data.get("status") == "success"
        and gateway_data.get("reference") == transaction.reference
        and str(gateway_data.get("currency", "")).upper()
        == transaction.currency.upper()
        and gateway_data.get("amount")
        == amount_to_pesewas(transaction.amount)
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
        raise HTTPException(status_code=404, detail="Order not found.")

    # Do not process a successful payment twice.
    if transaction.status == "success":
        return

    if order.status == "cancelled":
        raise HTTPException(
            status_code=409,
            detail=(
                "This order has been cancelled. Payment requires "
                "manual review."
            ),
        )

    transaction.status = "success"
    transaction.provider = "paystack"
    transaction.gateway_response = json.dumps(
        gateway_data, default=str
    )

    # Only confirm a pending order. Do not overwrite shipped or delivered.
    if order.status == "pending":
        order.status = "confirmed"
        db.add(
            models.OrderStatusHistory(
                order_id=order.order_id,
                old_status="pending",
                new_status="confirmed",
                changed_by=None,
            )
        )

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
        raise HTTPException(
            status_code=404,
            detail="Order not found or you do not have permission to pay for it.",
        )

    if order.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"This order cannot be paid for because its status is {order.status}.",
        )

    if not current_user.email:
        raise HTTPException(
            status_code=400,
            detail="Your account must have an email address to make payment.",
        )

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
        raise HTTPException(
            status_code=404,
            detail="No pending payment transaction exists for this order.",
        )

    # Protect against discrepancies between the order and payment record.
    if (
        Decimal(str(transaction.amount))
        != Decimal(str(order.total_price))
        or transaction.currency.upper() != order.currency.upper()
    ):
        raise HTTPException(
            status_code=409,
            detail="The order total and payment transaction do not match.",
        )

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
        response = requests.post(
            PAYSTACK_INITIALIZE_URL,
            json=body,
            headers={"Authorization": f"Bearer {secret}"},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()

    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail="Could not initialize payment with Paystack.",
        )

    if (
        not result.get("status")
        or not isinstance(result.get("data"), dict)
        or not result["data"].get("authorization_url")
    ):
        raise HTTPException(
            status_code=502,
            detail="Paystack did not return a valid checkout URL.",
        )

    return {
        "message": "Payment initialized successfully.",
        "order_id": order.order_id,
        "reference": transaction.reference,
        "authorization_url": result["data"]["authorization_url"],
        "access_code": result["data"].get("access_code"),
    }


@router.get("/callback")
def payment_callback(
    reference: str,
    db: Session = Depends(get_db),
):
    transaction = (
        db.query(models.PaymentTransaction)
        .filter(models.PaymentTransaction.reference == reference)
        .first()
    )

    if not transaction:
        raise HTTPException(
            status_code=404,
            detail="Payment reference not found.",
        )

    # Verify against Paystack; never trust the browser redirect alone.
    gateway_data = verify_with_paystack(reference)

    if not validate_successful_payment(gateway_data, transaction):
        if (
            gateway_data.get("reference") == transaction.reference
            and gateway_data.get("status") in ("failed", "abandoned")
            and transaction.status == "pending"
        ):
            transaction.gateway_response = json.dumps(
                gateway_data, default=str
            )
            db.commit()

        return RedirectResponse(
            url=(
                f"{str(settings.BASE_URL).rstrip('/')}"
                f"/order/{transaction.order_id}?payment=failed"
            ),
            status_code=303,
        )

    order_id = transaction.order_id

    try:
        record_payment_success(db, transaction, gateway_data)
    except Exception:
        db.rollback()
        raise

    return RedirectResponse(
        url=(
            f"{str(settings.BASE_URL).rstrip('/')}"
            f"/order/{order_id}?payment=success"
        ),
        status_code=303,
    )


@router.post("/webhook")
async def paystack_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    secret = get_paystack_secret()
    raw_body = await request.body()

    received_signature = request.headers.get("x-paystack-signature")

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()

    if not received_signature or not hmac.compare_digest(
        received_signature, expected_signature
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Paystack webhook signature.",
        )

    try:
        event = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=400,
            detail="Invalid webhook payload.",
        )

    # Acknowledge unrelated event types without changing order/payment data.
    if event.get("event") != "charge.success":
        return JSONResponse({"status": "ignored"})

    event_data = event.get("data") or {}
    reference = event_data.get("reference")

    if not reference:
        raise HTTPException(
            status_code=400,
            detail="Webhook does not contain a payment reference.",
        )

    transaction = (
        db.query(models.PaymentTransaction)
        .filter(models.PaymentTransaction.reference == reference)
        .first()
    )

    # Unknown references should not update any records.
    if not transaction:
        return JSONResponse({"status": "ignored"})

    # Verify independently with Paystack instead of trusting webhook data.
    gateway_data = verify_with_paystack(reference)

    if not validate_successful_payment(gateway_data, transaction):
        return JSONResponse({"status": "payment_not_verified"})

    try:
        record_payment_success(db, transaction, gateway_data)
    except Exception:
        db.rollback()
        raise

    return JSONResponse({"status": "success"})
