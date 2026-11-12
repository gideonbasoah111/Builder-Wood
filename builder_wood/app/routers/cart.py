"""Cart management — user-facing endpoints."""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from builder_wood.app.database import get_db
from builder_wood.app import models
from builder_wood.app.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cart", tags=["cart"])


# ============================================================
# Schemas
# ============================================================
class CartItemAdd(BaseModel):
    wood_id: int
    quantity: int = Field(default=1, ge=1, le=9999)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=9999)


class CartItemOut(BaseModel):
    cart_item_id: int
    wood_id: int
    quantity: int
    local_name: str
    price_per_piece: str
    unit_price_snapshot: str
    currency: str
    image_url: Optional[str] = None
    available_stock: int
    is_available: bool
    over_requested: bool

    model_config = {"from_attributes": True}


# ============================================================
# Helpers
# ============================================================
def _get_owned_item(db: Session, user_id: int, cart_item_id: int) -> models.CartItem:
    item = (
        db.query(models.CartItem)
        .filter(
            models.CartItem.cart_item_id == cart_item_id,
            models.CartItem.user_id == user_id,
        )
        .first()
    )
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cart item not found")
    return item


# ============================================================
# Routes — order matters: literal paths before parameterized ones
# ============================================================
@router.delete("/clear", response_model=dict)
def clear_cart(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    db.query(models.CartItem).filter(
        models.CartItem.user_id == current_user.user_id
    ).delete(synchronize_session=False)
    db.commit()
    return {"message": "Cart cleared"}


@router.get("/", response_model=List[CartItemOut])
def get_cart(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    items = (
        db.query(models.CartItem)
        .options(joinedload(models.CartItem.wood))
        .filter(models.CartItem.user_id == current_user.user_id)
        .all()
    )
    if not items:
        return []

    wood_ids = [item.wood_id for item in items]
    images = (
        db.query(models.WoodImage)
        .filter(
            models.WoodImage.wood_id.in_(wood_ids),
            models.WoodImage.is_primary.is_(True),
        )
        .all()
    )
    image_map = {img.wood_id: img.image_url for img in images}

    result = []
    for item in items:
        wood = item.wood
        if wood is None:
            # Wood was soft-deleted or removed entirely.
            result.append(
                {
                    "cart_item_id": item.cart_item_id,
                    "wood_id": item.wood_id,
                    "quantity": item.quantity,
                    "local_name": "Item no longer available",
                    "price_per_piece": str(item.unit_price),
                    "unit_price_snapshot": str(item.unit_price),
                    "currency": item.currency or "GHS",
                    "image_url": None,
                    "available_stock": 0,
                    "is_available": False,
                    "over_requested": True,
                }
            )
            continue

        stock = wood.stock_quantity or 0
        result.append(
            {
                "cart_item_id": item.cart_item_id,
                "wood_id": item.wood_id,
                "quantity": item.quantity,
                "local_name": wood.local_name,
                "price_per_piece": str(wood.price_per_piece),
                "unit_price_snapshot": str(item.unit_price),
                "currency": wood.currency,
                "image_url": image_map.get(item.wood_id),
                "available_stock": stock,
                "is_available": bool(wood.is_available) and wood.deleted_at is None,
                "over_requested": item.quantity > stock,
            }
        )
    return result


@router.post("/add", response_model=dict)
def add_to_cart(
    payload: CartItemAdd,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    wood = (
        db.query(models.Wood)
        .filter(
            models.Wood.wood_id == payload.wood_id,
            models.Wood.is_available.is_(True),
            models.Wood.deleted_at.is_(None),
        )
        .first()
    )

    if not wood:
        raise HTTPException(404, "This wood is not available")

    if wood.seller_id == current_user.user_id:
        raise HTTPException(400, "You cannot buy your own wood.")

    existing = (
        db.query(models.CartItem)
        .filter(
            models.CartItem.user_id == current_user.user_id,
            models.CartItem.wood_id == payload.wood_id,
        )
        .first()
    )

    new_total_qty = (existing.quantity if existing else 0) + payload.quantity
    if new_total_qty > (wood.stock_quantity or 0):
        raise HTTPException(
            400, f"Only {wood.stock_quantity} pieces available in stock."
        )

    if existing:
        existing.quantity = new_total_qty
    else:
        db.add(
            models.CartItem(
                user_id=current_user.user_id,
                wood_id=payload.wood_id,
                quantity=payload.quantity,
                unit_price=wood.price_per_piece,
                currency=wood.currency,
            )
        )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(409, "Please retry — the cart changed under you.")

    return {"message": "Quantity updated" if existing else "Item added to cart"}


@router.put("/{cart_item_id}", response_model=dict)
def update_quantity(
    cart_item_id: int,
    payload: CartItemUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    item = _get_owned_item(db, current_user.user_id, cart_item_id)

    wood = (
        db.query(models.Wood)
        .filter(
            models.Wood.wood_id == item.wood_id,
            models.Wood.is_available.is_(True),
            models.Wood.deleted_at.is_(None),
        )
        .first()
    )
    if not wood:
        raise HTTPException(400, "This wood is no longer available")

    if payload.quantity > (wood.stock_quantity or 0):
        raise HTTPException(
            400, f"Only {wood.stock_quantity} pieces available in stock."
        )

    item.quantity = payload.quantity
    db.commit()
    return {"message": "Updated"}


@router.delete("/{cart_item_id}", response_model=dict)
def remove_item(
    cart_item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    item = _get_owned_item(db, current_user.user_id, cart_item_id)
    db.delete(item)
    db.commit()
    return {"message": "Removed"}