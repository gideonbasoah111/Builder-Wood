"""Cron job: release funds for orders past the 48-hour inspection window."""
import sys
from datetime import datetime
from decimal import Decimal

import requests
from sqlalchemy.orm import Session

from builder_wood.app.database import SessionLocal
from builder_wood.app import models
from builder_wood.app.config import settings

PAYSTACK_TRANSFER_URL = "https://api.paystack.co/transfer"


def settle_eligible_orders():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        eligible = (
            db.query(models.Order)
            .filter(
                models.Order.settlement_status == "eligible",
                models.Order.settlement_eligible_at <= now,
                models.Order.is_funds_released == False,
                models.Order.payment_status == "paid",
            )
            .all()
        )
        print(f"[settle] found {len(eligible)} eligible orders")

        for order in eligible:
            try:
                _transfer(db, order)
            except Exception as exc:
                print(f"[settle] order {order.order_id} failed: {exc}")
                continue
    finally:
        db.close()


def _transfer(db: Session, order: models.Order):
    import uuid
    seller = db.query(models.User).filter(models.User.user_id == order.seller_id).first()
    if not seller or not seller.paystack_recipient_code:
        print(f"[settle] order {order.order_id}: seller has no payout account")
        return

    reference = f"BW-PAYOUT-{order.order_id}-{uuid.uuid4().hex[:8]}".lower()
    amount_pesewas = int((Decimal(str(order.seller_payout_amount)) * 100).quantize(Decimal("1")))

    resp = requests.post(
        PAYSTACK_TRANSFER_URL,
        headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
        json={
            "source": "balance",
            "amount": amount_pesewas,
            "recipient": seller.paystack_recipient_code,
            "reason": f"Builder Wood auto-settlement for order #{order.order_id}",
            "reference": reference,
        },
        timeout=20,
    )
    data = resp.json()
    if not data.get("status"):
        order.settlement_status = "not_eligible"
        order.transfer_failed_reason = data.get("message", "unknown")
        db.commit()
        print(f"[settle] order {order.order_id}: transfer rejected: {data.get('message')}")
        return

    order.settlement_status = "transferring"
    order.transfer_reference = reference
    db.commit()
    print(f"[settle] order {order.order_id}: transfer initiated: {reference}")


if __name__ == "__main__":
    settle_eligible_orders()