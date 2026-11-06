from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func as sqlfunc, or_
from sqlalchemy.orm import Session, joinedload

from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_active_seller
from builder_wood.app.database import get_db

try:
    from builder_wood.app.cloudinary_client import (
        upload_image_to_cloudinary,
        delete_cloudinary_image,
    )
except ImportError:  # Cloudinary is optional until image features are used.
    upload_image_to_cloudinary = None
    delete_cloudinary_image = None


router = APIRouter(prefix="/woods", tags=["woods"])

CLOUDINARY_PREFIX = "https://res.cloudinary.com/"
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _display_name(wood: models.Wood) -> str:
    parts = [
        value.strip()
        for value in (wood.local_name, wood.trade_name)
        if value and value.strip()
    ]
    return " / ".join(parts) if parts else "Timber product"


def _size_label(wood: models.Wood) -> str:
    parts: list[str] = []
    if wood.thickness_in:
        parts.append(f"{wood.thickness_in}″")
    if wood.width_in:
        parts.append(f"{wood.width_in}″")
    if wood.length_ft:
        parts.append(f"{wood.length_ft}ft")
    return " × ".join(parts) if parts else "Not specified"


def _is_cloudinary_url(url: Optional[str]) -> bool:
    return bool(url and url.startswith(CLOUDINARY_PREFIX))


def _serialize_images(wood: models.Wood) -> list[dict[str, Any]]:
    """Return Cloudinary images, with the primary image first."""
    images = [
        {
            "image_id": image.image_id,
            "image_url": image.image_url,
            "is_primary": bool(image.is_primary),
            "display_order": int(image.display_order or 0),
        }
        for image in (wood.images or [])
        if _is_cloudinary_url(image.image_url)
    ]
    images.sort(
        key=lambda item: (
            not item["is_primary"],
            item["display_order"],
            item["image_id"],
        )
    )
    return images


def _serialize_wood(wood: models.Wood, *, full: bool = False) -> dict[str, Any]:
    images = _serialize_images(wood)
    data: dict[str, Any] = {
        "wood_id": wood.wood_id,
        "display_name": _display_name(wood),
        "local_name": wood.local_name,
        "trade_name": wood.trade_name,
        "size_label": _size_label(wood),
        "thickness_in": wood.thickness_in,
        "width_in": wood.width_in,
        "length_ft": wood.length_ft,
        "price_per_piece": (
            float(wood.price_per_piece)
            if wood.price_per_piece is not None
            else None
        ),
        "currency": wood.currency or "GHS",
        "stock_quantity": int(wood.stock_quantity or 0),
        "is_treated": bool(wood.is_treated),
        "is_available": bool(wood.is_available),
        "is_featured": bool(getattr(wood, "is_featured", False)),
        "product_form": getattr(wood, "product_form", None),
        "category": (
            {
                "category_id": wood.category.category_id,
                "category_name": wood.category.category_name,
                "slug": wood.category.slug,
            }
            if wood.category
            else None
        ),
        "seller_id": wood.seller_id,
        "seller": (
            {
                "user_id": wood.seller.user_id,
                "name": wood.seller.name,
                "phone_number": wood.seller.phone_number,
            }
            if wood.seller
            else None
        ),
        "images": images,
        "image_url": images[0]["image_url"] if images else None,
    }
    if full:
        data["description"] = getattr(wood, "description", None)
    return data


def _base_query(db: Session):
    return db.query(models.Wood).options(
        joinedload(models.Wood.images),
        joinedload(models.Wood.category),
        joinedload(models.Wood.seller),
    )


def _get_owned_wood(
    db: Session,
    wood_id: int,
    seller: models.User,
    *,
    allow_admin: bool = True,
) -> models.Wood:
    wood = (
        _base_query(db)
        .filter(models.Wood.wood_id == wood_id)
        .first()
    )
    if not wood or wood.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wood not found",
        )

    is_admin = allow_admin and getattr(seller, "role", None) == "admin"
    if not is_admin and wood.seller_id != seller.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your product",
        )
    return wood


def _require_cloudinary() -> None:
    if upload_image_to_cloudinary is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image upload service is not configured.",
        )


def _delete_cloudinary_safely(url: Optional[str]) -> None:
    """Cloudinary cleanup must not turn a successful database action into a failure."""
    if delete_cloudinary_image and _is_cloudinary_url(url):
        try:
            delete_cloudinary_image(url)
        except Exception:
            # The database operation has already succeeded. A remote cleanup
            # failure should be logged by the Cloudinary helper/monitoring layer.
            pass


# ---------------------------------------------------------------------------
# GET /woods/ — public catalogue
#
# Keep sold-out products visible. The frontend can show an "Out of stock"
# label and disable purchasing while preserving product pages and shared links.
# Products explicitly disabled by the seller and soft-deleted products remain
# hidden from the public catalogue.
# ---------------------------------------------------------------------------
@router.get("/")
def list_woods(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    local_name: Optional[str] = Query(None),
    is_treated: Optional[bool] = Query(None),
    min_price: Optional[Decimal] = Query(None, ge=0),
    max_price: Optional[Decimal] = Query(None, ge=0),
    sort: str = Query(
        "featured",
        pattern="^(featured|price_asc|price_desc|name_asc)$",
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_price cannot be greater than max_price",
        )

    query = (
        _base_query(db)
        .filter(models.Wood.is_available.is_(True))
        .filter(models.Wood.deleted_at.is_(None))
    )

    if category:
        query = query.join(models.Category).filter(
            models.Category.slug == category
        )
    if local_name:
        query = query.filter(models.Wood.local_name.ilike(local_name))
    if is_treated is not None:
        query = query.filter(models.Wood.is_treated == is_treated)
    if min_price is not None:
        query = query.filter(models.Wood.price_per_piece >= min_price)
    if max_price is not None:
        query = query.filter(models.Wood.price_per_piece <= max_price)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                models.Wood.local_name.ilike(pattern),
                models.Wood.trade_name.ilike(pattern),
                models.Wood.description.ilike(pattern),
            )
        )

    if sort == "price_asc":
        query = query.order_by(models.Wood.price_per_piece.asc())
    elif sort == "price_desc":
        query = query.order_by(models.Wood.price_per_piece.desc())
    elif sort == "name_asc":
        query = query.order_by(models.Wood.local_name.asc())
    else:
        query = query.order_by(
            models.Wood.is_featured.desc(),
            models.Wood.created_at.desc(),
        )

    rows = query.limit(limit).offset(offset).all()
    return [_serialize_wood(wood) for wood in rows]


# ---------------------------------------------------------------------------
# GET /woods/seller/my-products — seller catalogue
# Keep this route above /{wood_id} so "seller" is not treated as an ID.
# ---------------------------------------------------------------------------
@router.get("/seller/my-products")
def get_my_products(
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    rows = (
        _base_query(db)
        .filter(
            models.Wood.seller_id == current_seller.user_id,
            models.Wood.deleted_at.is_(None),
        )
        .order_by(models.Wood.created_at.desc())
        .all()
    )
    return [_serialize_wood(wood, full=True) for wood in rows]


# ---------------------------------------------------------------------------
# GET /woods/{wood_id} — product detail
# ---------------------------------------------------------------------------
@router.get("/{wood_id}")
def get_wood(wood_id: int, db: Session = Depends(get_db)):
    wood = (
        _base_query(db)
        .filter(models.Wood.wood_id == wood_id)
        .first()
    )
    if not wood or wood.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        )
    return _serialize_wood(wood, full=True)


# ---------------------------------------------------------------------------
# POST /woods/ — create product
# ---------------------------------------------------------------------------
@router.post("/", status_code=status.HTTP_201_CREATED)
def create_wood(
    payload: schemas.WoodCreate,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    category = (
        db.query(models.Category)
        .filter(models.Category.category_id == payload.category_id)
        .first()
    )
    if not category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Category not found",
        )

    wood_data = payload.model_dump(exclude={"seller_id"})
    wood = models.Wood(seller_id=current_seller.user_id, **wood_data)

    try:
        db.add(wood)
        db.commit()
        db.refresh(wood)
    except Exception:
        db.rollback()
        raise

    return _serialize_wood(wood, full=True)


# ---------------------------------------------------------------------------
# PUT /woods/{wood_id} — update product
# ---------------------------------------------------------------------------
@router.put("/{wood_id}")
def update_wood(
    wood_id: int,
    payload: schemas.WoodUpdate,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    wood = _get_owned_wood(db, wood_id, current_seller)

    for field, value in payload.model_dump(exclude_unset=True).items():
        # Product ownership is controlled by the authenticated account.
        if field == "seller_id":
            continue
        setattr(wood, field, value)

    try:
        db.commit()
        db.refresh(wood)
    except Exception:
        db.rollback()
        raise

    return _serialize_wood(wood, full=True)


# ---------------------------------------------------------------------------
# PATCH product availability and stock
# ---------------------------------------------------------------------------
@router.patch("/{wood_id}/availability")
def set_availability(
    wood_id: int,
    payload: schemas.WoodAvailabilityUpdate,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    wood = _get_owned_wood(db, wood_id, current_seller)
    wood.is_available = payload.is_available

    try:
        db.commit()
        db.refresh(wood)
    except Exception:
        db.rollback()
        raise

    return _serialize_wood(wood, full=True)


@router.patch("/{wood_id}/stock")
def set_stock(
    wood_id: int,
    payload: schemas.WoodStockUpdate,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    wood = _get_owned_wood(db, wood_id, current_seller)
    wood.stock_quantity = payload.stock_quantity

    try:
        db.commit()
        db.refresh(wood)
    except Exception:
        db.rollback()
        raise

    return _serialize_wood(wood, full=True)


# ---------------------------------------------------------------------------
# DELETE /woods/{wood_id} — soft delete
# ---------------------------------------------------------------------------
@router.delete("/{wood_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_wood(
    wood_id: int,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    wood = _get_owned_wood(db, wood_id, current_seller)
    wood.deleted_at = sqlfunc.current_timestamp()
    wood.is_available = False

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return None


# ---------------------------------------------------------------------------
# POST /woods/{wood_id}/images — upload product image
# ---------------------------------------------------------------------------
@router.post(
    "/{wood_id}/images",
    status_code=status.HTTP_201_CREATED,
)
async def upload_image(
    wood_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    _require_cloudinary()
    wood = _get_owned_wood(db, wood_id, current_seller)

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only image uploads are allowed",
        )

    # Read one byte beyond the limit so oversized files are rejected without
    # loading an arbitrarily large upload into memory.
    contents = await file.read(MAX_IMAGE_SIZE + 1)
    if not contents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded image is empty",
        )
    if len(contents) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image too large (max 5 MB)",
        )

    try:
        image_url = await run_in_threadpool(
            upload_image_to_cloudinary,
            contents,
            folder="builder_wood/products",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Image upload failed: {exc}",
        ) from exc
    finally:
        await file.close()

    existing_count = (
        db.query(models.WoodImage)
        .filter(models.WoodImage.wood_id == wood_id)
        .count()
    )

    image = models.WoodImage(
        wood_id=wood_id,
        image_url=image_url,
        alt_text=_display_name(wood),
        is_primary=(existing_count == 0),
        display_order=existing_count,
    )

    try:
        db.add(image)
        db.commit()
        db.refresh(image)
    except Exception:
        db.rollback()
        # Avoid leaving an orphaned Cloudinary asset if the DB insert fails.
        if delete_cloudinary_image and _is_cloudinary_url(image_url):
            try:
                await run_in_threadpool(delete_cloudinary_image, image_url)
            except Exception:
                pass
        raise

    return {
        "image_id": image.image_id,
        "image_url": image.image_url,
        "is_primary": bool(image.is_primary),
        "display_order": int(image.display_order or 0),
    }


# ---------------------------------------------------------------------------
# DELETE /woods/{wood_id}/images/{image_id} — delete product image
# ---------------------------------------------------------------------------
@router.delete(
    "/{wood_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_image(
    wood_id: int,
    image_id: int,
    db: Session = Depends(get_db),
    current_seller: models.User = Depends(get_current_active_seller),
):
    _get_owned_wood(db, wood_id, current_seller)

    image = (
        db.query(models.WoodImage)
        .filter(
            models.WoodImage.image_id == image_id,
            models.WoodImage.wood_id == wood_id,
        )
        .first()
    )
    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )

    was_primary = bool(image.is_primary)
    image_url = image.image_url

    try:
        if was_primary:
            remaining_images = (
                db.query(models.WoodImage)
                .filter(
                    models.WoodImage.wood_id == wood_id,
                    models.WoodImage.image_id != image_id,
                )
                .order_by(
                    models.WoodImage.display_order.asc(),
                    models.WoodImage.image_id.asc(),
                )
                .all()
            )

            # Normalize primary flags and promote the next image, if one exists.
            for remaining in remaining_images:
                remaining.is_primary = False
            if remaining_images:
                remaining_images[0].is_primary = True

        db.delete(image)
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Delete the remote asset only after the database transaction succeeds.
    _delete_cloudinary_safely(image_url)
    return None
