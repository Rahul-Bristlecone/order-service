from src.order_service.extentions.db import db
from datetime import datetime, UTC

# docker exec -it user-service bash
# python -m alembic revision --autogenerate -m "Added new column"
# python -m alembic upgrade head

class OrderModel(db.Model):
    __tablename__ = "orders"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "po_number", name="uq_order_business_key"
        ),
    )

    order_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=False)
    store_number = db.Column(db.Integer, nullable=False)
    customer_id = db.Column(db.Integer, nullable=True)
    company_id = db.Column(db.Integer, nullable=True)
    product_id = db.Column(db.Integer, nullable=True)

    customer_code = db.Column(db.String(64), nullable=False)
    company_code = db.Column(db.String(64), nullable=False)
    barcode = db.Column(db.String(64), nullable=False)
    po_number = db.Column(db.String(128), nullable=False)
    ship_by_date = db.Column(db.String(32), nullable=True)
    ordered_quantity = db.Column(db.Integer, nullable=False, default=0)
    quantity_to_deliver = db.Column(db.Integer, nullable=True)
    quantity_delivered = db.Column(db.Integer, nullable=True)
    tax_rate = db.Column(db.Float, nullable=False, default=0)
    unit_price = db.Column(db.Float, nullable=False, default=0)
    poa_status = db.Column(db.Integer, nullable=False, default=0)
    asn_status = db.Column(db.Integer, nullable=False, default=0)
    invoice_status = db.Column(db.Integer, nullable=False, default=0)

    order_status = db.Column(
        db.Enum("pending", "outstanding", "processing", "shipped", "delivered", "cancelled", "rejected", name="order_status_enum"),
        default="pending",
        nullable=False
    )

    total_amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default="INR", nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)