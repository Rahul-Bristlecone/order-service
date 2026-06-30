from datetime import datetime, UTC

import pytest
from flask_jwt_extended import create_access_token

from src.order_service.main import create_app
from src.order_service.extentions.db import db
from src.order_service.models.order_model import OrderModel


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
        "src.order_service.resources.orders.redis_client.get",
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
        "src.order_service.resources.orders.redis_client.get",
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
        "src.order_service.resources.orders.redis_client.get",
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
        "src.order_service.resources.orders.redis_client.get",
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
        "src.order_service.resources.orders.redis_client.get",
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
        "src.order_service.resources.orders.redis_client.get",
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
        "src.order_service.resources.orders.redis_client.get",
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
