# builder_wood/app/main.py
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from builder_wood.app import cloudinary_config  # noqa: F401

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from builder_wood.app.auth import get_password_hash
from builder_wood.app.config import settings
from builder_wood.app.database import SessionLocal
from builder_wood.app.models import User
from builder_wood.app.routers import (
    admin,
    admin_wallet,
    auth,
    cart,
    orders,
    payments,
    seller,
    seller_payout,
    vehicles,
    wallet,
    woods,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    admin_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL")
    admin_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
    admin_name = os.getenv("BOOTSTRAP_ADMIN_NAME", "System Admin")
    admin_phone = os.getenv("BOOTSTRAP_ADMIN_PHONE")

    if admin_email and admin_password:
        try:
            db = SessionLocal()
            try:
                existing = db.query(User).filter(User.email == admin_email).first()
                if existing:
                    logger.info("Bootstrap admin already exists: %s", admin_email)
                else:
                    db.add(
                        User(
                            name=admin_name,
                            email=admin_email,
                            phone_number=admin_phone,
                            password_hash=get_password_hash(admin_password),
                            role="admin",
                            is_active=True,
                        )
                    )
                    db.commit()
                    logger.warning("Bootstrap admin created: %s", admin_email)
            finally:
                db.close()
        except Exception as e:
            logger.error("Bootstrap admin failed: %s", e)

    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="Timber marketplace",
    version="1.0.0",
    lifespan=lifespan,
)

_static_dir = BASE_DIR / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Routers — specific first
app.include_router(seller.router)
app.include_router(seller_payout.router)
app.include_router(wallet.router)
app.include_router(auth.router)
app.include_router(cart.router)
app.include_router(payments.router)
app.include_router(orders.router)
app.include_router(admin.router)
app.include_router(admin_wallet.router)
app.include_router(vehicles.router)
app.include_router(woods.router)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request, name="index.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="login.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="forgot_password.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="reset_password.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/checkout", response_class=HTMLResponse)
def checkout_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="checkout.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/my-orders", response_class=HTMLResponse)
def my_orders_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="my_orders.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/order/{order_id}", response_class=HTMLResponse)
def order_confirmation(request: Request, order_id: int):
    return templates.TemplateResponse(
        request=request, name="order_confirmation.html",
        context={"shop_name": settings.APP_NAME, "order_id": order_id},
    )


@app.get("/product/{wood_id}", response_class=HTMLResponse)
def product_detail_page(request: Request, wood_id: int):
    return templates.TemplateResponse(
        request=request, name="product_detail.html",
        context={"shop_name": settings.APP_NAME, "wood_id": wood_id},
    )


@app.get("/seller/wallet-page", response_class=HTMLResponse, include_in_schema=False)
def seller_wallet_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="seller_wallet.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/health")
def health():
    return {"status": "ok", "application": settings.APP_NAME}