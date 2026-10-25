"""
Bulk-import woods into woodshop_db.

Usage (from project root 'digital marketing/'):
    python -m builder_wood.import_woods

Idempotent: re-running skips woods that already exist (same name + size + seller).
"""

from decimal import Decimal

from builder_wood.app.database import SessionLocal
from builder_wood.app.models import User, Category, Wood, WoodImage


# ============================================================
# EDIT THIS LIST TO ADD YOUR WOODS
# ============================================================
# Each row is a tuple:
#   (
#     local_name, trade_name, category_slug,
#     thickness_in, width_in, length_ft,
#     product_form, is_treated,
#     price_ghs, stock_pieces, is_featured,
#     description, durability
#   )
#
# category_slug must be one of:
#   hardwood, softwood, roofing, ceiling, furniture, interior, flooring
#
# product_form must be one of:
#   Sawn Timber, Ceiling Batten, T&G Board, Moulding, Plinth
# ============================================================

WOODS = [
    # ---------- Wawa (softwood + ceiling) ----------
    (
        "Wawa", "Obeche", "softwood",
        2, 4, 14, "Sawn Timber", False,
        65, 300, False,
        "Light, easy to saw. The everyday Sokoban roofing timber.",
        "Low — treat for outdoor use",
    ),
    (
        "Wawa", "Obeche", "ceiling",
        2, 2, 12, "Ceiling Batten", True,
        25, 800, False,
        "Treated ceiling batten for nogging and furring.",
        "Low — treated",
    ),
    (
        "Wawa", "Obeche", "roofing",
        1, 12, 14, "Sawn Timber", False,
        45, 400, False,
        "Wide Wawa board for roofing and ceiling panels.",
        "Low — treat for outdoor use",
    ),

    # ---------- Esa (interior / construction) ----------
    (
        "Esa", "Celtis", "interior",
        2, 4, 14, "Sawn Timber", False,
        72, 150, False,
        "Hard, strong hardwood. Non-durable — treat before outdoor use.",
        "Non-durable — treat",
    ),
    (
        "Esa", "Celtis", "hardwood",
        2, 3, 14, "Sawn Timber", False,
        55, 200, False,
        "Smaller Esa profile for framing and light construction.",
        "Non-durable — treat",
    ),

    # ---------- Denya (heavy hardwood) ----------
    (
        "Denya", "Okan", "hardwood",
        4, 6, 14, "Sawn Timber", False,
        140, 60, True,
        "Dense and durable. Heavy flooring, bridges, mining timbers.",
        "Very durable",
    ),
    (
        "Denya", "Okan", "flooring",
        2, 4, 14, "Sawn Timber", False,
        135, 80, False,
        "Denya flooring stock — hard-wearing and long-lasting.",
        "Very durable",
    ),

    # ---------- Odum (prestige hardwood) ----------
    (
        "Odum", "Iroko", "hardwood",
        2, 4, 14, "Sawn Timber", False,
        150, 50, True,
        "Ghana's prestige hardwood. Furniture, doors, boat work.",
        "Very durable",
    ),
    (
        "Odum", "Iroko", "furniture",
        1, 12, 10, "Sawn Timber", False,
        180, 30, False,
        "Wide Odum board for premium furniture and cabinet tops.",
        "Very durable",
    ),

    # ---------- Dahoma ----------
    (
        "Dahoma", "Dahoma", "hardwood",
        2, 4, 14, "Sawn Timber", False,
        75, 120, False,
        "Strong structural timber. Note: new Dahoma has a smell — dry longer.",
        "Moderately durable",
    ),

    # ---------- Ofram ----------
    (
        "Ofram", "Ofram", "softwood",
        1, 12, 14, "Sawn Timber", False,
        55, 250, False,
        "Lightweight general-purpose timber. Everyday Sokoban stock.",
        "Low — treat for outdoor use",
    ),
    (
        "Ofram", "Ofram", "ceiling",
        2, 2, 12, "Ceiling Batten", True,
        28, 500, False,
        "Treated Ofram ceiling batten, alternative to Wawa.",
        "Low — treated",
    ),

    # ---------- Mahogany ----------
    (
        "Mahogany", "African Mahogany", "furniture",
        2, 4, 14, "Sawn Timber", False,
        110, 90, False,
        "Classic furniture timber. Rich colour, easy to work.",
        "Moderately durable",
    ),

    # ---------- Cindella ----------
    (
        "Cindella", "Cedar", "furniture",
        2, 4, 14, "Sawn Timber", False,
        95, 40, False,
        "Sokoban carver's favourite. Verify origin with supplier.",
        "Low — verify with supplier",
    ),

    # ---------- Sapele ----------
    (
        "Sapele", "Sapele", "furniture",
        1, 6, 14, "Sawn Timber", False,
        105, 60, False,
        "Premium furniture and veneer timber. Attractive grain.",
        "Moderately durable",
    ),
]


# ============================================================
# Optional: images
# ============================================================
# For each wood created, if a matching image exists in
# builder_wood/app/static/uploads/products/, we attach it.
#
# Filename pattern we look for (first match wins):
#   {wood_id}.jpg, {wood_id}.jpeg, {wood_id}.png
#   {wood_id}_{local_name_lower}.jpg, ...
#
# So if the wood ends up with wood_id=7, drop '7.jpg' or
# '7_wawa.jpg' into that folder and it will be attached.
# ============================================================

from pathlib import Path

UPLOAD_DIR = Path(__file__).resolve().parent / "app" / "static" / "uploads" / "products"


def find_image(wood_id: int, local_name: str):
    """Return a URL for the first matching image file, or None."""
    if not UPLOAD_DIR.exists():
        return None
    slug = local_name.lower().replace(" ", "-")
    candidates = [
        f"{wood_id}.jpg",  f"{wood_id}.jpeg", f"{wood_id}.png",
        f"{wood_id}_{slug}.jpg", f"{wood_id}_{slug}.jpeg", f"{wood_id}_{slug}.png",
    ]
    for name in candidates:
        if (UPLOAD_DIR / name).exists():
            return f"/static/uploads/products/{name}"
    return None


# ============================================================
# Import logic
# ============================================================
def main():
    db = SessionLocal()
    try:
        # 1. Seller
        seller = db.query(User).filter(User.role == "seller").first()
        if not seller:
            print("✗ No seller found in users table.")
            print("  Insert one first. Example SQL:")
            print("""
  INSERT INTO users (role, name, company_name, email, phone_number, password_hash, location)
  VALUES ('seller', 'Jane Mensah', 'Mensah Timber, Sokoban',
          'jane@sokobanwood.gh', '+233244000001',
          'not-a-real-hash-yet', 'Sokoban Wood Village, Kumasi');
""")
            return
        print(f"→ using seller: {seller.name} (user_id={seller.user_id})")

        # 2. Category lookup
        cats = {c.slug: c.category_id for c in db.query(Category).all()}
        if not cats:
            print("✗ No categories found. Insert the 7 categories first.")
            return
        print(f"→ loaded {len(cats)} categories: {', '.join(sorted(cats.keys()))}")

        # 3. Import loop
        added = skipped = images_added = 0
        for row in WOODS:
            (local, trade, slug, t, w, l, form, treated,
             price, stock, featured, desc, dur) = row

            if slug not in cats:
                print(f"  ⚠ skip {local} {t}x{w}x{l}: category '{slug}' missing")
                skipped += 1
                continue

            # Skip duplicate
            exists = (db.query(Wood)
                      .filter_by(local_name=local, thickness_in=t,
                                 width_in=w, length_ft=l,
                                 seller_id=seller.user_id)
                      .first())
            if exists:
                print(f"  • {local} {int(t)}x{int(w)}x{int(l)} already listed")
                skipped += 1
                continue

            wood = Wood(
                local_name=local,
                trade_name=trade,
                category_id=cats[slug],
                seller_id=seller.user_id,
                thickness_in=Decimal(str(t)),
                width_in=Decimal(str(w)),
                length_ft=Decimal(str(l)),
                product_form=form,
                is_treated=treated,
                price_per_piece=Decimal(str(price)),
                stock_quantity=stock,
                description=desc,
                durability=dur,
                currency="GHS",
                source_market="Sokoban Wood Village",
                source_town="Kumasi",
                is_available=True,
                is_featured=featured,
            )
            db.add(wood)
            db.flush()  # get wood.wood_id

            # Image attach (if a matching file exists)
            url = find_image(wood.wood_id, local)
            if url:
                db.add(WoodImage(
                    wood_id=wood.wood_id,
                    image_url=url,
                    alt_text=f"{local} {int(t)}x{int(w)}x{int(l)}",
                    is_primary=True,
                    display_order=0,
                ))
                images_added += 1
                img_note = f"  [img: {url.split('/')[-1]}]"
            else:
                img_note = ""

            print(f"  ✓ {local} {int(t)}x{int(w)}x{int(l)}  GH₵{price}{img_note}")
            added += 1

        db.commit()
        print(f"\nDone. added={added}, skipped={skipped}, images={images_added}")
        print("Open http://127.0.0.1:8000/ to see them.")
    finally:
        db.close()


if __name__ == "__main__":
    main()