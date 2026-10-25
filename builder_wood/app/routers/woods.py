from pathlib import Path
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import or_, func as sqlfunc
from sqlalchemy.orm import Session, joinedload

from builder_wood.app.database import get_db
from builder_wood.app import models, schemas
from builder_wood.app.auth import get_current_active_seller

router = APIRouter(prefix="/woods", tags=["woods"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "static" / "uploads" / "products"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _to_list_out(w):
    images = sorted(
        w.images or [],
        key=lambda image: (
            not image.is_primary,
            image.display_order,
            image.image_id
        )
    )
    primary_image = images[0].image_url if images else None

    return {
        "wood_id": w.wood_id,
        "local_name": w.local_name,
        "trade_name": w.trade_name,
        "display_name": w.display_name,
        "size_label": w.size_label,
        "price_per_piece": w.price_per_piece,
        "currency": w.currency,
        "stock_quantity": w.stock_quantity,
        "is_treated": w.is_treated,
        "is_featured": w.is_featured,
        "category_id": w.category_id,
        "category_slug": w.category.slug if w.category else None,
        "category_name": w.category.category_name if w.category else None,

        # --- SELLER INFO ---
        "seller_id": w.seller_id,
        "seller_name": w.seller.name if w.seller else "Builder Wood",
        "seller_phone": w.seller.phone_number if w.seller else "",

        "primary_image_url": primary_image,
    }


@router.get("/", response_model=List[schemas.WoodListOut])
def list_woods(
        category: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        local_name: Optional[str] = Query(None),
        is_treated: Optional[bool] = Query(None),
        min_price: Optional[Decimal] = Query(None, ge=0),
        max_price: Optional[Decimal] = Query(None, ge=0),
        sort: str = Query("featured", pattern="^(featured|price_asc|price_desc|name_asc)$"),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_db),
):
    q = (
        db.query(models.Wood)
        .options(
            joinedload(models.Wood.images),
            joinedload(models.Wood.category),
            joinedload(models.Wood.seller)
        )
        .filter(models.Wood.is_available == True)
        .filter(models.Wood.deleted_at.is_(None))
        .filter(models.Wood.stock_quantity > 0)
    )

    if category:
        q = q.join(models.Category).filter(models.Category.slug == category)
    if local_name:
        q = q.filter(models.Wood.local_name.ilike(local_name))
    if is_treated is not None:
        q = q.filter(models.Wood.is_treated == is_treated)
    if min_price is not None:
        q = q.filter(models.Wood.price_per_piece >= min_price)
    if max_price is not None:
        q = q.filter(models.Wood.price_per_piece <= max_price)
    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                models.Wood.local_name.ilike(like),
                models.Wood.trade_name.ilike(like),
                models.Wood.description.ilike(like),
            )
        )

    if sort == "price_asc":
        q = q.order_by(models.Wood.price_per_piece.asc())
    elif sort == "price_desc":
        q = q.order_by(models.Wood.price_per_piece.desc())
    elif sort == "name_asc":
        q = q.order_by(models.Wood.local_name.asc())
    else:
        q = q.order_by(models.Wood.is_featured.desc(), models.Wood.created_at.desc())

    rows = q.limit(limit).offset(offset).all()
    return [_to_list_out(w) for w in rows]


# ============================================================
# SELLER: Get My Products
# ============================================================
@router.get("/seller/my-products", response_model=List[schemas.WoodListOut])
def get_my_products(
        db: Session = Depends(get_db),
        current_seller: models.User = Depends(get_current_active_seller)
):
    woods = (
        db.query(models.Wood)
        .options(
            joinedload(models.Wood.images),
            joinedload(models.Wood.category),
            joinedload(models.Wood.seller)
        )
        .filter(
            models.Wood.seller_id == current_seller.user_id,
            models.Wood.deleted_at.is_(None)
        )
        .order_by(models.Wood.created_at.desc())
        .all()
    )
    return [_to_list_out(w) for w in woods]


@router.get("/{wood_id}", response_model=schemas.WoodOut)
def get_wood(wood_id: int, db: Session = Depends(get_db)):
    wood = (
        db.query(models.Wood)
        .options(
            joinedload(models.Wood.images),
            joinedload(models.Wood.category),
            joinedload(models.Wood.seller)
        )
        .filter(models.Wood.wood_id == wood_id)
        .first()
    )
    if not wood or wood.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Wood not found")
    return wood


@router.post("/", response_model=schemas.WoodOut, status_code=201)
def create_wood(payload: schemas.WoodCreate, seller_id: int, db: Session = Depends(get_db)):
    seller = db.query(models.User).filter(models.User.user_id == seller_id).first()
    if not seller or seller.role not in ("seller", "admin"):
        raise HTTPException(status_code=403, detail="Only sellers can list woods")
    category = db.query(models.Category).filter(models.Category.category_id == payload.category_id).first()
    if not category:
        raise HTTPException(status_code=400, detail="Category not found")
    wood = models.Wood(seller_id=seller_id, **payload.model_dump())
    db.add(wood)
    db.commit()
    db.refresh(wood)
    return wood


@router.put("/{wood_id}", response_model=schemas.WoodOut)
def update_wood(wood_id: int, payload: schemas.WoodUpdate, db: Session = Depends(get_db)):
    wood = db.query(models.Wood).filter(models.Wood.wood_id == wood_id).first()
    if not wood:
        raise HTTPException(status_code=404, detail="Wood not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(wood, field, value)
    db.commit()
    db.refresh(wood)
    return wood


@router.patch("/{wood_id}/availability", response_model=schemas.WoodOut)
def set_availability(wood_id: int, payload: schemas.WoodAvailabilityUpdate, db: Session = Depends(get_db)):
    wood = db.query(models.Wood).filter(models.Wood.wood_id == wood_id).first()
    if not wood:
        raise HTTPException(status_code=404, detail="Wood not found")
    wood.is_available = payload.is_available
    db.commit()
    db.refresh(wood)
    return wood


@router.patch("/{wood_id}/stock", response_model=schemas.WoodOut)
def set_stock(wood_id: int, payload: schemas.WoodStockUpdate, db: Session = Depends(get_db)):
    wood = db.query(models.Wood).filter(models.Wood.wood_id == wood_id).first()
    if not wood:
        raise HTTPException(status_code=404, detail="Wood not found")
    wood.stock_quantity = payload.stock_quantity
    db.commit()
    db.refresh(wood)
    return wood


@router.delete("/{wood_id}", status_code=204)
def delete_wood(
        wood_id: int,
        db: Session = Depends(get_db),
        current_seller: models.User = Depends(get_current_active_seller)
):
    wood = db.query(models.Wood).filter(
        models.Wood.wood_id == wood_id,
        models.Wood.seller_id == current_seller.user_id
    ).first()

    if not wood:
        raise HTTPException(status_code=404, detail="Wood not found or you don't have permission")

    wood.deleted_at = sqlfunc.current_timestamp()
    wood.is_available = False
    db.commit()
    return None


@router.post("/{wood_id}/images", response_model=schemas.WoodImageOut, status_code=201)
def upload_image(wood_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    wood = db.query(models.Wood).filter(models.Wood.wood_id == wood_id).first()
    if not wood:
        raise HTTPException(status_code=404, detail="Wood not found")
    safe = "".join(c for c in file.filename if c.isalnum() or c in "._-")
    filename = f"{wood_id}_{safe}"
    dest = UPLOAD_DIR / filename
    with dest.open("wb") as f:
        f.write(file.file.read())
    is_first = db.query(models.WoodImage).filter(models.WoodImage.wood_id == wood_id).count() == 0
    img = models.WoodImage(
        wood_id=wood_id,
        image_url=f"/static/uploads/products/{filename}",
        alt_text=wood.display_name,
        is_primary=is_first,
    )
    db.add(img)
    db.commit()
    db.refresh(img)
    return img


@router.delete("/{wood_id}/images/{image_id}", status_code=204)
def delete_image(wood_id: int, image_id: int, db: Session = Depends(get_db)):
    img = (
        db.query(models.WoodImage)
        .filter(models.WoodImage.image_id == image_id, models.WoodImage.wood_id == wood_id)
        .first()
    )
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")
    file_path = UPLOAD_DIR / Path(img.image_url).name
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError:
        pass
    db.delete(img)
    db.commit()
    return None