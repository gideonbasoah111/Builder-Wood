from typing import Literal

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
    get_current_user
)

router = APIRouter(prefix="/auth", tags=["auth"])


class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=6)
    phone_number: str
    role: Literal["customer", "seller"] = "customer"

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 bytes.")
        return value


@router.post("/register")
def register(user: UserRegister, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.email == user.email).first():
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )

    is_active = user.role == "customer"

    new_user = models.User(
        name=user.name,
        email=user.email,
        phone_number=user.phone_number,
        password_hash=get_password_hash(user.password),
        role=user.role,
        is_active=is_active
    )

    db.add(new_user)
    db.commit()

    msg = (
        "Account created. You can now login."
        if is_active
        else "Account created. Waiting for Admin approval."
    )

    return {"message": msg}


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(
        models.User.email == form_data.username
    ).first()

    if not user or not verify_password(
        form_data.password,
        user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending approval or has been deactivated."
        )

    access_token = create_access_token(
        data={"user_id": user.user_id, "role": user.role}
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "name": user.name,
        "user_id": user.user_id
    }


@router.get("/me")
def read_users_me(
    current_user: models.User = Depends(get_current_user)
):
    return {
        "user_id": current_user.user_id,
        "name": current_user.name,
        "email": current_user.email,
        "phone_number": current_user.phone_number,
        "address": current_user.address,
        "role": current_user.role,
        "is_active": current_user.is_active
    }