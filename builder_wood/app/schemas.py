from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ============================================================
# USERS
# ============================================================
class UserBase(BaseModel):
    name: str
    company_name: Optional[str] = None
    email: EmailStr
    phone_number: Optional[str] = None
    address: Optional[str] = None
    location: Optional[str] = None
    role: str = "customer"


class UserCreate(UserBase):
    password: str = Field(min_length=6)


class UserUpdate(BaseModel):
    name: Optional[str] = None
    company_name: Optional[str] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    location: Optional[str] = None


class UserOut(UserBase):
    user_id: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# CATEGORIES
# ============================================================
class CategoryBase(BaseModel):
    category_name: str
    slug: str
    parent_category: Optional[int] = None
    description: Optional[str] = None
    display_order: int = 0


class CategoryCreate(CategoryBase):
    pass


class CategoryOut(CategoryBase):
    category_id: int
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# VEHICLES
# ============================================================
class VehicleBase(BaseModel):
    name: str
    max_pieces: int
    max_length_ft: Decimal
    max_distance_km: Decimal
    rate_per_km: Decimal
    base_fee: Decimal


class VehicleCreate(VehicleBase):
    pass


class VehicleOut(VehicleBase):
    vehicle_id: int
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# WOOD IMAGES
# ============================================================
class WoodImageOut(BaseModel):
    image_id: int
    image_url: str
    alt_text: Optional[str] = None
    is_primary: bool
    display_order: int

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# WOODS
# ============================================================
class WoodBase(BaseModel):
    local_name: str
    scientific_name: Optional[str] = None
    trade_name: Optional[str] = None
    description: Optional[str] = None
    durability: Optional[str] = None

    thickness_in: Decimal
    width_in: Decimal
    length_ft: Decimal
    product_form: str = "Sawn Timber"
    is_treated: bool = False

    currency: str = "GHS"
    price_per_piece: Decimal
    stock_quantity: int = 0

    category_id: int
    source_market: str = "Sokoban Wood Village"
    source_town: str = "Kumasi"

    is_available: bool = True
    is_featured: bool = False


class WoodCreate(WoodBase):
    """Payload a seller sends to create a new wood listing."""
    pass


class WoodUpdate(BaseModel):
    """Partial update for price, stock, availability, etc."""
    local_name: Optional[str] = None
    trade_name: Optional[str] = None
    description: Optional[str] = None
    durability: Optional[str] = None
    price_per_piece: Optional[Decimal] = None
    stock_quantity: Optional[int] = None
    category_id: Optional[int] = None
    is_treated: Optional[bool] = None
    is_available: Optional[bool] = None
    is_featured: Optional[bool] = None


class WoodAvailabilityUpdate(BaseModel):
    is_available: bool


class WoodStockUpdate(BaseModel):
    stock_quantity: int = Field(ge=0)


# ---- Read models used by the frontend ----
class WoodListOut(BaseModel):
    """Lightweight shape used by GET /woods (browse grid)."""

    wood_id: int
    local_name: str
    trade_name: Optional[str] = None
    display_name: str
    size_label: str
    price_per_piece: Decimal
    currency: str
    stock_quantity: int
    is_treated: bool
    is_featured: bool
    category_id: int

    # ADDED: These are required for the frontend cards
    category_slug: Optional[str] = None
    category_name: Optional[str] = None

    primary_image_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class WoodOut(WoodBase):
    """Full detail shape used by GET /woods/{id}."""

    wood_id: int
    seller_id: int
    created_at: datetime
    updated_at: datetime
    images: List[WoodImageOut] = []

    # Exposed as computed strings so the client doesn't have to
    # build them from thickness/width/length itself.
    size_label: str
    display_name: str

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# ORDER ITEMS
# ============================================================
class OrderItemIn(BaseModel):
    wood_id: int
    quantity: int = Field(gt=0)


class OrderItemOut(BaseModel):
    order_item_id: int
    wood_id: int
    wood_name_snap: str
    size_snap: str
    quantity: int
    price_at_purchase: Decimal

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# ORDERS
# ============================================================
class OrderCreate(BaseModel):
    seller_id: int
    delivery_address: str
    distance_km: Decimal
    vehicle_id: Optional[int] = None
    notes: Optional[str] = None
    items: List[OrderItemIn] = Field(min_length=1)


class OrderStatusUpdate(BaseModel):
    status: str


class OrderOut(BaseModel):
    order_id: int
    customer_id: int
    seller_id: int
    delivery_address: str
    distance_km: Decimal
    vehicle_id: Optional[int] = None
    total_pieces: int
    subtotal: Decimal
    delivery_fee: Decimal
    total_price: Decimal
    currency: str
    status: str
    notes: Optional[str] = None
    order_date: datetime
    items: List[OrderItemOut] = []

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# CONVERSATIONS / MESSAGES
# ============================================================
class ConversationCreate(BaseModel):
    seller_id: int
    wood_id: Optional[int] = None


class MessageCreate(BaseModel):
    message_text: str = Field(min_length=1)


class MessageOut(BaseModel):
    message_id: int
    conversation_id: int
    sender_id: int
    message_text: Optional[str]
    is_read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationOut(BaseModel):
    conversation_id: int
    buyer_id: int
    seller_id: int
    wood_id: Optional[int] = None
    last_message_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)