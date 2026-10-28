import logging
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
    # Never crash the whole app if the DB is momentarily unreachable
    # at boot. Log it and continue; the admin user can be created later.
    try:
        db = SessionLocal()
        try:
            admin_email = "admin@builderwood.com"
            if not db.query(User).filter(User.email == admin_email).first():
                db.add(
                    User(
                        name="System Admin",
                        email=admin_email,
                        phone_number="+233000000000",
                        password_hash=get_password_hash("admin123"),
                        role="admin",
                        is_active=True,
                    )
                )
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Startup admin check failed: {e}")

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