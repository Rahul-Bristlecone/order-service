from datetime import datetime, UTC

import pytest
from flask_jwt_extended import create_access_token

from order_service.main import create_app
from order_service.extentions.db import db
from order_service.models.order_model import OrderModel


@pytest.fixture()
def app():
    app = create_app("sqlite:///:memory:")
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def _auth_header(app):
    with app.app_context():
        token = create_access_token(identity="7")
    return {"Authorization": f"Bearer {token}"}


def _create_order(app):
    with app.app_context():
        order = OrderModel(
            user_id=7,
            store_number=1001,
            customer_id=1,
            company_id=2,
            product_id=3,
            customer_code="CUST001",
            company_code="COMP001",
            barcode="8901234567890",
            po_number="PO-UPD-1",
            ship_by_date="2026-06-20",
            ordered_quantity=2,
            quantity_to_deliver=2,
            quantity_delivered=0,
            tax_rate=18.0,
            unit_price=50.0,
            poa_status=0,
            asn_status=0,
            invoice_status=0,
            order_status="pending",
            total_amount=100.0,
            currency="INR",
            created_at=datetime.now(UTC),
        )
        db.session.add(order)
        db.session.commit()
        return order.order_id


def test_patch_order_updates_allowed_fields(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    response = client.patch(
        f"/order/{order_id}",
        json={
            "quantity_to_deliver": 1,
            "ship_by_date": "20260625",
            "price": 75.5,
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["quantity_to_deliver"] == 1
    assert body["ship_by_date"] == "2026-06-25"
    assert body["unit_price"] == 75.5
    assert body["total_amount"] == 75.5


def test_patch_order_rejects_unknown_field(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    response = client.patch(
        f"/order/{order_id}",
        json={"po_number": "NOT-ALLOWED"},
        headers=headers,
    )

    assert response.status_code == 400


def test_patch_order_ignores_extra_frontend_fields(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    response = client.patch(
        f"/order/{order_id}",
        json={
            "quantity_to_deliver": 1,
            "order_status": "processing",
            "carrier": "Xpress",
            "not_after": "2026-06-30",
            "not_before": "2026-06-10",
            "dont_pick_before": "2026-06-12",
            "comment": "UI-only note",
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["quantity_to_deliver"] == 1


def test_patch_order_updates_document_status_fields(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    response = client.patch(
        f"/order/{order_id}",
        json={
            "poa_status": 1,
            "asn_status": 1,
            "invoice_status": 1,
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["poa_status"] == 1
    assert body["asn_status"] == 1
    assert body["invoice_status"] == 1
    assert body["order_status"] == "outstanding"


def test_patch_order_accepts_camelcase_status_key(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    response = client.patch(
        f"/order/{order_id}",
        json={"poaStatus": 1},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["poa_status"] == 1


def test_get_orders_cancels_expired_pending_orders(client, app):
    headers = _auth_header(app)

    with app.app_context():
        order = OrderModel(
            user_id=7,
            store_number=1001,
            customer_id=1,
            company_id=2,
            product_id=3,
            customer_code="CUST001",
            company_code="COMP001",
            barcode="8901234567890",
            po_number="PO-OLD-1",
            ship_by_date="2020-01-01",
            ordered_quantity=2,
            quantity_to_deliver=2,
            quantity_delivered=0,
            tax_rate=18.0,
            unit_price=50.0,
            poa_status=0,
            asn_status=0,
            invoice_status=0,
            order_status="pending",
            total_amount=100.0,
            currency="INR",
            created_at=datetime.now(UTC),
        )
        db.session.add(order)
        db.session.commit()
        created_order_id = order.order_id

    response = client.get("/orders", headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert len(body) == 1
    assert body[0]["order_status"] == "cancelled"

    with app.app_context():
        persisted = db.session.get(OrderModel, created_order_id)
        assert persisted.order_status == "cancelled"


def test_get_order_cancels_expired_pending_order(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    with app.app_context():
        order = db.session.get(OrderModel, order_id)
        order.ship_by_date = "2020-01-01"
        order.order_status = "pending"
        db.session.commit()

    response = client.get(f"/order/{order_id}", headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["order_status"] == "cancelled"

    with app.app_context():
        persisted = db.session.get(OrderModel, order_id)
        assert persisted.order_status == "cancelled"


def test_patch_order_keeps_pending_when_ship_by_date_is_today(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)
    today = datetime.now(UTC).date().isoformat()

    response = client.patch(
        f"/order/{order_id}",
        json={"ship_by_date": today},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["ship_by_date"] == today
    assert body["order_status"] == "pending"


def test_get_order_corrects_cancelled_to_pending_when_ship_by_date_is_future(client, app, monkeypatch):
    """Test that a cancelled order is corrected to pending when ship_by_date is today or later and poa_status=0"""
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)
    tomorrow = (datetime.now(UTC).date()).isoformat()

    with app.app_context():
        order = db.session.get(OrderModel, order_id)
        order.ship_by_date = tomorrow
        order.order_status = "cancelled"
        order.poa_status = 0
        db.session.commit()

    response = client.get(f"/order/{order_id}", headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["order_status"] == "pending"

    with app.app_context():
        persisted = db.session.get(OrderModel, order_id)
        assert persisted.order_status == "pending"


def test_get_order_corrects_pending_to_outstanding_when_poa_sent(client, app, monkeypatch):
    """Test that a pending order is corrected to outstanding when poa_status is set to 1"""
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    with app.app_context():
        order = db.session.get(OrderModel, order_id)
        order.ship_by_date = "2020-01-01"
        order.order_status = "pending"
        order.poa_status = 1
        db.session.commit()

    response = client.get(f"/order/{order_id}", headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["order_status"] == "outstanding"

    with app.app_context():
        persisted = db.session.get(OrderModel, order_id)
        assert persisted.order_status == "outstanding"


def test_get_orders_corrects_all_statuses_properly(client, app):
    """Test that GET /orders corrects multiple orders with different scenarios"""
    headers = _auth_header(app)
    today = datetime.now(UTC).date().isoformat()

    with app.app_context():
        # Order 1: cancelled but should be pending (poa=0, future date)
        order1 = OrderModel(
            user_id=7,
            store_number=1001,
            customer_id=1,
            company_id=2,
            product_id=3,
            customer_code="CUST001",
            company_code="COMP001",
            barcode="8901234567890",
            po_number="PO-TEST-1",
            ship_by_date=today,
            ordered_quantity=2,
            quantity_to_deliver=2,
            quantity_delivered=0,
            tax_rate=18.0,
            unit_price=50.0,
            poa_status=0,
            asn_status=0,
            invoice_status=0,
            order_status="cancelled",
            total_amount=100.0,
            currency="INR",
            created_at=datetime.now(UTC),
        )
        # Order 2: pending but should be outstanding (poa=1)
        order2 = OrderModel(
            user_id=7,
            store_number=1001,
            customer_id=1,
            company_id=2,
            product_id=3,
            customer_code="CUST002",
            company_code="COMP001",
            barcode="8901234567891",
            po_number="PO-TEST-2",
            ship_by_date="2020-01-01",
            ordered_quantity=2,
            quantity_to_deliver=2,
            quantity_delivered=0,
            tax_rate=18.0,
            unit_price=50.0,
            poa_status=1,
            asn_status=0,
            invoice_status=0,
            order_status="pending",
            total_amount=100.0,
            currency="INR",
            created_at=datetime.now(UTC),
        )
        # Order 3: shipped (terminal state, should not change)
        order3 = OrderModel(
            user_id=7,
            store_number=1001,
            customer_id=1,
            company_id=2,
            product_id=3,
            customer_code="CUST003",
            company_code="COMP001",
            barcode="8901234567892",
            po_number="PO-TEST-3",
            ship_by_date="2020-01-01",
            ordered_quantity=2,
            quantity_to_deliver=2,
            quantity_delivered=0,
            tax_rate=18.0,
            unit_price=50.0,
            poa_status=1,
            asn_status=0,
            invoice_status=0,
            order_status="shipped",
            total_amount=100.0,
            currency="INR",
            created_at=datetime.now(UTC),
        )
        db.session.add_all([order1, order2, order3])
        db.session.commit()

    response = client.get("/orders", headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert len(body) == 3
    
    # Find orders by po_number to verify their status
    order1_response = next(o for o in body if o["po_number"] == "PO-TEST-1")
    order2_response = next(o for o in body if o["po_number"] == "PO-TEST-2")
    order3_response = next(o for o in body if o["po_number"] == "PO-TEST-3")
    
    assert order1_response["order_status"] == "pending"
    assert order2_response["order_status"] == "outstanding"
    assert order3_response["order_status"] == "shipped"  # Terminal state, unchanged

    with app.app_context():
        persisted1 = OrderModel.query.filter_by(po_number="PO-TEST-1").first()
        persisted2 = OrderModel.query.filter_by(po_number="PO-TEST-2").first()
        persisted3 = OrderModel.query.filter_by(po_number="PO-TEST-3").first()
        assert persisted1.order_status == "pending"
        assert persisted2.order_status == "outstanding"
        assert persisted3.order_status == "shipped"


def test_get_orders_with_no_orders_returns_empty_list(client, app):
    """Test that GET /orders returns empty list when user has no orders"""
    headers = _auth_header(app)

    response = client.get("/orders", headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body == []


def test_patch_order_rejects_invalid_ship_by_date(client, app, monkeypatch):
    """Test that PATCH /order rejects invalid ship_by_date format"""
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    response = client.patch(
        f"/order/{order_id}",
        json={"ship_by_date": "invalid-date"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "Invalid ship_by_date" in response.get_json()["message"]


def test_patch_order_rejects_negative_quantity_to_deliver(client, app, monkeypatch):
    """Test that PATCH /order rejects negative quantity_to_deliver"""
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    response = client.patch(
        f"/order/{order_id}",
        json={"quantity_to_deliver": -5},
        headers=headers,
    )

    assert response.status_code == 400
    assert "quantity_to_deliver must be zero or greater" in response.get_json()["message"]


def test_patch_order_rejects_zero_or_negative_price(client, app, monkeypatch):
    """Test that PATCH /order rejects zero or negative price"""
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]
    monkeypatch.setattr(
        "order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    order_id = _create_order(app)

    response = client.patch(
        f"/order/{order_id}",
        json={"price": 0},
        headers=headers,
    )

    assert response.status_code == 400
    assert "price must be greater than zero" in response.get_json()["message"]





