from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


ORDER_STATUSES = ("pending", "preparing", "served", "paid")


class Category(db.Model):
    __tablename__ = "category"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

    menu_items = db.relationship("MenuItem", back_populates="category", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Category {self.id} {self.name}>"


class MenuItem(db.Model):
    __tablename__ = "menu_item"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    description = db.Column(db.Text, nullable=True)

    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    is_available = db.Column(db.Boolean, nullable=False, default=True)

    category = db.relationship("Category", back_populates="menu_items")
    order_items = db.relationship("OrderItem", back_populates="menu_item", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<MenuItem {self.id} {self.name}>"


class TableInfo(db.Model):
    __tablename__ = "table_info"

    id = db.Column(db.Integer, primary_key=True)
    table_number = db.Column(db.Integer, unique=True, nullable=False)
    capacity = db.Column(db.Integer, nullable=False, default=2)
    status = db.Column(db.String(20), nullable=False, default="free")  # free/occupied/booked

    # Booking metadata
    booked_by = db.Column(db.String(150), nullable=True)
    booked_phone = db.Column(db.String(30), nullable=True)
    booked_from = db.Column(db.DateTime, nullable=True)
    booked_until = db.Column(db.DateTime, nullable=True)
    booking_note = db.Column(db.Text, nullable=True)

    orders = db.relationship("Order", back_populates="table", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<TableInfo {self.table_number} {self.status}>"


class Order(db.Model):
    __tablename__ = "order"

    id = db.Column(db.Integer, primary_key=True)
    table_id = db.Column(db.Integer, db.ForeignKey("table_info.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Stored as subtotal excluding GST (as per assignment field name).
    total_amount = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))

    order_items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    table = db.relationship("TableInfo", back_populates="orders")

    @property
    def subtotal_amount(self) -> Decimal:
        return Decimal(self.total_amount)

    def gst_amount(self, gst_rate: float) -> Decimal:
        return (self.subtotal_amount * Decimal(str(gst_rate))).quantize(Decimal("0.01"))

    def grand_total(self, gst_rate: float) -> Decimal:
        return (self.subtotal_amount + self.gst_amount(gst_rate)).quantize(Decimal("0.01"))

    def __repr__(self) -> str:
        return f"<Order {self.id} {self.status}>"


class OrderItem(db.Model):
    __tablename__ = "order_item"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    menu_item_id = db.Column(db.Integer, db.ForeignKey("menu_item.id"), nullable=False)

    quantity = db.Column(db.Integer, nullable=False, default=1)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))

    order = db.relationship("Order", back_populates="order_items")
    menu_item = db.relationship("MenuItem", back_populates="order_items")

    def __repr__(self) -> str:
        return f"<OrderItem {self.id} qty={self.quantity}>"

