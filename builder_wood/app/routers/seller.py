from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import List, Optional

import cloudinary
import cloudinary.uploader
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
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from builder_wood.app import models
from builder_wood.app.auth import get_current_active_seller
from builder_wood.app.config import settings
from builder_wood.app.database import get_db

cloudinary.config(secure=True)

router = APIRouter(prefix="/seller", tags=["seller"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
READ_CHUNK = 256 * 1024
CLOUDINARY_TIMEOUT = 30

MAX_NAME_LEN = 200
MAX_STOCK = 1_000_000
LOW_STOCK_THRESHOLD = 10


def _validate_positive(name: str, value: Optional[float]) -> None:
    if value is None or not math.isfinite(value) or value <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{name} must be a positive number.",
        )


def _validate_name(field: str, value: Optional[str]) -> str:
    v = (value or "").strip()
    if not v:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} is required.",
        )
    if len(v) > MAX_NAME_LEN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be at most {MAX_NAME_LEN} characters.",
        )
    return v


def _validate_stock(stock: int) -> int:
    if stock < 0 or stock > MAX_STOCK:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Stock must be between 0 and {MAX_STOCK}.",
        )
    return stock


def _safe_ext(filename: Optional[str]) -> str:
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing file name.",
        )
    ext = Path(filename).suffix.lower()
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File has no extension; expected an image (.jpg/.png/.webp/.gif).",
        )
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported image type '{ext}'. Allowed: {sorted(ALLOWED_IMAGE_EXTS)}.",
        )
    return ext


async def _read_capped(upload: UploadFile, limit: int) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Image '{upload.filename}' exceeds {limit // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _upload_to_cloudinary(upload: UploadFile, wood_id: int, tag: str) -> str:
    ext = _safe_ext(upload.filename)
    content = await _read_capped(upload, MAX_IMAGE_BYTES)

    public_id = f"builder_wood/{wood_id}_{tag}_{uuid.uuid4().hex[:8]}"

    def _do_upload() -> dict:
        return cloudinary.uploader.upload(
            content,
            public_id=public_id,
            resource_type="image",
            timeout=CLOUDINARY_TIMEOUT,
        )

    try:
        result = await run_in_threadpool(_do_upload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Cloudinary upload failed: {exc}",
        )

    secure_url = result.get("secure_url")
    returned_pid = result.get("public_id") or public_id

    if not secure_url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cloudinary returned no secure_url",
        )

    _upload_to_cloudinary.last_public_id = returned_pid
    return secure_url


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
    if not main_image or not main_image.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A main image is required.",
        )

    local_name = _validate_name("Local name", local_name)
    trade_name = _validate_name("Trade name", trade_name)
    _validate_positive("Thickness", thickness)
    _validate_positive("Width", width)
    _validate_positive("Length", length)
    _validate_positive("Price", price)
    stock = _validate_stock(stock)

    category = (
        db.query(models.Category)
        .filter(models.Category.slug == category_slug)
        .first()
    )
    if not category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category '{category_slug}'.",
        )

    new_wood = models.Wood(
        local_name=local_name,
        trade_name=trade_name,
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

    uploaded_public_ids: List[str] = []

    async def _upload_and_track(upload: UploadFile, tag: str) -> str:
        url = await _upload_to_cloudinary(upload, new_wood.wood_id, tag)
        pid = getattr(_upload_to_cloudinary, "last_public_id", None)
        if pid:
            uploaded_public_ids.append(pid)
        return url

    try:
        main_url = await _upload_and_track(main_image, "main")
        db.add(
            models.WoodImage(
                wood_id=new_wood.wood_id,
                image_url=main_url,
                is_primary=True,
                display_order=0,
            )
        )

        order_counter = 1
        if dimension_image and dimension_image.filename:
            dim_url = await _upload_and_track(dimension_image, "dim")
            db.add(
                models.WoodImage(
                    wood_id=new_wood.wood_id,
                    image_url=dim_url,
                    is_primary=False,
                    display_order=order_counter,
                )
            )
            order_counter += 1

        if detail_images:
            for img in detail_images:
                if not img or not img.filename:
                    continue
                detail_url = await _upload_and_track(img, "detail")
                db.add(
                    models.WoodImage(
                        wood_id=new_wood.wood_id,
                        image_url=detail_url,
                        is_primary=False,
                        display_order=order_counter,
                    )
                )
                order_counter += 1

        db.commit()

    except Exception:
        db.rollback()
        for pid in uploaded_public_ids:
            try:
                await run_in_threadpool(
                    cloudinary.uploader.destroy, pid
                )
            except Exception:
                pass
        raise

    return {"status": "success", "wood_id": new_wood.wood_id}


@router.get("/low-stock")
def seller_low_stock(
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    rows = (
        db.query(models.Wood)
        .filter(
            models.Wood.seller_id == current_seller.user_id,
            models.Wood.deleted_at.is_(None),
            models.Wood.is_available.is_(True),
            models.Wood.stock_quantity <= LOW_STOCK_THRESHOLD,
        )
        .order_by(models.Wood.stock_quantity.asc(), models.Wood.wood_id.asc())
        .all()
    )
    return [
        {
            "wood_id": w.wood_id,
            "local_name": w.local_name,
            "trade_name": w.trade_name,
            "stock": int(w.stock_quantity or 0),
        }
        for w in rows
    ]


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

@router.get("/payout", response_class=HTMLResponse, include_in_schema=False)
def seller_payout_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="seller_payout.html",
        context={"shop_name": settings.APP_NAME},
    )