"""
In-app messaging between buyers and sellers.

Endpoints:
- POST /conversations/start          — get or create conversation
- GET  /conversations                — list user's conversations
- GET  /conversations/{id}           — get conversation with messages
- POST /conversations/{id}/messages  — send a message (contact-info filtered)
- POST /conversations/{id}/mark-read — mark all messages as read
- GET  /conversations/unread-count   — total unread
- GET  /conversations/{id}/stream    — Server-Sent Events real-time stream

Delivery model (real-time):
- A per-conversation asyncio.Condition ("event bus") lives in this process.
- When a message is committed, the sender's request calls notify_conversation()
  which wakes every SSE stream waiting on that conversation. The receiver gets
  the message in the same event-loop tick — no polling latency, no refresh.
- SSE also polls every FALLBACK_POLL_SECONDS (1s) in case the sender was
  handled by a different worker (Render WEB_CONCURRENCY>1). Same-process
  delivery is instant; cross-worker delivery is <= 1s.
- Each SSE stream opens a short-lived DB session per wake/poll so a pooled
  connection is never held for the lifetime of the stream.

Rules:
- All buyer/seller communication goes through here.
- Messages containing contact info are stored flagged (invisible to both
  parties), counted as violations, and escalate to a temporary or permanent ban.
- Flagged messages are never shown to participants.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, case
from sqlalchemy.orm import Session, joinedload

from builder_wood.app.database import get_db, SessionLocal
from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_user, decode_token
from builder_wood.app.contact_filter import detect_contact_info

logger = logging.getLogger(__name__)

router = APIRouter(tags=["messaging"])

# Cross-worker safety net: even without an in-process notification, the stream
# will re-check the DB at most this often.
FALLBACK_POLL_SECONDS = 1.0

# How long a single SSE stream can sit idle before sending a ping comment.
# Keeps proxies (Render) from tearing down an idle connection.
HEARTBEAT_SECONDS = 15.0


# ============================================================
# In-process event bus (per conversation)
# ============================================================
# conversation_id -> asyncio.Condition
#
# All SSE streams for a conversation wait() on the same Condition. When a
# message is committed by send_message(), the endpoint calls notify_conversation
# which acquires the same Condition and notify_all()s. Every waiting stream
# wakes, re-queries for messages newer than its last_message_id, and yields.
#
# Because asyncio is single-threaded per worker, this is race-free within one
# process: the wait is re-armed before the next await.
_conversation_conditions: Dict[int, asyncio.Condition] = {}
_conditions_lock: asyncio.Lock = asyncio.Lock()


async def _get_condition(conversation_id: int) -> asyncio.Condition:
    """Return (and lazily create) the asyncio.Condition for a conversation."""
    async with _conditions_lock:
        cond = _conversation_conditions.get(conversation_id)
        if cond is None:
            cond = asyncio.Condition()
            _conversation_conditions[conversation_id] = cond
        return cond


async def _notify_conversation(conversation_id: int) -> None:
    """
    Wake every SSE stream currently waiting on this conversation.

    Called synchronously (via run_coroutine) right after a message is
    committed. If no one is listening, this is a no-op.
    """
    cond = await _get_condition(conversation_id)
    async with cond:
        cond.notify_all()


# ============================================================
# POST /conversations/start
# ============================================================
@router.post("/conversations/start", response_model=schemas.ConversationDetailOut)
def start_conversation(
    payload: schemas.StartConversationIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.user_id == payload.seller_id:
        raise HTTPException(400, "Cannot message yourself")

    if current_user.role not in ("customer", "admin"):
        raise HTTPException(403, "Only buyers can initiate product conversations")

    seller = db.query(models.User).filter(
        models.User.user_id == payload.seller_id,
        models.User.role == "seller",
        models.User.is_active.is_(True),
    ).first()
    if not seller:
        raise HTTPException(404, "Seller not found or inactive")

    # Resolve context: an order (if given) pins the conversation to that order.
    context_wood_id = payload.wood_id
    if payload.order_id is not None:
        order = db.query(models.Order).filter(
            models.Order.order_id == payload.order_id,
            models.Order.customer_id == current_user.user_id,
        ).first()
        if not order:
            raise HTTPException(404, "Order not found")
        if order.seller_id != payload.seller_id:
            raise HTTPException(400, "Seller does not match this order")
        if context_wood_id is None and getattr(order, "items", None):
            context_wood_id = order.items[0].wood_id

    conv = db.query(models.Conversation).filter(
        models.Conversation.buyer_id == current_user.user_id,
        models.Conversation.seller_id == payload.seller_id,
        models.Conversation.channel == "product",
    ).first()

    if conv:
        if context_wood_id and conv.last_context_wood_id != context_wood_id:
            conv.last_context_wood_id = context_wood_id
            db.commit()
    else:
        conv = models.Conversation(
            buyer_id=current_user.user_id,
            seller_id=payload.seller_id,
            channel="product",
            wood_id=context_wood_id,
            last_context_wood_id=context_wood_id,
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)

    return _build_conversation_detail(conv, current_user, db)


# ============================================================
# GET /conversations
# ============================================================
@router.get("/conversations", response_model=List[schemas.ConversationSummaryOut])
def list_conversations(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    convs = (
        db.query(models.Conversation)
        .filter(
            (models.Conversation.buyer_id == current_user.user_id)
            | (models.Conversation.seller_id == current_user.user_id),
            models.Conversation.channel == "product",
        )
        .order_by(
            models.Conversation.last_message_at.is_(None),
            models.Conversation.last_message_at.desc(),
        )
        .all()
    )

    if not convs:
        return []

    conv_ids = [c.conversation_id for c in convs]

    other_ids = set()
    for c in convs:
        other_ids.add(c.seller_id if c.buyer_id == current_user.user_id else c.buyer_id)

    users_map = {
        u.user_id: u
        for u in db.query(models.User).filter(models.User.user_id.in_(other_ids)).all()
    }

    stats_rows = (
        db.query(
            models.Message.conversation_id.label("conv_id"),
            func.max(models.Message.message_id).label("last_msg_id"),
            func.sum(case(
                (
                    (models.Message.sender_id != current_user.user_id)
                    & (models.Message.is_read.is_(False))
                    & (models.Message.is_flagged.is_(False)),
                    1,
                ),
                else_=0,
            )).label("unread_count"),
        )
        .filter(models.Message.conversation_id.in_(conv_ids))
        .group_by(models.Message.conversation_id)
        .all()
    )

    last_msg_ids = [r.last_msg_id for r in stats_rows if r.last_msg_id]
    last_msgs = {}
    if last_msg_ids:
        last_msgs = {
            m.message_id: m
            for m in db.query(models.Message).filter(models.Message.message_id.in_(last_msg_ids)).all()
        }

    stats_map = {
        r.conv_id: {"last_msg_id": r.last_msg_id, "unread": int(r.unread_count or 0)}
        for r in stats_rows
    }

    result = []
    for conv in convs:
        other_id = conv.seller_id if conv.buyer_id == current_user.user_id else conv.buyer_id
        other = users_map.get(other_id)
        other_role = "seller" if conv.buyer_id == current_user.user_id else "buyer"

        stats = stats_map.get(conv.conversation_id, {"last_msg_id": None, "unread": 0})
        last_msg = last_msgs.get(stats["last_msg_id"]) if stats["last_msg_id"] else None

        preview = None
        if last_msg and last_msg.message_text and not last_msg.is_flagged:
            preview = last_msg.message_text[:80]

        result.append({
            "conversation_id": conv.conversation_id,
            "other_party_name": other.name if other else "Unknown",
            "other_party_role": other_role,
            "last_message_preview": preview,
            "last_message_at": conv.last_message_at,
            "unread_count": stats["unread"],
        })

    return result


# ============================================================
# GET /conversations/unread-count
# ============================================================
@router.get("/conversations/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    count = (
        db.query(func.count(models.Message.message_id))
        .join(models.Conversation, models.Message.conversation_id == models.Conversation.conversation_id)
        .filter(
            (models.Conversation.buyer_id == current_user.user_id)
            | (models.Conversation.seller_id == current_user.user_id),
            models.Message.sender_id != current_user.user_id,
            models.Message.is_read.is_(False),
            models.Message.is_flagged.is_(False),
        )
        .scalar()
    )
    return {"unread_count": count or 0}


# ============================================================
# GET /conversations/{id}
# ============================================================
@router.get("/conversations/{conversation_id}", response_model=schemas.ConversationDetailOut)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conv = db.query(models.Conversation).filter(
        models.Conversation.conversation_id == conversation_id
    ).first()

    if not conv:
        raise HTTPException(404, "Conversation not found")

    if current_user.user_id not in (conv.buyer_id, conv.seller_id):
        raise HTTPException(403, "Not a participant in this conversation")

    db.query(models.Message).filter(
        models.Message.conversation_id == conversation_id,
        models.Message.sender_id != current_user.user_id,
        models.Message.is_read.is_(False),
        models.Message.is_flagged.is_(False),
    ).update({models.Message.is_read: True}, synchronize_session=False)
    db.commit()

    return _build_conversation_detail(conv, current_user, db)


# ============================================================
# GET /conversations/{id}/stream — REAL-TIME SSE
# ============================================================
@router.get("/conversations/{conversation_id}/stream")
async def stream_conversation(
    conversation_id: int,
    token: str = None,
):
    """
    Server-Sent Events stream.

    Delivers new messages to the connected client in real time:
      - Same worker that handled the POST → the Condition is notified and
        the stream yields on the same event-loop tick (sub-100ms).
      - Different worker (WEB_CONCURRENCY > 1) → the 1s fallback poll picks
        it up. Still well under the 3s SLA.

    Each iteration opens a short-lived DB session so pooled connections are
    never held for the lifetime of the stream.
    """
    if not token:
        raise HTTPException(401, "Token required")

    try:
        payload = decode_token(token)
        user_id = payload.get("user_id")
    except Exception:
        raise HTTPException(401, "Invalid token")

    if not user_id:
        raise HTTPException(401, "Invalid token")

    # Validate access with a short-lived session that closes immediately.
    with SessionLocal() as db:
        conv = db.query(models.Conversation).filter(
            models.Conversation.conversation_id == conversation_id
        ).first()

        if not conv:
            raise HTTPException(404, "Conversation not found")

        if user_id not in (conv.buyer_id, conv.seller_id):
            raise HTTPException(403, "Not a participant")

        latest = db.query(func.max(models.Message.message_id)).filter(
            models.Message.conversation_id == conversation_id
        ).scalar()

    async def event_generator():
        last_message_id = latest or 0
        last_ping = asyncio.get_event_loop().time()

        # Tell the client the stream is live so it can hide a spinner.
        yield "event: open\ndata: {}\n\n"

        condition = await _get_condition(conversation_id)

        while True:
            # Wait for either a notification from send_message() or the
            # fallback poll timeout, whichever comes first.
            notified = False
            try:
                async with condition:
                    try:
                        await asyncio.wait_for(
                            condition.wait(),
                            timeout=FALLBACK_POLL_SECONDS,
                        )
                        notified = True
                    except asyncio.TimeoutError:
                        notified = False
            except asyncio.CancelledError:
                # Client disconnected while waiting.
                return

            # Fetch anything newer than what we've already yielded.
            try:
                with SessionLocal() as db:
                    new_msgs = (
                        db.query(models.Message)
                        .options(joinedload(models.Message.sender))
                        .filter(
                            models.Message.conversation_id == conversation_id,
                            models.Message.message_id > last_message_id,
                            models.Message.is_flagged.is_(False),
                        )
                        .order_by(models.Message.message_id.asc())
                        .all()
                    )
                    payloads = [
                        {
                            "message_id": m.message_id,
                            "conversation_id": m.conversation_id,
                            "sender_id": m.sender_id,
                            "sender_name": m.sender.name if m.sender else "Unknown",
                            "message_text": m.message_text,
                            "is_read": m.is_read,
                            "is_flagged": m.is_flagged,
                            "created_at": m.created_at.isoformat() if m.created_at else None,
                        }
                        for m in new_msgs
                    ]
            except Exception:
                logger.exception("SSE poll failed for conversation %s", conversation_id)
                payloads = []

            for data in payloads:
                last_message_id = data["message_id"]
                yield f"event: message\ndata: {json.dumps(data)}\n\n"

            # Send a heartbeat comment if we've been idle for a while. This
            # keeps proxies from closing the connection. Only send if we did
            # not just deliver real data (no point pinging immediately after).
            if not payloads:
                now = asyncio.get_event_loop().time()
                if now - last_ping >= HEARTBEAT_SECONDS:
                    last_ping = now
                    yield ": ping\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# POST /conversations/{id}/messages
# ============================================================
@router.post("/conversations/{conversation_id}/messages", response_model=schemas.MessageOut)
async def send_message(
    conversation_id: int,
    payload: schemas.MessageCreateIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)

    banned_until = current_user.messaging_banned_until
    if banned_until is not None:
        if banned_until.tzinfo is None:
            banned_until = banned_until.replace(tzinfo=timezone.utc)
        if banned_until > now:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                {
                    "code": "messaging_banned",
                    "until": banned_until.isoformat(),
                    "message": (
                        f"Messaging suspended until {banned_until.isoformat()}. "
                        "Reason: repeated contact info sharing."
                    ),
                },
            )

    if not current_user.is_active:
        raise HTTPException(403, "Account inactive")

    conv = db.query(models.Conversation).filter(
        models.Conversation.conversation_id == conversation_id
    ).first()

    if not conv:
        raise HTTPException(404, "Conversation not found")

    if current_user.user_id not in (conv.buyer_id, conv.seller_id):
        raise HTTPException(403, "Not a participant")

    text = (payload.message_text or "").strip()
    if not text:
        raise HTTPException(400, "Message cannot be empty.")

    if conv.channel == "product":
        detection = detect_contact_info(text)
        if detection["detected"]:
            current_user.messaging_violations = (current_user.messaging_violations or 0) + 1
            violations = current_user.messaging_violations

            flagged = models.Message(
                conversation_id=conversation_id,
                sender_id=current_user.user_id,
                message_text=text,
                is_read=True,
                is_flagged=True,
                flag_reason=detection["type"],
            )
            db.add(flagged)

            ban_msg = ""
            if violations == 1:
                ban_msg = "This is your first warning."
            elif violations == 2:
                current_user.messaging_banned_until = now + timedelta(hours=24)
                ban_msg = "Your messaging is suspended for 24 hours."
            elif violations == 3:
                current_user.messaging_banned_until = now + timedelta(days=7)
                ban_msg = "Your messaging is suspended for 7 days."
            elif violations >= 4:
                current_user.is_active = False
                ban_msg = "Your account has been suspended."

            db.commit()

            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                {
                    "code": "contact_info_blocked",
                    "type": detection["type"],
                    "violations": violations,
                    "message": (
                        f"Your message contains contact information ({detection['type']}). "
                        f"Please keep all communication on Builder Wood. {ban_msg}"
                    ).strip(),
                },
            )

    msg = models.Message(
        conversation_id=conversation_id,
        sender_id=current_user.user_id,
        message_text=text,
        is_read=False,
        is_flagged=False,
    )
    db.add(msg)
    conv.last_message_at = now
    db.commit()
    db.refresh(msg)

    # Wake every SSE stream for this conversation in the same worker.
    # If the receiver is on a different worker, their 1s fallback poll
    # picks it up. Either way, delivery is < 3s.
    await _notify_conversation(conversation_id)

    return {
        "message_id": msg.message_id,
        "conversation_id": msg.conversation_id,
        "sender_id": msg.sender_id,
        "sender_name": current_user.name,
        "message_text": msg.message_text,
        "is_read": msg.is_read,
        "is_flagged": msg.is_flagged,
        "created_at": msg.created_at,
    }


# ============================================================
# POST /conversations/{id}/mark-read
# ============================================================
@router.post("/conversations/{conversation_id}/mark-read")
def mark_read(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conv = db.query(models.Conversation).filter(
        models.Conversation.conversation_id == conversation_id
    ).first()

    if not conv:
        raise HTTPException(404, "Conversation not found")

    if current_user.user_id not in (conv.buyer_id, conv.seller_id):
        raise HTTPException(403, "Not a participant")

    updated = db.query(models.Message).filter(
        models.Message.conversation_id == conversation_id,
        models.Message.sender_id != current_user.user_id,
        models.Message.is_read.is_(False),
        models.Message.is_flagged.is_(False),
    ).update({models.Message.is_read: True}, synchronize_session=False)
    db.commit()

    return {"updated": updated}


# ============================================================
# Internal helper
# ============================================================
def _build_conversation_detail(conv: models.Conversation, current_user: models.User, db: Session):
    other_id = conv.seller_id if conv.buyer_id == current_user.user_id else conv.buyer_id
    other = db.query(models.User).filter(models.User.user_id == other_id).first()
    other_role = "seller" if conv.buyer_id == current_user.user_id else "buyer"

    wood_context = None
    wood_id = conv.last_context_wood_id or conv.wood_id
    if wood_id:
        wood = db.query(models.Wood).filter(models.Wood.wood_id == wood_id).first()
        if wood:
            wood_context = {
                "wood_id": wood.wood_id,
                "local_name": wood.local_name,
                "size_label": wood.size_label,
                "price": float(wood.price_per_piece),
            }

    # Newest 200 messages, returned oldest-to-newest for display.
    newest = (
        db.query(models.Message)
        .options(joinedload(models.Message.sender))
        .filter(
            models.Message.conversation_id == conv.conversation_id,
            models.Message.is_flagged.is_(False),
        )
        .order_by(models.Message.created_at.desc())
        .limit(200)
        .all()
    )
    messages = list(reversed(newest))

    return {
        "conversation_id": conv.conversation_id,
        "buyer_id": conv.buyer_id,
        "seller_id": conv.seller_id,
        "channel": conv.channel,
        "other_party_id": other_id,
        "other_party_name": other.name if other else "Unknown",
        "other_party_role": other_role,
        "wood_context": wood_context,
        "messages": [
            {
                "message_id": m.message_id,
                "conversation_id": m.conversation_id,
                "sender_id": m.sender_id,
                "sender_name": m.sender.name if m.sender else "Unknown",
                "message_text": m.message_text,
                "is_read": m.is_read,
                "is_flagged": m.is_flagged,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }