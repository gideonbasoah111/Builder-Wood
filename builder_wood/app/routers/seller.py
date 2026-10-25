from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pathlib import Path
import shutil, uuid
from typing import List, Optional

from builder_wood.app.database import get_db
from builder_wood.app import models
from builder_wood.app.auth import get_current_active_seller

router = APIRouter(prefix="/seller", tags=["seller"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "static" / "uploads" / "products"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/add-wood")
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
        dimension_image: UploadFile = File(None),
        detail_images: List[UploadFile] = File(default=[]),
        db: Session = Depends(get_db),
        current_seller: models.User = Depends(get_current_active_seller)
):
    category = db.query(models.Category).filter(models.Category.slug == category_slug).first()
    if not category:
        raise HTTPException(status_code=400, detail="Invalid category")

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
        is_available=True
    )
    db.add(new_wood)
    db.commit()
    db.refresh(new_wood)

    order_counter = 1  # 0 is reserved for the main image

    # 1. MAIN IMAGE
    if main_image and main_image.filename:
        file_extension = Path(main_image.filename).suffix.lower() or ".jpg"
        unique_filename = f"{new_wood.wood_id}_main_{uuid.uuid4().hex[:6]}{file_extension}"
        file_path = UPLOAD_DIR / unique_filename

        with file_path.open("wb") as buffer:
            shutil.copyfileobj(main_image.file, buffer)

        db.add(models.WoodImage(
            wood_id=new_wood.wood_id,
            image_url=f"/static/uploads/products/{unique_filename}",
            is_primary=True,
            display_order=0
        ))

    # 2. DIMENSION IMAGE
    if dimension_image and dimension_image.filename:
        file_extension = Path(dimension_image.filename).suffix.lower() or ".jpg"
        unique_filename = f"{new_wood.wood_id}_dim_{uuid.uuid4().hex[:6]}{file_extension}"
        file_path = UPLOAD_DIR / unique_filename

        with file_path.open("wb") as buffer:
            shutil.copyfileobj(dimension_image.file, buffer)

        db.add(models.WoodImage(
            wood_id=new_wood.wood_id,
            image_url=f"/static/uploads/products/{unique_filename}",
            is_primary=False,
            display_order=order_counter
        ))
        order_counter += 1

    # 3. DETAIL IMAGES
    if detail_images:
        for image in detail_images:
            if not image.filename:
                continue

            file_extension = Path(image.filename).suffix.lower() or ".jpg"
            unique_filename = f"{new_wood.wood_id}_detail_{uuid.uuid4().hex[:6]}{file_extension}"
            file_path = UPLOAD_DIR / unique_filename

            with file_path.open("wb") as buffer:
                shutil.copyfileobj(image.file, buffer)

            db.add(models.WoodImage(
                wood_id=new_wood.wood_id,
                image_url=f"/static/uploads/products/{unique_filename}",
                is_primary=False,
                display_order=order_counter
            ))
            order_counter += 1

    db.commit()
    return {"status": "success", "wood_id": new_wood.wood_id}