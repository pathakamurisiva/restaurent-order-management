from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import os

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_migrate import Migrate
from sqlalchemy import text

from config import Config
from models import (
    ORDER_STATUSES,
    db,
    Category,
    MenuItem,
    Order,
    OrderItem,
    TableInfo,
)


app = Flask(__name__)
app.config.from_object(Config)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///restaurant.db"
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

db.init_app(app)
migrate = Migrate(app, db)


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value))


def compute_order_subtotal(order: Order) -> Decimal:
    subtotal = Decimal("0.00")
    for oi in order.order_items:
        subtotal += _to_decimal(oi.subtotal)
    return subtotal.quantize(Decimal("0.01"))


def recalc_order_total(order: Order) -> None:
    order.total_amount = compute_order_subtotal(order)
    db.session.add(order)


def seed_data() -> None:
    categories = ["Starters", "Main Course", "Beverages", "Desserts"]
    category_objs = []
    for name in categories:
        category_objs.append(Category(name=name))
    db.session.add_all(category_objs)
    db.session.flush()  # get ids

    # 12 menu items across categories
    items = [
        # Starters
        ("Spring Rolls", "8.99", "Crispy veggie rolls with dipping sauce", 0),
        ("Garlic Bread", "5.99", "Toasted bread with garlic butter", 0),
        ("Caesar Salad", "10.50", "Romaine, parmesan, Caesar dressing", 0),
        # Main Course
        ("Margherita Pizza", "14.99", "Classic tomato, basil, mozzarella", 1),
        ("Grilled Chicken", "16.50", "Herb grilled chicken with seasonal sides", 1),
        ("Pasta Alfredo", "15.75", "Creamy Alfredo sauce with parmesan", 1),
        ("Beef Burger", "17.25", "Juicy beef patty, cheese, pickles, fries", 1),
        # Beverages
        ("Iced Tea", "3.50", "Fresh-brewed iced tea", 2),
        ("Lemonade", "4.00", "Chilled lemon lemonade", 2),
        # Desserts
        ("Chocolate Brownie", "6.25", "Fudgy brownie with cocoa", 3),
        ("Cheesecake", "7.50", "Creamy cheesecake with berry topping", 3),
        ("Ice Cream Scoop", "4.75", "Vanilla or chocolate scoop", 3),
    ]

    menu_items: list[MenuItem] = []
    for name, price, description, category_idx in items:
        menu_items.append(
            MenuItem(
                name=name,
                price=_to_decimal(price),
                description=description,
                category_id=category_objs[category_idx].id,
                is_available=True,
            )
        )
    db.session.add_all(menu_items)

    # 6 tables
    for table_no in range(1, 7):
        db.session.add(TableInfo(table_number=table_no, capacity=4, status="free"))

    db.session.commit()


def init_db() -> None:
    with app.app_context():
        db.create_all()
        ensure_tableinfo_columns()
        # Seed on first run
        if Category.query.first() is None:
            seed_data()


def ensure_tableinfo_columns() -> None:
    required_columns = {
        "booked_by": "TEXT",
        "booked_phone": "TEXT",
        "booked_from": "DATETIME",
        "booked_until": "DATETIME",
        "booking_note": "TEXT",
    }
    table_cols = db.session.execute(text("PRAGMA table_info(table_info)")).fetchall()
    existing = {row[1] for row in table_cols}
    for col_name, col_type in required_columns.items():
        if col_name not in existing:
            db.session.execute(text(f"ALTER TABLE table_info ADD COLUMN {col_name} {col_type}"))
    db.session.commit()


def status_badge_class(status: str) -> str:
    if status == "pending":
        return "bg-warning"
    if status == "preparing":
        return "bg-primary"
    if status == "served":
        return "bg-success"
    if status == "paid":
        return "bg-secondary"
    return "bg-light"


@app.context_processor
def _jinja_helpers():
    return {"status_badge_class": status_badge_class}


def allowed_transition(current: str, new_status: str) -> bool:
    if current == new_status:
        return True
    transitions = {
        "pending": {"preparing"},
        "preparing": {"served"},
        "served": {"paid"},
        "paid": set(),
    }
    return new_status in transitions.get(current, set())


def free_table_if_needed(order: Order) -> None:
    if order.table and order.table.status != "free":
        order.table.status = "free"
        order.table.booked_by = None
        order.table.booked_phone = None
        order.table.booked_from = None
        order.table.booked_until = None
        order.table.booking_note = None
        db.session.add(order.table)


def parse_booking_datetime(date_str: str, time_str: str) -> datetime | None:
    if not date_str or not time_str:
        return None
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def parse_datetime_local(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def apply_booking(table: TableInfo, *, booked_by: str, booked_phone: str, booked_from: datetime, booked_until: datetime, booking_note: str | None) -> None:
    table.status = "booked"
    table.booked_by = booked_by
    table.booked_phone = booked_phone
    table.booked_from = booked_from
    table.booked_until = booked_until
    table.booking_note = booking_note


@app.route("/")
def dashboard():
    today = date.today()
    start_dt = datetime.combine(today, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)

    total_orders_today = Order.query.filter(Order.created_at >= start_dt, Order.created_at < end_dt).count()
    paid_orders_today = Order.query.filter(
        Order.status == "paid", Order.created_at >= start_dt, Order.created_at < end_dt
    ).all()

    gst_rate = float(app.config["GST_RATE"])
    todays_revenue = sum([o.grand_total(gst_rate) for o in paid_orders_today], Decimal("0.00")).quantize(
        Decimal("0.01")
    )

    pending_orders_count = Order.query.filter(Order.status == "pending").count()
    tables_occupied = TableInfo.query.filter(TableInfo.status == "occupied").count()

    return render_template(
        "index.html",
        total_orders_today=total_orders_today,
        todays_revenue=todays_revenue,
        pending_orders_count=pending_orders_count,
        tables_occupied=tables_occupied,
    )


@app.route("/menu")
def menu():
    q = request.args.get("q", "").strip()
    items_query = MenuItem.query
    if q:
        items_query = items_query.filter(MenuItem.name.ilike(f"%{q}%"))
    items_query = items_query.order_by(MenuItem.category_id, MenuItem.name)

    categories = Category.query.order_by(Category.id).all()
    items = items_query.all()
    by_category: dict[int, list[MenuItem]] = {c.id: [] for c in categories}
    for item in items:
        by_category.setdefault(item.category_id, []).append(item)

    return render_template("menu.html", categories=categories, by_category=by_category, q=q)


@app.route("/menu/add", methods=["GET", "POST"])
def menu_add():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        price = request.form.get("price", "").strip()
        description = request.form.get("description", "").strip()
        category_id = request.form.get("category_id")
        is_available = request.form.get("is_available") == "on"

        if not name or not price or not category_id:
            flash("Name, price, and category are required.", "error")
            return redirect(url_for("menu_add"))

        try:
            price_dec = _to_decimal(price)
        except Exception:
            flash("Invalid price.", "error")
            return redirect(url_for("menu_add"))

        item = MenuItem(
            name=name,
            price=price_dec,
            description=description,
            category_id=int(category_id),
            is_available=is_available,
        )
        db.session.add(item)
        db.session.commit()
        flash("Menu item added successfully.", "success")
        return redirect(url_for("menu"))

    categories = Category.query.order_by(Category.id).all()
    return render_template("menu_add.html", categories=categories)


@app.route("/menu/edit/<int:item_id>", methods=["GET", "POST"])
def menu_edit(item_id: int):
    item = MenuItem.query.get_or_404(item_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        price = request.form.get("price", "").strip()
        description = request.form.get("description", "").strip()
        category_id = request.form.get("category_id")
        is_available = request.form.get("is_available") == "on"

        if not name or not price or not category_id:
            flash("Name, price, and category are required.", "error")
            return redirect(url_for("menu_edit", item_id=item_id))

        try:
            price_dec = _to_decimal(price)
        except Exception:
            flash("Invalid price.", "error")
            return redirect(url_for("menu_edit", item_id=item_id))

        item.name = name
        item.price = price_dec
        item.description = description
        item.category_id = int(category_id)
        item.is_available = is_available
        db.session.add(item)
        db.session.commit()
        flash("Menu item updated successfully.", "success")
        return redirect(url_for("menu"))

    categories = Category.query.order_by(Category.id).all()
    return render_template("menu_edit.html", item=item, categories=categories)


@app.route("/menu/delete/<int:item_id>", methods=["POST"])
def menu_delete(item_id: int):
    item = MenuItem.query.get_or_404(item_id)
    used = OrderItem.query.filter_by(menu_item_id=item_id).first() is not None
    if used:
        flash("Cannot delete menu item: it is referenced by existing orders.", "error")
        return redirect(url_for("menu"))
    db.session.delete(item)
    db.session.commit()
    flash("Menu item deleted.", "success")
    return redirect(url_for("menu"))


@app.route("/menu/toggle/<int:item_id>", methods=["POST"])
def menu_toggle(item_id: int):
    item = MenuItem.query.get_or_404(item_id)
    item.is_available = not item.is_available
    db.session.add(item)
    db.session.commit()
    flash("Menu item availability updated.", "success")
    return redirect(url_for("menu"))


@app.route("/tables")
def tables():
    tables_list = TableInfo.query.order_by(TableInfo.table_number).all()
    return render_template("tables.html", tables_list=tables_list)


@app.route("/customer")
def customer_tables():
    tables_list = TableInfo.query.order_by(TableInfo.table_number).all()
    return render_template("customer.html", tables_list=tables_list)


@app.route("/customer/availability")
def customer_availability():
    tables_list = TableInfo.query.order_by(TableInfo.table_number).all()
    return render_template("customer.html", tables_list=tables_list)


@app.route("/customer/book/<int:table_id>", methods=["POST"])
def customer_book_table(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    if table.status != "free":
        flash(f"Table {table.table_number} is not available right now.", "error")
        return redirect(url_for("customer_tables"))

    name = request.form.get("booked_by", "").strip()
    phone = request.form.get("booked_phone", "").strip()
    booking_date = request.form.get("booking_date", "").strip()
    from_time = request.form.get("booked_from_time", "").strip()
    until_time = request.form.get("booked_until_time", "").strip()
    note = request.form.get("booking_note", "").strip() or None

    booked_from = parse_booking_datetime(booking_date, from_time)
    booked_until = parse_booking_datetime(booking_date, until_time)

    if not name or not phone:
        flash("Name and phone are required.", "error")
        return redirect(url_for("customer_tables"))
    if not booked_from or not booked_until or booked_until <= booked_from:
        flash("Please provide a valid booking time range.", "error")
        return redirect(url_for("customer_tables"))

    apply_booking(
        table,
        booked_by=name,
        booked_phone=phone,
        booked_from=booked_from,
        booked_until=booked_until,
        booking_note=note,
    )
    db.session.add(table)
    db.session.commit()
    flash(f"Table {table.table_number} booked successfully! We'll confirm on your phone.", "success")
    return redirect(url_for("customer_tables"))


@app.route("/tables/add", methods=["GET"])
def tables_add_get():
    return redirect(url_for("tables", modal="add"))


@app.route("/tables/add", methods=["POST"])
def tables_add_post():
    try:
        table_number, capacity, status = _validate_table_payload(request.form)
    except Exception as e:
        flash(str(e), "error")
        return redirect(url_for("tables"))

    existing = TableInfo.query.filter_by(table_number=table_number).first()
    if existing:
        flash("Table number already exists.", "error")
        return redirect(url_for("tables"))

    t = TableInfo(table_number=table_number, capacity=capacity, status=status)
    db.session.add(t)
    db.session.commit()
    flash("Table added successfully.", "success")
    return redirect(url_for("tables"))


@app.route("/tables/edit/<int:table_id>", methods=["GET"])
def tables_edit_get(table_id: int):
    return redirect(url_for("tables", modal="edit", table_id=table_id))


@app.route("/tables/edit/<int:table_id>", methods=["POST"])
def tables_edit_post(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    try:
        table_number, capacity, status = _validate_table_payload(request.form)
    except Exception as e:
        flash(str(e), "error")
        return redirect(url_for("tables"))

    # Ensure uniqueness if the table_number is changed
    if table.table_number != table_number:
        if TableInfo.query.filter_by(table_number=table_number).first():
            flash("Table number already exists.", "error")
            return redirect(url_for("tables"))

    table.table_number = table_number
    table.capacity = capacity
    table.status = status
    if status == "free":
        table.booked_by = None
        table.booked_phone = None
        table.booked_from = None
        table.booked_until = None
        table.booking_note = None
    db.session.add(table)
    db.session.commit()
    flash("Table updated successfully.", "success")
    return redirect(url_for("tables"))


@app.route("/tables/delete/<int:table_id>", methods=["POST"])
def tables_delete_post(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    used = Order.query.filter_by(table_id=table.id).first() is not None
    if used:
        flash("Cannot delete table: it is referenced by existing orders.", "error")
        return redirect(url_for("tables"))

    db.session.delete(table)
    db.session.commit()
    flash("Table deleted successfully.", "success")
    return redirect(url_for("tables"))


@app.route("/tables/book/<int:table_id>", methods=["GET"])
def tables_book_get(table_id: int):
    return redirect(url_for("tables", modal="book", table_id=table_id))


@app.route("/tables/book/<int:table_id>", methods=["POST"])
def tables_book_post(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    booked_by = request.form.get("booked_by", "").strip()
    booked_phone = request.form.get("booked_phone", "").strip()
    booked_from_str = request.form.get("booked_from", "").strip()
    booked_until_str = request.form.get("booked_until", "").strip()
    note = request.form.get("booking_note", "").strip() or None

    booked_from = parse_datetime_local(booked_from_str)
    booked_until = parse_datetime_local(booked_until_str)
    if not booked_by or not booked_phone:
        flash("Customer name and phone are required.", "error")
        return redirect(url_for("tables"))
    if not booked_from or not booked_until or booked_until <= booked_from:
        flash("Invalid booked from/until values.", "error")
        return redirect(url_for("tables"))

    apply_booking(
        table,
        booked_by=booked_by,
        booked_phone=booked_phone,
        booked_from=booked_from,
        booked_until=booked_until,
        booking_note=note,
    )
    db.session.add(table)
    db.session.commit()
    flash(f"Booking saved for Table {table.table_number}.", "success")
    return redirect(url_for("tables"))


@app.route("/tables/unbook/<int:table_id>", methods=["POST"])
def tables_unbook_post(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    table.status = "free"
    table.booked_by = None
    table.booked_phone = None
    table.booked_from = None
    table.booked_until = None
    table.booking_note = None
    db.session.add(table)
    db.session.commit()
    flash(f"Table {table.table_number} is now free.", "success")
    return redirect(url_for("tables"))


@app.route("/tables/extend/<int:table_id>", methods=["POST"])
def tables_extend_post(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    if not table.booked_until:
        flash("This table does not have a booking end time to extend.", "error")
        return redirect(url_for("tables"))
    table.booked_until = table.booked_until + timedelta(minutes=30)
    db.session.add(table)
    db.session.commit()
    flash(f"Extended Table {table.table_number} booking by 30 minutes.", "success")
    return redirect(url_for("tables"))


@app.route("/order/new/<int:table_id>", methods=["GET"])
def order_new_get(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    if table.status != "free":
        flash("Table is already occupied. Select a free table.", "error")
        return redirect(url_for("tables"))
    categories = Category.query.order_by(Category.id).all()
    items = MenuItem.query.order_by(MenuItem.category_id, MenuItem.name).all()
    by_category: dict[int, list[MenuItem]] = {}
    for item in items:
        by_category.setdefault(item.category_id, []).append(item)
    return render_template("order_new.html", table=table, categories=categories, by_category=by_category)


@app.route("/order/new/<int:table_id>", methods=["POST"])
def order_new_post(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    if table.status != "free":
        flash("Table is already occupied. Select a free table.", "error")
        return redirect(url_for("tables"))

    quantities: dict[int, int] = {}
    for key in request.form.keys():
        if key.startswith("qty_"):
            try:
                menu_item_id = int(key.replace("qty_", ""))
                qty = int(request.form.get(key, "0") or "0")
                if qty > 0:
                    quantities[menu_item_id] = qty
            except ValueError:
                continue

    if not quantities:
        flash("Add at least one item to place an order.", "error")
        return redirect(url_for("order_new_get", table_id=table_id))

    order = Order(table_id=table_id, status="pending", created_at=datetime.utcnow(), total_amount=Decimal("0.00"))
    db.session.add(order)
    db.session.flush()  # obtain order.id

    subtotal = Decimal("0.00")
    created_any = False
    for menu_item_id, qty in quantities.items():
        menu_item = MenuItem.query.get(menu_item_id)
        if not menu_item or not menu_item.is_available:
            continue
        created_any = True
        oi_subtotal = _to_decimal(menu_item.price) * qty
        subtotal += oi_subtotal
        db.session.add(
            OrderItem(
                order_id=order.id,
                menu_item_id=menu_item_id,
                quantity=qty,
                subtotal=oi_subtotal.quantize(Decimal("0.01")),
            )
        )

    order.total_amount = subtotal.quantize(Decimal("0.01"))

    # Occupy table only if we created at least one valid item
    if not created_any:
        db.session.rollback()
        flash("No valid/available items were selected.", "error")
        return redirect(url_for("order_new_get", table_id=table_id))

    table.status = "occupied"
    db.session.add(table)
    db.session.add(order)
    db.session.commit()
    flash("Order placed successfully.", "success")
    return redirect(url_for("orders"))


@app.route("/orders")
def orders():
    status = request.args.get("status", "").strip()
    date_str = request.args.get("date", "").strip()

    query = Order.query.order_by(Order.created_at.desc())
    if status and status in ORDER_STATUSES:
        query = query.filter(Order.status == status)

    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            start_dt = datetime.combine(d, datetime.min.time())
            end_dt = start_dt + timedelta(days=1)
            query = query.filter(Order.created_at >= start_dt, Order.created_at < end_dt)
        except ValueError:
            flash("Invalid date format. Use YYYY-MM-DD.", "error")

    orders_list = query.all()
    return render_template("orders.html", orders_list=orders_list, status=status, date_str=date_str, statuses=ORDER_STATUSES)


@app.route("/order/<int:order_id>")
def order_detail(order_id: int):
    order = Order.query.get_or_404(order_id)
    order_items = order.order_items
    available_menu_items = MenuItem.query.filter(MenuItem.is_available.is_(True)).order_by(MenuItem.name).all()
    return render_template(
        "order_detail.html",
        order=order,
        order_items=order_items,
        statuses=ORDER_STATUSES,
        available_menu_items=available_menu_items,
    )


@app.route("/order/<int:order_id>/status", methods=["POST"])
def order_status_update(order_id: int):
    order = Order.query.get_or_404(order_id)
    new_status = request.form.get("status", "").strip()

    if new_status not in ORDER_STATUSES:
        flash("Invalid order status.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    if not allowed_transition(order.status, new_status):
        flash("That status transition is not allowed.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    if order.status == "paid":
        flash("Paid orders cannot be changed.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    order.status = new_status

    if new_status == "paid":
        free_table_if_needed(order)

    db.session.add(order)
    db.session.commit()

    flash("Order status updated.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/order/<int:order_id>/add-item", methods=["POST"])
def order_add_item(order_id: int):
    order = Order.query.get_or_404(order_id)
    if order.status == "paid":
        flash("Paid orders cannot be modified.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    menu_item_id = request.form.get("menu_item_id")
    qty_str = request.form.get("quantity", "1")
    try:
        qty = int(qty_str)
    except ValueError:
        qty = 0
    if not menu_item_id or qty <= 0:
        flash("Provide a valid item and quantity.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    menu_item = MenuItem.query.get_or_404(int(menu_item_id))
    if not menu_item.is_available:
        flash("Selected item is unavailable.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    existing = OrderItem.query.filter_by(order_id=order_id, menu_item_id=menu_item.id).first()
    if existing:
        existing.quantity += qty
        existing.subtotal = (_to_decimal(menu_item.price) * existing.quantity).quantize(Decimal("0.01"))
        db.session.add(existing)
    else:
        db.session.add(
            OrderItem(
                order_id=order_id,
                menu_item_id=menu_item.id,
                quantity=qty,
                subtotal=(_to_decimal(menu_item.price) * qty).quantize(Decimal("0.01")),
            )
        )

    recalc_order_total(order)
    db.session.add(order)
    db.session.commit()
    flash("Item added to order.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/order/<int:order_id>/remove-item", methods=["POST"])
def order_remove_item(order_id: int):
    order = Order.query.get_or_404(order_id)
    if order.status == "paid":
        flash("Paid orders cannot be modified.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    menu_item_id = request.form.get("menu_item_id")
    qty_str = request.form.get("quantity", "1")
    remove_qty = 1
    try:
        remove_qty = int(qty_str)
    except ValueError:
        remove_qty = 1

    if not menu_item_id:
        flash("Select an item to remove.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    qty_item = OrderItem.query.filter_by(order_id=order_id, menu_item_id=int(menu_item_id)).first()
    if not qty_item:
        flash("Item not found on this order.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    if remove_qty >= qty_item.quantity:
        db.session.delete(qty_item)
    else:
        qty_item.quantity -= remove_qty
        menu_item = MenuItem.query.get(qty_item.menu_item_id)
        qty_item.subtotal = (_to_decimal(menu_item.price) * qty_item.quantity).quantize(Decimal("0.01"))
        db.session.add(qty_item)

    recalc_order_total(order)
    db.session.add(order)
    db.session.commit()
    flash("Item updated/removed.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/order/<int:order_id>/bill")
def order_bill(order_id: int):
    order = Order.query.get_or_404(order_id)
    gst_rate = float(app.config["GST_RATE"])
    subtotal = _to_decimal(order.total_amount).quantize(Decimal("0.01"))
    gst_amount = (subtotal * _to_decimal(app.config["GST_RATE"])).quantize(Decimal("0.01"))
    grand_total = (subtotal + gst_amount).quantize(Decimal("0.01"))

    return render_template(
        "bill.html",
        order=order,
        gst_rate=gst_rate,
        subtotal=subtotal,
        gst_amount=gst_amount,
        grand_total=grand_total,
    )


@app.route("/order/<int:order_id>/pay", methods=["POST"])
def order_pay(order_id: int):
    order = Order.query.get_or_404(order_id)
    if order.status == "paid":
        flash("Order already paid.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    if order.status != "served":
        flash("Only served orders can be marked as paid.", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    order.status = "paid"
    free_table_if_needed(order)
    db.session.add(order)
    db.session.commit()
    flash("Payment successful. Table is now free.", "success")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/kitchen")
def kitchen():
    kitchen_orders = Order.query.filter(Order.status.in_(["pending", "preparing"])).order_by(Order.created_at.asc()).all()

    now = datetime.utcnow()
    elapsed = {}
    for order in kitchen_orders:
        seconds = int((now - order.created_at).total_seconds())
        elapsed[order.id] = seconds

    return render_template("kitchen.html", orders=kitchen_orders, elapsed=elapsed, now=now)


def _validate_table_payload(payload: dict) -> tuple[int, int, str]:
    table_number = payload.get("table_number")
    capacity = payload.get("capacity")
    status = (payload.get("status") or "").strip().lower()

    if table_number is None:
        raise ValueError("table_number is required")
    if capacity is None:
        raise ValueError("capacity is required")
    if status not in {"free", "occupied", "booked"}:
        raise ValueError("status must be free, occupied, or booked")

    table_number = int(table_number)
    capacity = int(capacity)
    if capacity not in {2, 4, 6, 8, 10}:
        raise ValueError("capacity must be one of: 2, 4, 6, 8, 10")
    return table_number, capacity, status


def serialize_menu_item(item: MenuItem) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "price": str(item.price),
        "description": item.description,
        "category_id": item.category_id,
        "is_available": bool(item.is_available),
    }


def serialize_order(order: Order) -> dict:
    gst_rate = float(app.config["GST_RATE"])
    subtotal = _to_decimal(order.total_amount).quantize(Decimal("0.01"))
    gst_amount = (subtotal * _to_decimal(app.config["GST_RATE"])).quantize(Decimal("0.01"))
    return {
        "id": order.id,
        "table_id": order.table_id,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "subtotal_amount": str(subtotal),
        "gst_amount": str(gst_amount),
        "grand_total": str((subtotal + gst_amount).quantize(Decimal("0.01"))),
        "items": [
            {
                "id": oi.id,
                "menu_item_id": oi.menu_item_id,
                "menu_item_name": oi.menu_item.name,
                "quantity": oi.quantity,
                "subtotal": str(oi.subtotal),
            }
            for oi in order.order_items
        ],
    }


# ---------------------------
# REST API: Menu
# ---------------------------
@app.get("/api/menu")
def api_menu_list():
    items = MenuItem.query.order_by(MenuItem.category_id, MenuItem.name).all()
    return jsonify({"items": [serialize_menu_item(i) for i in items]})


@app.get("/api/menu/<int:item_id>")
def api_menu_get(item_id: int):
    item = MenuItem.query.get_or_404(item_id)
    return jsonify(serialize_menu_item(item))


@app.post("/api/menu")
def api_menu_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip() or None
    category_id = data.get("category_id")
    price = data.get("price")
    is_available = bool(data.get("is_available", True))

    if not name or category_id is None or price is None:
        return jsonify({"error": "name, price, and category_id are required"}), 400

    try:
        price_dec = _to_decimal(price)
    except Exception:
        return jsonify({"error": "Invalid price"}), 400

    item = MenuItem(name=name, description=description, category_id=int(category_id), price=price_dec, is_available=is_available)
    db.session.add(item)
    db.session.commit()
    return jsonify(serialize_menu_item(item)), 201


@app.put("/api/menu/<int:item_id>")
def api_menu_update(item_id: int):
    item = MenuItem.query.get_or_404(item_id)
    data = request.get_json(silent=True) or {}

    if "name" in data:
        item.name = (data.get("name") or "").strip() or item.name
    if "description" in data:
        item.description = (data.get("description") or "").strip() or None
    if "category_id" in data:
        item.category_id = int(data.get("category_id"))
    if "price" in data:
        try:
            item.price = _to_decimal(data.get("price"))
        except Exception:
            return jsonify({"error": "Invalid price"}), 400
    if "is_available" in data:
        item.is_available = bool(data.get("is_available"))

    db.session.add(item)
    db.session.commit()
    return jsonify(serialize_menu_item(item))


@app.delete("/api/menu/<int:item_id>")
def api_menu_delete(item_id: int):
    item = MenuItem.query.get_or_404(item_id)
    used = OrderItem.query.filter_by(menu_item_id=item_id).first() is not None
    if used:
        return jsonify({"error": "Cannot delete menu item: it is referenced by existing orders"}), 409
    db.session.delete(item)
    db.session.commit()
    return jsonify({"success": True})


# ---------------------------
# REST API: Tables
# ---------------------------
@app.get("/api/tables")
def api_tables():
    tables_q = TableInfo.query.order_by(TableInfo.table_number).all()
    return jsonify(
        {
            "tables": [
                {
                    "id": t.id,
                    "table_number": t.table_number,
                    "capacity": t.capacity,
                    "status": t.status,
                    "booked_by": t.booked_by,
                    "booked_phone": t.booked_phone,
                    "booked_from": t.booked_from.isoformat() if t.booked_from else None,
                    "booked_until": t.booked_until.isoformat() if t.booked_until else None,
                    "booking_note": t.booking_note,
                }
                for t in tables_q
            ]
        }
    )


@app.get("/api/tables/availability")
def api_tables_availability():
    tables_q = TableInfo.query.order_by(TableInfo.table_number).all()
    payload = []
    for t in tables_q:
        payload.append(
            {
                "id": t.id,
                "table_number": t.table_number,
                "capacity": t.capacity,
                "status": t.status,
                "booked_by": t.booked_by,
                "booked_phone": t.booked_phone,
                "booked_from": t.booked_from.isoformat() if t.booked_from else None,
                "booked_until": t.booked_until.isoformat() if t.booked_until else None,
                "booking_note": t.booking_note,
            }
        )
    return jsonify(payload)


@app.post("/api/tables")
def api_tables_create():
    data = request.get_json(silent=True) or {}
    try:
        table_number, capacity, status = _validate_table_payload(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if TableInfo.query.filter_by(table_number=table_number).first():
        return jsonify({"error": "Table number already exists"}), 409

    t = TableInfo(table_number=table_number, capacity=capacity, status=status)
    db.session.add(t)
    db.session.commit()
    return jsonify({"id": t.id, "table_number": t.table_number, "capacity": t.capacity, "status": t.status}), 201


@app.put("/api/tables/<int:table_id>")
def api_tables_update(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    data = request.get_json(silent=True) or {}
    try:
        table_number, capacity, status = _validate_table_payload(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if table.table_number != table_number and TableInfo.query.filter_by(table_number=table_number).first():
        return jsonify({"error": "Table number already exists"}), 409

    table.table_number = table_number
    table.capacity = capacity
    table.status = status
    if status == "free":
        table.booked_by = None
        table.booked_phone = None
        table.booked_from = None
        table.booked_until = None
        table.booking_note = None
    db.session.add(table)
    db.session.commit()
    return jsonify({"id": table.id, "table_number": table.table_number, "capacity": table.capacity, "status": table.status})


@app.put("/api/tables/<int:table_id>/book")
def api_tables_book(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    data = request.get_json(silent=True) or {}
    booked_by = (data.get("booked_by") or "").strip()
    booked_phone = (data.get("booked_phone") or "").strip()
    booked_from = parse_datetime_local((data.get("booked_from") or "").strip())
    booked_until = parse_datetime_local((data.get("booked_until") or "").strip())
    booking_note = (data.get("booking_note") or "").strip() or None

    if not booked_by or not booked_phone:
        return jsonify({"error": "booked_by and booked_phone are required"}), 400
    if not booked_from or not booked_until or booked_until <= booked_from:
        return jsonify({"error": "Invalid booking time range"}), 400

    apply_booking(
        table,
        booked_by=booked_by,
        booked_phone=booked_phone,
        booked_from=booked_from,
        booked_until=booked_until,
        booking_note=booking_note,
    )
    db.session.add(table)
    db.session.commit()
    return jsonify({"success": True, "id": table.id, "status": table.status})


@app.put("/api/tables/<int:table_id>/free")
def api_tables_free(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    table.status = "free"
    table.booked_by = None
    table.booked_phone = None
    table.booked_from = None
    table.booked_until = None
    table.booking_note = None
    db.session.add(table)
    db.session.commit()
    return jsonify({"success": True, "id": table.id, "status": table.status})


@app.delete("/api/tables/<int:table_id>")
def api_tables_delete(table_id: int):
    table = TableInfo.query.get_or_404(table_id)
    used = Order.query.filter_by(table_id=table.id).first() is not None
    if used:
        return jsonify({"error": "Cannot delete table: it is referenced by existing orders"}), 409
    db.session.delete(table)
    db.session.commit()
    return jsonify({"success": True})


# ---------------------------
# REST API: Orders
# ---------------------------
@app.get("/api/orders")
def api_orders_list():
    orders_q = Order.query.order_by(Order.created_at.desc()).all()
    return jsonify({"orders": [serialize_order(o) for o in orders_q]})


@app.get("/api/orders/<int:order_id>")
def api_order_get(order_id: int):
    order = Order.query.get_or_404(order_id)
    return jsonify(serialize_order(order))


@app.post("/api/orders")
def api_order_create():
    data = request.get_json(silent=True) or {}
    table_id = data.get("table_id")
    items = data.get("items") or []

    if table_id is None or not isinstance(items, list):
        return jsonify({"error": "table_id and items[] are required"}), 400

    table = TableInfo.query.get_or_404(int(table_id))
    if table.status != "free":
        return jsonify({"error": "Table is not free"}), 409

    order = Order(table_id=table.id, status="pending", created_at=datetime.utcnow(), total_amount=Decimal("0.00"))
    db.session.add(order)
    db.session.flush()

    subtotal = Decimal("0.00")
    created_any = False
    for it in items:
        try:
            menu_item_id = int(it.get("menu_item_id"))
            qty = int(it.get("quantity"))
        except Exception:
            return jsonify({"error": "Each item needs menu_item_id and integer quantity"}), 400
        if qty <= 0:
            continue
        menu_item = MenuItem.query.get(menu_item_id)
        if not menu_item or not menu_item.is_available:
            continue
        created_any = True
        oi_sub = (_to_decimal(menu_item.price) * qty).quantize(Decimal("0.01"))
        subtotal += oi_sub
        db.session.add(OrderItem(order_id=order.id, menu_item_id=menu_item_id, quantity=qty, subtotal=oi_sub))

    if not created_any:
        db.session.rollback()
        return jsonify({"error": "No valid/available items to create an order"}), 400

    order.total_amount = subtotal.quantize(Decimal("0.01"))
    table.status = "occupied"
    db.session.add(table)
    db.session.add(order)
    db.session.commit()

    return jsonify(serialize_order(order)), 201


@app.put("/api/orders/<int:order_id>")
def api_order_update(order_id: int):
    order = Order.query.get_or_404(order_id)
    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip()
    if new_status not in ORDER_STATUSES:
        return jsonify({"error": "Invalid status"}), 400

    if not allowed_transition(order.status, new_status):
        return jsonify({"error": "That status transition is not allowed"}), 409

    order.status = new_status
    if new_status == "paid":
        free_table_if_needed(order)
    db.session.add(order)
    db.session.commit()
    return jsonify(serialize_order(order))


@app.delete("/api/orders/<int:order_id>")
def api_order_delete(order_id: int):
    order = Order.query.get_or_404(order_id)
    table = order.table
    db.session.delete(order)
    if table:
        table.status = "free"
        db.session.add(table)
    db.session.commit()
    return jsonify({"success": True})


# ---------------------------
# REST API: Dashboard
# ---------------------------
@app.get("/api/dashboard/stats")
def api_dashboard_stats():
    today = date.today()
    start_dt = datetime.combine(today, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)

    paid_today = (
        Order.query.filter(Order.status == "paid", Order.created_at >= start_dt, Order.created_at < end_dt).all()
    )
    gst_rate = float(app.config["GST_RATE"])
    revenue = sum([o.grand_total(gst_rate) for o in paid_today], Decimal("0.00")).quantize(Decimal("0.01"))

    order_count = Order.query.filter(Order.created_at >= start_dt, Order.created_at < end_dt).count()
    pending_count = Order.query.filter(Order.status == "pending").count()

    top_items = (
        db.session.query(OrderItem.menu_item_id, db.func.sum(OrderItem.quantity).label("qty"))
        .join(Order, OrderItem.order_id == Order.id)
        .filter(Order.status == "paid", Order.created_at >= start_dt, Order.created_at < end_dt)
        .group_by(OrderItem.menu_item_id)
        .order_by(db.func.sum(OrderItem.quantity).desc())
        .limit(5)
        .all()
    )
    top_items_payload = []
    for menu_item_id, qty in top_items:
        mi = MenuItem.query.get(menu_item_id)
        if not mi:
            continue
        top_items_payload.append(
            {
                "menu_item_id": menu_item_id,
                "name": mi.name,
                "quantity": int(qty or 0),
            }
        )

    return jsonify(
        {
            "todays_revenue": str(revenue),
            "order_count_today": order_count,
            "pending_orders_count": pending_count,
            "top_items": top_items_payload,
        }
    )


@app.errorhandler(404)
def not_found(_e):
    return render_template("base.html", content="Not found"), 404


if __name__ == "__main__":
    init_db()
    app.run(port=5000, debug=True)

