
"""
Authentication module.

Provides:
- Password hashing and verification
- JWT creation and decoding
- Current user authentication
- Active user, seller, and admin authorization

Designed to work with the existing Builder Wood database models.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from builder_wood.app.config import settings
from builder_wood.app.database import get_db
from builder_wood.app import models


logger = logging.getLogger(__name__)

# Keep bcrypt to maintain compatibility with existing password hashes.
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login"
)

MAX_PASSWORD_BYTES = 72

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


# ============================================================
# Password hashing
# ============================================================

def _validate_password(password: str) -> None:
    if not isinstance(password, str):
        raise ValueError("Password must be a string.")

    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("Password must not exceed 72 bytes.")


def get_password_hash(password: str) -> str:
    """
    Hash a password using bcrypt.

    The byte-length restriction prevents bcrypt truncation.
    """
    _validate_password(password)
    return pwd_context.hash(password)


def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:
    """
    Verify a password without exposing hashing errors.
    """
    try:
        _validate_password(plain_password)

        if not hashed_password:
            return False

        return pwd_context.verify(
            plain_password,
            hashed_password,
        )

    except (ValueError, TypeError):
        return False


# ============================================================
# JWT
# ============================================================

def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token.
    """
    if not isinstance(data, dict):
        raise ValueError("Token data must be a dictionary.")

    if not settings.SECRET_KEY:
        raise RuntimeError("JWT secret key is not configured.")

    algorithm = settings.ALGORITHM

    if not algorithm:
        raise RuntimeError("JWT algorithm is not configured.")

    if expires_delta is not None and expires_delta.total_seconds() <= 0:
        raise ValueError("Token expiration must be positive.")

    now = datetime.now(timezone.utc)

    expires_at = now + (
        expires_delta
        or timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    )

    payload = data.copy()

    payload.update({
        "iat": now,
        "exp": expires_at,
    })

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=algorithm,
    )


def decode_token(token: str) -> dict:
    """
    Decode a JWT and return its payload.

    Raises ValueError when the token is invalid or expired.
    Used by protected endpoints and SSE authentication.
    """
    try:
        if not token or not settings.SECRET_KEY:
            raise ValueError("Invalid token.")

        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={
                "require_exp": True,
            },
        )

        if not isinstance(payload, dict):
            raise ValueError("Invalid token payload.")

        return payload

    except (JWTError, TypeError, ValueError) as exc:
        raise ValueError("Invalid or expired token.") from exc


# ============================================================
# Current user authentication
# ============================================================

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    """
    Authenticate the JWT and retrieve the active database user.

    The user's role is always read from the database, not trusted
    from the JWT payload.
    """
    try:
        payload = decode_token(token)

        user_id = payload.get("user_id")

        if type(user_id) is not int or user_id <= 0:
            raise CREDENTIALS_EXCEPTION

    except ValueError:
        raise CREDENTIALS_EXCEPTION

    user = (
        db.query(models.User)
        .filter(models.User.user_id == user_id)
        .first()
    )

    if user is None:
        raise CREDENTIALS_EXCEPTION

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is inactive.",
        )

    return user


# ============================================================
# Active user authorization
# ============================================================

def get_current_active_user(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    return current_user


# ============================================================
# Seller authorization
# ============================================================

def get_current_active_seller(
    current_user: models.User = Depends(get_current_user),
) -> models.User:

    if current_user.role not in ("seller", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seller access required.",
        )

    return current_user


# ============================================================
# Administrator authorization
# ============================================================

def get_current_admin(
    current_user: models.User = Depends(get_current_user),
) -> models.User:

    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    return current_user
