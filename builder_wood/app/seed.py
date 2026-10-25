"""
Seed script for Builder Wood.

Usage (from project root 'digital marketing/'):
    python -m builder_wood.seed

Inserts:
  - 1 demo seller
  - 6 demo wood listings spread across categories
Idempotent: skips anything that already exists.
"""

from decimal import Decimal

from builder_wood.app.database import SessionLocal
from builder_wood.app.models import User, Category, Wood


SELLER_EMAIL = "jane@sokobanwood.gh"


def get_or_create_seller(db):
    seller = db.query(User).filter(User.email == SELLER_EMAIL).first()
    if seller:
        print(f"→ seller already exists: id={seller.user_id}")
        return seller
    seller = User(
        role="seller",
        name="Jane Mensah",
        company_name="Mensah Timber, Sokoban",
        email=SELLER_EMAIL,
        phone_number="+233244000001",
        password_hash="not-a-real-hash-yet",     # we'll do real auth later
        location="Sokoban Wood Village, Kumasi",
    )
    db.add(seller)
    db.commit()
    db.refresh(seller)
    print(f"✓ created seller: id={seller.user_id}")
    return seller


def cat_id(db, slug):
    c = db.query(Category).filter(Category.slug == slug).first()
    if not c:
        raise SystemExit(f"Category '{slug}' not found. Did you run the seed SQL?")
    return c.category_id


def seed_woods(db, seller):
    if db.query(Wood).filter(Wood.seller_id == seller.user_id).count() > 0:
        print("→ woods already seeded")
        return

    rows = [
        # (local, trade, category, thickness, width, length, treated, price, stock, form, desc)
        ("Wawa",  "Obeche", "softwood", 2, 4, 14, False, 65,  300, "Sawn Timber",
         "Light, easy to saw. The everyday Sokoban roofing timber."),
        ("Wawa",  "Obeche", "ceiling",  2, 2, 12, True,  25,  800, "Ceiling Batten",
         "Treated ceiling batten for nogging and furring."),
        ("Esa",   "Celtis", "interior", 2, 4, 14, False, 72,  150, "Sawn Timber",
         "Hard, strong hardwood. Non-durable — treat before outdoor use."),
        ("Denya", "Okan",   "hardwood", 4, 6, 14, False, 140,  60, "Sawn Timber",
         "Dense and durable. Heavy flooring, bridges, mining timbers."),
        ("Odum",  "Iroko",  "hardwood", 2, 4, 14, False, 150,  50, "Sawn Timber",
         "Ghana's prestige hardwood. Furniture, doors, boat work."),
        ("Cindella", "Cedar", "furniture", 2, 4, 14, False, 95, 40, "Sawn Timber",
         "Sokoban carver's favourite. Verify origin with supplier."),
    ]

    created = 0
    for (local, trade, slug, t, w, l, treated, price, stock, form, desc) in rows:
        wood = Wood(
            local_name=local,
            trade_name=trade,
            category_id=cat_id(db, slug),
            seller_id=seller.user_id,
            thickness_in=Decimal(str(t)),
            width_in=Decimal(str(w)),
            length_ft=Decimal(str(l)),
            product_form=form,
            is_treated=treated,
            price_per_piece=Decimal(str(price)),
            stock_quantity=stock,
            description=desc,
            currency="GHS",
            source_market="Sokoban Wood Village",
            source_town="Kumasi",
            is_available=True,
            is_featured=(local in ("Denya", "Odum")),
        )
        db.add(wood)
        created += 1

    db.commit()
    print(f"✓ inserted {created} woods")


def main():
    db = SessionLocal()
    try:
        seller = get_or_create_seller(db)
        seed_woods(db, seller)
        print("\nDone. Open http://127.0.0.1:8000/ to see them.")
    finally:
        db.close()


if __name__ == "__main__":
    main()