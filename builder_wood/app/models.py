from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, DECIMAL, Enum, ForeignKey, Integer,
    String, Text, TIMESTAMP, UniqueConstraint, Index, CHAR, func,
)
from sqlalchemy.orm import relationship
from builder_wood.app.database import Base


class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(Enum("customer", "seller", "admin", name="user_role"), nullable=False, default="customer")
    name = Column(String(255), nullable=False)
    company_name = Column(String(255))
    email = Column(String(255), nullable=False, unique=True)
    phone_number = Column(String(20), unique=True)
    password_hash = Column(String(255), nullable=False)
    address = Column(String(255))
    location = Column(String(255))
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())
    woods = relationship("Wood", back_populates="seller", foreign_keys="Wood.seller_id", cascade="all, delete-orphan")
    vehicles = relationship("SellerVehicle", back_populates="seller", cascade="all, delete-orphan")
    orders_as_customer = relationship("Order", back_populates="customer", foreign_keys="Order.customer_id")
    orders_as_seller = relationship("Order", back_populates="seller", foreign_keys="Order.seller_id")
    conversations_as_buyer = relationship("Conversation", back_populates="buyer", foreign_keys="Conversation.buyer_id",
                                          cascade="all, delete-orphan")
    conversations_as_seller = relationship("Conversation", back_populates="seller",
                                           foreign_keys="Conversation.seller_id", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="sender")


class Category(Base):
    __tablename__ = "categories"
    category_id = Column(Integer, primary_key=True, autoincrement=True)
    category_name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    parent_category = Column(Integer, ForeignKey("categories.category_id", ondelete="SET NULL"))
    description = Column(String(255))
    display_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    parent = relationship("Category", remote_side=[category_id], backref="children")
    woods = relationship("Wood", back_populates="category")


class Vehicle(Base):
    __tablename__ = "vehicles"
    vehicle_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    max_pieces = Column(Integer, nullable=False)
    max_length_ft = Column(DECIMAL(6, 2), nullable=False)
    max_distance_km = Column(DECIMAL(6, 2), nullable=False)
    rate_per_km = Column(DECIMAL(8, 2), nullable=False)
    base_fee = Column(DECIMAL(8, 2), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sellers = relationship("SellerVehicle", back_populates="vehicle", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="vehicle")


class SellerVehicle(Base):
    __tablename__ = "seller_vehicles"
    seller_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"), primary_key=True)
    seller = relationship("User", back_populates="vehicles")
    vehicle = relationship("Vehicle", back_populates="sellers")


class Wood(Base):
    __tablename__ = "woods"
    wood_id = Column(Integer, primary_key=True, autoincrement=True)
    local_name = Column(String(100), nullable=False)
    scientific_name = Column(String(150))
    trade_name = Column(String(100))
    description = Column(Text)
    durability = Column(String(150))
    thickness_in = Column(DECIMAL(5, 2), nullable=False)
    width_in = Column(DECIMAL(5, 2), nullable=False)
    length_ft = Column(DECIMAL(6, 2), nullable=False)
    product_form = Column(Enum("Sawn Timber", "Ceiling Batten", "T&G Board", "Moulding", "Plinth", name="product_form"),
                          nullable=False, default="Sawn Timber")
    is_treated = Column(Boolean, nullable=False, default=False)
    currency = Column(CHAR(3), nullable=False, default="GHS")
    price_per_piece = Column(DECIMAL(12, 2), nullable=False)
    stock_quantity = Column(Integer, nullable=False, default=0)
    category_id = Column(Integer, ForeignKey("categories.category_id", ondelete="RESTRICT"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    source_market = Column(String(100), nullable=False, default="Sokoban Wood Village")
    source_town = Column(String(50), nullable=False, default="Kumasi")
    is_available = Column(Boolean, nullable=False, default=True)
    is_featured = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())
    category = relationship("Category", back_populates="woods")
    seller = relationship("User", back_populates="woods", foreign_keys=[seller_id])
    images = relationship("WoodImage", back_populates="wood", cascade="all, delete-orphan",
                          order_by="WoodImage.display_order")
    order_items = relationship("OrderItem", back_populates="wood")
    conversations = relationship("Conversation", back_populates="wood")

    @property
    def size_label(self):
        return "{}x{}x{}".format(int(self.thickness_in), int(self.width_in), int(self.length_ft))

    @property
    def display_name(self):
        return "{} {}".format(self.local_name, self.size_label)


class WoodImage(Base):
    __tablename__ = "wood_images"
    image_id = Column(Integer, primary_key=True, autoincrement=True)
    wood_id = Column(Integer, ForeignKey("woods.wood_id", ondelete="CASCADE"), nullable=False)
    image_url = Column(String(255), nullable=False)
    alt_text = Column(String(255))
    is_primary = Column(Boolean, nullable=False, default=False)
    display_order = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    wood = relationship("Wood", back_populates="images")


class Order(Base):
    __tablename__ = "orders"
    order_id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False)
    delivery_address = Column(String(255), nullable=False)
    distance_km = Column(DECIMAL(6, 2), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.vehicle_id", ondelete="SET NULL"))
    total_pieces = Column(Integer, nullable=False, default=0)
    subtotal = Column(DECIMAL(12, 2), nullable=False, default=0)
    delivery_fee = Column(DECIMAL(10, 2), nullable=False, default=0)
    total_price = Column(DECIMAL(12, 2), nullable=False, default=0)
    currency = Column(CHAR(3), nullable=False, default="GHS")

    # UPDATED ENUM: Added "disputed" and "paid"
    status = Column(
        Enum("pending", "paid", "confirmed", "shipped", "delivered", "cancelled", "disputed", name="order_status"),
        nullable=False, default="pending"
    )
    notes = Column(Text)
    order_date = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())

    # ESCROW FIELDS
    is_funds_released = Column(Boolean, nullable=False, default=False)
    seller_payout_amount = Column(DECIMAL(12, 2), nullable=True)
    platform_commission = Column(DECIMAL(12, 2), nullable=True)

    # DISPUTE & AUTO-CONFIRM FIELDS
    auto_confirm_at = Column(TIMESTAMP, nullable=True)
    dispute_reason = Column(Text, nullable=True)
    disputed_at = Column(TIMESTAMP, nullable=True)

    customer = relationship("User", back_populates="orders_as_customer", foreign_keys=[customer_id])
    seller = relationship("User", back_populates="orders_as_seller", foreign_keys=[seller_id])
    vehicle = relationship("Vehicle", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    status_history = relationship("OrderStatusHistory", back_populates="order", cascade="all, delete-orphan",
                                  order_by="OrderStatusHistory.changed_at")
    transactions = relationship("PaymentTransaction", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"
    order_item_id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False)
    wood_id = Column(Integer, ForeignKey("woods.wood_id", ondelete="RESTRICT"), nullable=False)
    wood_name_snap = Column(String(100), nullable=False)
    size_snap = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    price_at_purchase = Column(DECIMAL(12, 2), nullable=False)
    order = relationship("Order", back_populates="items")
    wood = relationship("Wood", back_populates="order_items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    history_id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False)
    old_status = Column(String(50))
    new_status = Column(String(50), nullable=False)
    changed_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))
    changed_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    order = relationship("Order", back_populates="status_history")
    changed_by_user = relationship("User")


class Conversation(Base):
    __tablename__ = "conversations"
    conversation_id = Column(Integer, primary_key=True, autoincrement=True)
    buyer_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    wood_id = Column(Integer, ForeignKey("woods.wood_id", ondelete="SET NULL"))
    last_message_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    __table_args__ = (UniqueConstraint("buyer_id", "seller_id", "wood_id", name="uq_conv"),)
    buyer = relationship("User", back_populates="conversations_as_buyer", foreign_keys=[buyer_id])
    seller = relationship("User", back_populates="conversations_as_seller", foreign_keys=[seller_id])
    wood = relationship("Wood", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan",
                            order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"
    message_id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    message_text = Column(Text)
    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    conversation = relationship("Conversation", back_populates="messages")
    sender = relationship("User", back_populates="messages")


class CartItem(Base):
    __tablename__ = "cart_items"
    cart_item_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    wood_id = Column(Integer, ForeignKey("woods.wood_id", ondelete="CASCADE"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    user = relationship("User", backref="cart_items")
    wood = relationship("Wood")


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    transaction_id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False)
    reference = Column(String(100), unique=True, nullable=False, index=True)
    provider = Column(String(50), nullable=False, default="paystack")
    amount = Column(DECIMAL(12, 2), nullable=False)
    currency = Column(CHAR(3), nullable=False, default="GHS")
    status = Column(Enum("pending", "success", "failed", "refunded", name="payment_status"), nullable=False,
                    default="pending")
    gateway_response = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())
    order = relationship("Order", back_populates="transactions")