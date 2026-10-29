import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from builder_wood.app.config import settings
from builder_wood.app.database import engine, SessionLocal
from builder_wood.app.models import (
    User,
    Category,
    Vehicle,
    Wood,
    WoodImage,
    Order,
    OrderItem,
    Conversation,
    Message,
    SellerVehicle,
    OrderStatusHistory,
    CartItem,
    PaymentTransaction,
)
from builder_wood.app.routers import woods, orders, seller, auth, admin, cart, payments
from builder_wood.app.auth import get_password_hash

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ------------------------------------------------------------------
    # One-time bootstrap admin.
    #
    # Runs ONLY when BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD
    # are set in the environment. Otherwise it does nothing — so the
    # normal (production) state after the first deploy is "no admin is
    # ever created automatically".
    #
    # Lifecycle:
    #   1. Add the four BOOTSTRAP_ADMIN_* env vars in Render.
    #   2. Deploy. Admin is created once. Logs say so.
    #   3. Log in, confirm.
    #   4. Delete the four env vars from Render.
    #   5. Redeploy. Logs say "No bootstrap admin configured".
    # ------------------------------------------------------------------
    admin_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL")
    admin_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
    admin_name = os.getenv("BOOTSTRAP_ADMIN_NAME", "System Admin")
    admin_phone = os.getenv("BOOTSTRAP_ADMIN_PHONE")  # optional; NULL is fine

    if admin_email and admin_password:
        try:
            db = SessionLocal()
            try:
                existing = (
                    db.query(User).filter(User.email == admin_email).first()
                )
                if existing:
                    logger.info(
                        "Bootstrap admin already exists: %s", admin_email
                    )
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
                    logger.warning(
                        "Bootstrap admin created: %s", admin_email
                    )
                    logger.warning(
                        "REMOVE BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD, "
                        "BOOTSTRAP_ADMIN_NAME, BOOTSTRAP_ADMIN_PHONE from Render now."
                    )
            finally:
                db.close()
        except Exception as e:
            # Never let bootstrap failures crash the app.
            logger.error("Bootstrap admin failed: %s", e)
    else:
        logger.info(
            "No bootstrap admin configured "
            "(BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD unset)."
        )

    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="Timber marketplace",
    version="0.7.0",
    lifespan=lifespan,
)

# ------------------------------------------------------------------
# Static files — create the directory if missing so the container
# doesn't crash at import time when static/ isn't committed.
# ------------------------------------------------------------------
_static_dir = BASE_DIR / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(woods.router)
app.include_router(orders.router)
app.include_router(seller.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(cart.router)
app.include_router(payments.router)


# ------------------------------------------------------------------
# PAGE ROUTES
# ------------------------------------------------------------------
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
        request=request,
        name="order_confirmation.html",
        context={"shop_name": settings.APP_NAME, "order_id": order_id},
    )


@app.get("/product/{wood_id}", response_class=HTMLResponse)
def product_detail_page(request: Request, wood_id: int):
    return templates.TemplateResponse(
        request=request,
        name="product_detail.html",
        context={"shop_name": settings.APP_NAME, "wood_id": wood_id},
    )


@app.get("/seller/upload", response_class=HTMLResponse)
def seller_upload_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="seller_upload.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/seller/products", response_class=HTMLResponse)
def seller_products_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="seller_products.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/seller/orders", response_class=HTMLResponse)
def seller_orders_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="seller_orders.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="admin_dashboard.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/admin/payouts", response_class=HTMLResponse)
def admin_payouts_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="admin_payouts.html", context={"shop_name": settings.APP_NAME}
    )


@app.get("/health")
def health():
    return {"status": "ok", "application": settings.APP_NAME}