from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field

from builder_wood.app.database import get_db
from builder_wood.app import models
from builder_wood.app.auth import get_current_user

router = APIRouter(prefix="/cart", tags=["cart"])


# ============================================================
# Schemas with strict validation
# ============================================================
class CartItemAdd(BaseModel):
    wood_id: int
    quantity: int = Field(default=1, ge=1)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1)


class CartItemOut(BaseModel):
    cart_item_id: int
    wood_id: int
    quantity: int
    local_name: str
    price_per_piece: float
    currency: str
    image_url: Optional[str] = None

    class Config:
        from_attributes = True


# ============================================================
# Routes
# ============================================================

@router.get("/", response_model=List[CartItemOut])
def get_cart(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    items = db.query(models.CartItem).filter(models.CartItem.user_id == current_user.user_id).all()
    result = []
    for item in items:
        img = db.query(models.WoodImage).filter(
            models.WoodImage.wood_id == item.wood_id,
            models.WoodImage.is_primary == True
        ).first()

        result.append({
            "cart_item_id": item.cart_item_id,
            "wood_id": item.wood_id,
            "quantity": item.quantity,
            "local_name": item.wood.local_name,
            "price_per_piece": float(item.wood.price_per_piece),
            "currency": item.wood.currency,
            "image_url": img.image_url if img else None
        })
    return result


@router.post("/add")
def add_to_cart(payload: CartItemAdd, db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    # 1. Validate the wood exists and is available
    wood = db.query(models.Wood).filter(
        models.Wood.wood_id == payload.wood_id,
        models.Wood.is_available == True,
        models.Wood.deleted_at.is_(None)
    ).first()

    if not wood:
        raise HTTPException(404, "This wood is not available")

    # --- THIS IS THE NEW LINE YOU NEED ---
    if wood.seller_id == current_user.user_id:
        raise HTTPException(400, "You cannot buy your own wood.")
    # -------------------------------------

    # 2. Check if item is already in cart
    existing = db.query(models.CartItem).filter(
        models.CartItem.user_id == current_user.user_id,
        models.CartItem.wood_id == payload.wood_id
    ).first()

    # 3. Determine total requested quantity
    current_qty = existing.quantity if existing else 0
    new_total_qty = current_qty + payload.quantity

    # 4. Validate against stock
    if new_total_qty > wood.stock_quantity:
        raise HTTPException(
            400,
            f"Only {wood.stock_quantity} pieces available in stock."
        )

    if existing:
        existing.quantity = new_total_qty
        db.commit()
        return {"message": "Quantity updated"}

    new_item = models.CartItem(
        user_id=current_user.user_id,
        wood_id=payload.wood_id,
        quantity=payload.quantity
    )
    db.add(new_item)
    db.commit()
    return {"message": "Item added to cart"}


@router.put("/{cart_item_id}")
def update_quantity(cart_item_id: int, payload: CartItemUpdate, db: Session = Depends(get_db),
                    current_user: models.User = Depends(get_current_user)):
    # 1. Find the cart item
    item = db.query(models.CartItem).filter(
        models.CartItem.cart_item_id == cart_item_id,
        models.CartItem.user_id == current_user.user_id
    ).first()

    if not item:
        raise HTTPException(404, "Cart item not found")

    # 2. Validate the wood is still available
    wood = db.query(models.Wood).filter(
        models.Wood.wood_id == item.wood_id,
        models.Wood.is_available == True,
        models.Wood.deleted_at.is_(None)
    ).first()

    if not wood:
        raise HTTPException(400, "This wood is no longer available")

    # 3. Validate stock
    if payload.quantity > wood.stock_quantity:
        raise HTTPException(
            400,
            f"Only {wood.stock_quantity} pieces available in stock."
        )

    # 4. Update
    item.quantity = payload.quantity
    db.commit()
    return {"message": "Updated"}


@router.delete("/{cart_item_id}")
def remove_item(cart_item_id: int, db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    item = db.query(models.CartItem).filter(
        models.CartItem.cart_item_id == cart_item_id,
        models.CartItem.user_id == current_user.user_id
    ).first()

    if item:
        db.delete(item)
        db.commit()
    return {"message": "Removed"}


# ============================================================
# IMPORTANT: This MUST be above /{cart_item_id}
# ============================================================
@router.delete("/clear")
def clear_cart(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    db.query(models.CartItem).filter(models.CartItem.user_id == current_user.user_id).delete()
    db.commit()
    return {"message": "Cart cleared"}