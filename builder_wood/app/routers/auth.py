import logging
import secrets
import requests
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field, field_validator

from builder_wood.app.database import get_db
from builder_wood.app import models
from builder_wood.app.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
)
from builder_wood.app.config import settings
from builder_wood.app.email_service import send_password_reset_email
from builder_wood.app.schemas import ForgotPasswordIn, ResetPasswordIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

PAYSTACK_RECIPIENT_URL = "https://api.paystack.co/transferrecipient"


class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=6)
    phone_number: str
    role: Literal["customer", "seller"] = "customer"
    company_name: Optional[str] = None
    location: Optional[str] = None

    # Payout fields (seller only)
    payout_type: Optional[Literal["mobile_money", "bank"]] = None
    payout_network: Optional[str] = None       # for mobile_money
    payout_bank_code: Optional[str] = None     # for bank
    payout_account_number: Optional[str] = None
    payout_account_name: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 bytes.")
        return value


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    company_name: Optional[str] = None
    location: Optional[str] = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6)

    @field_validator("new_password")
    @classmethod
    def validate_password_length(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 bytes.")
        return value


def _register_payout_recipient(
    payout_type: str,
    payout_network: Optional[str],
    payout_bank_code: Optional[str],
    account_number: str,
    account_name: str,
) -> Optional[dict]:
    """
    Register a Paystack recipient. Returns dict with recipient_code or None on failure.
    """
    if not settings.PAYSTACK_SECRET_KEY:
        return None

    if payout_type == "mobile_money":
        if not payout_network:
            return None
        body = {
            "type": "mobile_money",
            "name": account_name,
            "account_number": account_number,
            "bank_code": payout_network,
            "currency": "GHS",
        }
    elif payout_type == "bank":
        if not payout_bank_code:
            return None
        body = {
            "type": "ghipss",
            "name": account_name,
            "account_number": account_number,
            "bank_code": payout_bank_code,
            "currency": "GHS",
        }
    else:
        return None

    try:
        resp = requests.post(
            PAYSTACK_RECIPIENT_URL,
            headers={
                "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=20,
        )
        data = resp.json()
        if data.get("status") and data.get("data", {}).get("recipient_code"):
            return {
                "recipient_code": data["data"]["recipient_code"],
                "display_name": payout_network or payout_bank_code or "",
            }
        logger.warning(f"Paystack recipient creation rejected: {data.get('message')}")
        return None
    except Exception as e:
        logger.error(f"Paystack recipient creation failed: {e}")
        return None


@router.post("/register")
def register(user: UserRegister, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.email == user.email).first():
        raise HTTPException(
            status_code=400,
            detail="Email already registered",
        )

    is_active = user.role == "customer"

    new_user = models.User(
        name=user.name,
        email=user.email,
        phone_number=user.phone_number,
        password_hash=get_password_hash(user.password),
        role=user.role,
        company_name=user.company_name,
        location=user.location,
        is_active=is_active,
    )

    # If seller with payout details, register recipient with Paystack
    payout_registered = False
    payout_error = None

    if user.role == "seller" and user.payout_type and user.payout_account_number and user.payout_account_name:
        recipient = _register_payout_recipient(
            payout_type=user.payout_type,
            payout_network=user.payout_network,
            payout_bank_code=user.payout_bank_code,
            account_number=user.payout_account_number,
            account_name=user.payout_account_name,
        )
        if recipient:
            new_user.paystack_recipient_code = recipient["recipient_code"]
            new_user.payout_bank_name = recipient["display_name"]
            new_user.payout_account_number = user.payout_account_number
            new_user.payout_account_name = user.payout_account_name
            payout_registered = True
        else:
            payout_error = "Could not register payout account with Paystack. You can add it after approval."

    db.add(new_user)
    db.commit()

    msg = (
        "Account created. You can now login."
        if is_active
        else "Account created. Waiting for Admin approval."
    )

    return {
        "message": msg,
        "payout_registered": payout_registered,
        "payout_error": payout_error,
    }


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(
        models.User.email == form_data.username
    ).first()

    if not user or not verify_password(
        form_data.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending approval or has been deactivated.",
        )

    access_token = create_access_token(
        data={"user_id": user.user_id, "role": user.role}
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "name": user.name,
        "user_id": user.user_id,
    }


@router.get("/me")
def read_users_me(current_user: models.User = Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "name": current_user.name,
        "email": current_user.email,
        "phone_number": current_user.phone_number,
        "address": current_user.address,
        "company_name": current_user.company_name,
        "location": current_user.location,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "has_payout_account": bool(current_user.paystack_recipient_code),
    }


@router.patch("/me")
def update_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if payload.name is not None:
        current_user.name = payload.name
    if payload.phone_number is not None:
        current_user.phone_number = payload.phone_number
    if payload.address is not None:
        current_user.address = payload.address
    if payload.company_name is not None:
        current_user.company_name = payload.company_name
    if payload.location is not None:
        current_user.location = payload.location

    db.commit()
    db.refresh(current_user)

    return {
        "user_id": current_user.user_id,
        "name": current_user.name,
        "email": current_user.email,
        "phone_number": current_user.phone_number,
        "address": current_user.address,
        "company_name": current_user.company_name,
        "location": current_user.location,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "has_payout_account": bool(current_user.paystack_recipient_code),
    }


@router.post("/change-password")
def change_password(
    payload: PasswordChange,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(400, "Current password is incorrect")

    current_user.password_hash = get_password_hash(payload.new_password)
    db.commit()

    return {"message": "Password changed successfully"}


@router.post("/forgot-password")
def forgot_password(
    payload: ForgotPasswordIn,
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.email == payload.email).first()

    response = {"message": "If that email is registered, a reset link has been sent."}

    if not user:
        return response

    db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.user_id == user.user_id,
        models.PasswordResetToken.used.is_(False),
    ).delete()

    token = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)

    db.add(
        models.PasswordResetToken(
            user_id=user.user_id,
            token=token,
            expires_at=expires,
            used=False,
        )
    )
    db.commit()

    base_url = str(settings.BASE_URL).rstrip("/")
    reset_url = f"{base_url}/reset-password?token={token}"

    sent = send_password_reset_email(user.email, reset_url)

    if not sent:
        logger.warning(f"[DEV MODE] Password reset URL for {user.email}: {reset_url}")
        if getattr(settings, "DEBUG", False):
            response["dev_reset_url"] = reset_url

    return response


@router.post("/reset-password")
def reset_password(
    payload: ResetPasswordIn,
    db: Session = Depends(get_db),
):
    record = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token == payload.token,
        models.PasswordResetToken.used.is_(False),
    ).first()

    if not record:
        raise HTTPException(400, "Invalid or already-used reset link")

    if record.expires_at < datetime.now(timezone.utc):
        raise HTTPException(400, "This reset link has expired. Please request a new one.")

    user = db.query(models.User).filter(models.User.user_id == record.user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    user.password_hash = get_password_hash(payload.new_password)
    record.used = True
    db.commit()

    return {"message": "Password reset successfully. You can now log in."}