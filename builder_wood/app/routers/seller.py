# builder_wood/app/routers/seller.py
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from builder_wood.app import models
from builder_wood.app.auth import get_current_active_seller
from builder_wood.app.config import settings
from builder_wood.app.database import get_db

router = APIRouter(prefix="/seller", tags=["seller"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

UPLOAD_DIR = BASE_DIR / "static" / "uploads" / "products"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB per file


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _validate_positive(name: str, value: float) -> None:
    if value is None or value <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{name} must be a positive number.",
        )


def _safe_ext(filename: Optional[str]) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported image type '{ext}'. Allowed: {sorted(ALLOWED_IMAGE_EXTS)}.",
        )
    return ext


def _save_upload(upload: UploadFile, wood_id: int, tag: str) -> str:
    ext = _safe_ext(upload.filename)
    unique_filename = f"{wood_id}_{tag}_{uuid.uuid4().hex[:8]}{ext}"
    dest = UPLOAD_DIR / unique_filename

    written = 0
    try:
        with dest.open("wb") as buffer:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_IMAGE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"Image '{upload.filename}' exceeds "
                            f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB."
                        ),
                    )
                buffer.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        upload.file.close()

    return f"/static/uploads/products/{unique_filename}"


# ---------------------------------------------------------------------------
# POST /seller/add-wood
# ---------------------------------------------------------------------------
@router.post(
    "/add-wood",
    status_code=status.HTTP_201_CREATED,
    summary="Publish a new wood listing",
)
async def add_wood(
    local_name: str = Form(...),
    trade_name: str = Form(...),
    thickness: float = Form(...),
    width: float = Form(...),
    length: float = Form(...),
    price: float = Form(...),
    stock: int = Form(...),
    category_slug: str = Form(...),
    product_form: str = Form("Sawn Timber"),
    is_treated: bool = Form(False),
    main_image: UploadFile = File(...),
    dimension_image: Optional[UploadFile] = File(None),
    detail_images: Optional[List[UploadFile]] = File(None),
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    if not main_image.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A main image is required.",
        )

    _validate_positive("Thickness", thickness)
    _validate_positive("Width", width)
    _validate_positive("Length", length)
    _validate_positive("Price", price)
    if stock < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stock cannot be negative.",
        )

    category = (
        db.query(models.Category)
        .filter(models.Category.slug == category_slug)
        .first()
    )
    if not category:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown category '{category_slug}'.",
        )

    new_wood = models.Wood(
        local_name=local_name.strip(),
        trade_name=trade_name.strip(),
        thickness_in=thickness,
        width_in=width,
        length_ft=length,
        price_per_piece=price,
        stock_quantity=stock,
        category_id=category.category_id,
        seller_id=current_seller.user_id,
        product_form=product_form,
        is_treated=is_treated,
        currency="GHS",
        is_available=True,
    )
    db.add(new_wood)
    db.flush()

    written_files: List[Path] = []

    try:
        url = _save_upload(main_image, new_wood.wood_id, "main")
        written_files.append(UPLOAD_DIR / Path(url).name)
        db.add(
            models.WoodImage(
                wood_id=new_wood.wood_id,
                image_url=url,
                is_primary=True,
                display_order=0,
            )
        )

        order_counter = 1
        if dimension_image and dimension_image.filename:
            url = _save_upload(dimension_image, new_wood.wood_id, "dim")
            written_files.append(UPLOAD_DIR / Path(url).name)
            db.add(
                models.WoodImage(
                    wood_id=new_wood.wood_id,
                    image_url=url,
                    is_primary=False,
                    display_order=order_counter,
                )
            )
            order_counter += 1

        if detail_images:
            for img in detail_images:
                if not img.filename:
                    continue
                url = _save_upload(img, new_wood.wood_id, "detail")
                written_files.append(UPLOAD_DIR / Path(url).name)
                db.add(
                    models.WoodImage(
                        wood_id=new_wood.wood_id,
                        image_url=url,
                        is_primary=False,
                        display_order=order_counter,
                    )
                )
                order_counter += 1

        db.commit()
    except Exception:
        db.rollback()
        for p in written_files:
            p.unlink(missing_ok=True)
        raise

    return {"status": "success", "wood_id": new_wood.wood_id}


# ---------------------------------------------------------------------------
# Seller dashboard pages (GET) — same router, same prefix
# ---------------------------------------------------------------------------
@router.get("/upload", response_class=HTMLResponse, include_in_schema=False)
def seller_upload_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="seller_upload.html",
        context={"shop_name": settings.APP_NAME},
    )


@router.get("/products", response_class=HTMLResponse, include_in_schema=False)
def seller_products_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="seller_products.html",
        context={"shop_name": settings.APP_NAME},
    )


@router.get("/orders", response_class=HTMLResponse, include_in_schema=False)
def seller_orders_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="seller_orders.html",
        context={"shop_name": settings.APP_NAME},
    )