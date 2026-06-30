import io

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


def test_upload_edi_creates_order(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )
    monkeypatch.setattr(
        "src.order_service.resources.orders.transform_edifact_to_json",
        lambda *_args, **_kwargs: {
            "status": "accepted",
            "user_store_number": 1001,
            "order_status": "pending",
            "total_amount": 100.0,
            "currency": "INR",
            "references": {"customer_id": 1, "company_id": 2, "product_id": 3},
            "metadata": {
                "customer_code": "CUST001",
                "company_code": "COMP001",
                "po_number": "PO-1",
                "order_creation_date": "20260614",
                "barcode": "8901234567890",
                "ordered_quantity": 2,
                "tax_rate": 18.0,
                "unit_price": 50.0,
                "ship_by_date": "20260620",
            },
        },
    )

    data = {"file": (io.BytesIO(b"dummy edi"), "order.edi")}
    response = client.post("/upload_edi", data=data, headers=headers, content_type="multipart/form-data")

    assert response.status_code == 201
    body = response.get_json()
    assert body["po_number"] == "PO-1"
    assert body["store_number"] == 1001
    assert body["ordered_quantity"] == 2
    assert body["quantity_to_deliver"] == 2
    assert body["tax_rate"] == 18.0
    assert body["poa_status"] == 0
    assert body["asn_status"] == 0
    assert body["invoice_status"] == 0


def test_upload_edi_rejects_duplicate_business_key(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )
    payload = {
        "status": "accepted",
        "user_store_number": 1001,
        "order_status": "pending",
        "total_amount": 100.0,
        "currency": "INR",
        "references": {"customer_id": 1, "company_id": 2, "product_id": 3},
        "metadata": {
            "customer_code": "CUST001",
            "company_code": "COMP001",
            "po_number": "PO-DUP",
            "order_creation_date": "20260614",
            "barcode": "8901234567890",
            "ordered_quantity": 2,
            "tax_rate": 18.0,
            "unit_price": 50.0,
            "ship_by_date": "20260620",
        },
    }
    monkeypatch.setattr("src.order_service.resources.orders.transform_edifact_to_json", lambda *_args, **_kwargs: payload)

    data = {"file": (io.BytesIO(b"dummy edi"), "order.edi")}
    first = client.post("/upload_edi", data=data, headers=headers, content_type="multipart/form-data")
    assert first.status_code == 201

    data2 = {"file": (io.BytesIO(b"dummy edi"), "order2.edi")}
    second = client.post("/upload_edi", data=data2, headers=headers, content_type="multipart/form-data")
    assert second.status_code == 409


def test_upload_edi_rejects_duplicate_po_for_same_user_with_different_customer(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    payloads = [
        {
            "status": "accepted",
            "user_store_number": 1001,
            "order_status": "pending",
            "total_amount": 100.0,
            "currency": "INR",
            "references": {"customer_id": 1, "company_id": 2, "product_id": 3},
            "metadata": {
                "customer_code": "CUST001",
                "company_code": "COMP001",
                "po_number": "PO-SAME-001",
                "order_creation_date": "20260614",
                "barcode": "8901234567890",
                "ordered_quantity": 2,
                "tax_rate": 18.0,
                "unit_price": 50.0,
                "ship_by_date": "20260620",
            },
        },
        {
            "status": "accepted",
            "user_store_number": 1001,
            "order_status": "pending",
            "total_amount": 100.0,
            "currency": "INR",
            "references": {"customer_id": 10, "company_id": 2, "product_id": 3},
            "metadata": {
                "customer_code": "CUST999",
                "company_code": "COMP001",
                "po_number": "PO-SAME-001",
                "order_creation_date": "20260614",
                "barcode": "8901234567890",
                "ordered_quantity": 2,
                "tax_rate": 18.0,
                "unit_price": 50.0,
                "ship_by_date": "20260620",
            },
        },
    ]

    monkeypatch.setattr(
        "src.order_service.resources.orders.transform_edifact_to_json",
        lambda *_args, **_kwargs: payloads.pop(0),
    )

    data = {"file": (io.BytesIO(b"dummy edi"), "order1.edi")}
    first = client.post("/upload_edi", data=data, headers=headers, content_type="multipart/form-data")
    assert first.status_code == 201

    data2 = {"file": (io.BytesIO(b"dummy edi"), "order2.edi")}
    second = client.post("/upload_edi", data=data2, headers=headers, content_type="multipart/form-data")
    assert second.status_code == 409


def test_upload_edi_requires_confirmation_then_persists_rejected_order(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )
    monkeypatch.setattr(
        "src.order_service.resources.orders.transform_edifact_to_json",
        lambda *_args, **_kwargs: {
            "status": "rejected",
            "order_status": "rejected",
            "errors": ["Customer not found", "Price mismatch"],
            "user_store_number": 1001,
            "total_amount": 100.0,
            "currency": "INR",
            "references": {},
            "metadata": {
                "customer_code": "CUST001",
                "company_code": "COMP001",
                "po_number": "PO-REJ-1",
                "order_creation_date": "20260614",
                "barcode": "8901234567890",
                "ordered_quantity": 2,
                "tax_rate": 18.0,
                "unit_price": 50.0,
                "ship_by_date": "20260620",
            },
        },
    )

    data = {"file": (io.BytesIO(b"dummy edi"), "order_rejected.edi")}
    first_response = client.post("/upload_edi", data=data, headers=headers, content_type="multipart/form-data")

    assert first_response.status_code == 400
    first_body = first_response.get_json()
    assert "Import validation error" in first_body.get("message", "")
    assert "Customer 'CUST001' does not exist" in first_body.get("message", "")
    assert "Click OK" in first_body.get("message", "")

    data_confirm = {"file": (io.BytesIO(b"dummy edi"), "order_rejected_confirm.edi")}
    response = client.post(
        "/upload_edi?confirm_rejected_import=true",
        data=data_confirm,
        headers=headers,
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["order_status"] == "rejected"
    assert body["po_number"] == "PO-REJ-1"

    with app.app_context():
        persisted = OrderModel.query.filter_by(po_number="PO-REJ-1").first()
        assert persisted is not None
        assert persisted.order_status == "rejected"


def test_upload_edi_checks_duplicate_po_before_other_validation(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    payloads = [
        {
            "status": "accepted",
            "user_store_number": 1001,
            "order_status": "pending",
            "total_amount": 100.0,
            "currency": "INR",
            "references": {"customer_id": 1, "company_id": 2, "product_id": 3},
            "metadata": {
                "customer_code": "CUST001",
                "company_code": "COMP001",
                "po_number": "PO-PRIORITY-1",
                "order_creation_date": "20260614",
                "barcode": "8901234567890",
                "ordered_quantity": 2,
                "tax_rate": 18.0,
                "unit_price": 50.0,
                "ship_by_date": "20260620",
            },
        },
        {
            "status": "rejected",
            "order_status": "rejected",
            "errors": ["Customer not found", "Price mismatch"],
            "user_store_number": 1001,
            "total_amount": 100.0,
            "currency": "INR",
            "references": {},
            "metadata": {
                "customer_code": "CUST999",
                "company_code": "COMP001",
                "po_number": "PO-PRIORITY-1",
                "order_creation_date": "20260614",
                "barcode": "8901234567890",
                "ordered_quantity": 2,
                "tax_rate": 18.0,
                "unit_price": 50.0,
                "ship_by_date": "20260620",
            },
        },
    ]

    monkeypatch.setattr(
        "src.order_service.resources.orders.transform_edifact_to_json",
        lambda *_args, **_kwargs: payloads.pop(0),
    )

    first_data = {"file": (io.BytesIO(b"dummy edi"), "order_first.edi")}
    first = client.post("/upload_edi", data=first_data, headers=headers, content_type="multipart/form-data")
    assert first.status_code == 201

    second_data = {"file": (io.BytesIO(b"dummy edi"), "order_second.edi")}
    second = client.post("/upload_edi", data=second_data, headers=headers, content_type="multipart/form-data")
    assert second.status_code == 409
    second_body = second.get_json()
    assert "same user_id + po_number" in second_body.get("message", "")


def test_reject_order_endpoint_persists_rejected_order(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    response = client.post(
        "/reject_order",
        json={
            "errors": ["Customer not found"],
            "store_number": 1001,
            "currency": "INR",
            "metadata": {
                "customer_code": "CUST003",
                "company_code": "COMP001",
                "po_number": "PO-REJECT-ENDPOINT-1",
                "order_creation_date": "20260614",
                "barcode": "7890123456780",
                "ordered_quantity": 10,
                "tax_rate": 18.0,
                "unit_price": 99.5,
                "ship_by_date": "20260620",
            },
            "references": {"company_id": 10},
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["order_status"] == "rejected"
    assert body["po_number"] == "PO-REJECT-ENDPOINT-1"


def test_reject_order_endpoint_accepts_camelcase_store_number(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    response = client.post(
        "/reject_order",
        json={
            "errors": ["Customer not found"],
            "storeNumber": 1001,
            "currency": "INR",
            "metadata": {
                "customerCode": "CUST003",
                "companyCode": "COMP001",
                "poNumber": "PO-REJECT-ENDPOINT-2",
                "orderCreationDate": "20260614",
                "barcode": "7890123456780",
                "orderedQuantity": 10,
                "taxRate": 18.0,
                "unitPrice": 99.5,
                "shipByDate": "20260620",
            },
            "references": {"company_id": 10},
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["order_status"] == "rejected"
    assert body["store_number"] == 1001
    assert body["po_number"] == "PO-REJECT-ENDPOINT-2"


def test_reject_order_alias_endpoint_persists_rejected_order(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    response = client.post(
        "/orders/reject_order",
        json={
            "errors": ["Product/barcode not found"],
            "storeNumber": 1001,
            "currency": "INR",
            "metadata": {
                "customerCode": "CUST004",
                "companyCode": "COMP001",
                "poNumber": "PO-REJECT-ENDPOINT-3",
                "orderCreationDate": "20260614",
                "barcode": "7890123456780",
                "orderedQuantity": 3,
                "taxRate": 18.0,
                "unitPrice": 20.0,
                "shipByDate": "20260620",
            },
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["order_status"] == "rejected"
    assert body["po_number"] == "PO-REJECT-ENDPOINT-3"


def test_reject_order_endpoint_accepts_store_number_from_raw_data(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )

    response = client.post(
        "/reject_order",
        json={
            "status": "rejected",
            "errors": ["Store/location not found"],
            "raw_data": {
                "store_number": 1001,
                "customer_code": "CUST003",
                "company_code": "COMP001",
                "po_number": "PO-REJECT-ENDPOINT-4",
                "barcode": "7890123456780",
                "price": 44.0,
                "quantities": 5,
            },
            "metadata": {
                "ordered_quantity": 5,
                "unit_price": 44.0,
                "tax_rate": 18.0,
                "order_creation_date": "20260614",
            },
            "currency": "INR",
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["store_number"] == 1001
    assert body["po_number"] == "PO-REJECT-ENDPOINT-4"
    assert body["order_status"] == "rejected"


def test_reject_order_uses_cached_draft_for_minimal_payload(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda key: '{"token": "%s"}' % token if key == "session:7" else None,
    )
    store = {}
    monkeypatch.setattr("src.order_service.resources.orders.redis_client.setex", lambda key, ttl, value: store.__setitem__(key, value))

    payload = {
        "status": "rejected",
        "order_status": "rejected",
        "errors": ["Customer not found"],
        "user_store_number": 1001,
        "total_amount": 100.0,
        "currency": "INR",
        "references": {},
        "metadata": {
            "customer_code": "CUST003",
            "company_code": "COMP001",
            "po_number": "PO-CACHED-REJ-1",
            "order_creation_date": "20260614",
            "barcode": "7890123456780",
            "ordered_quantity": 2,
            "tax_rate": 18.0,
            "unit_price": 50.0,
            "ship_by_date": "20260620",
        },
    }
    monkeypatch.setattr("src.order_service.resources.orders.transform_edifact_to_json", lambda *_args, **_kwargs: payload)

    first = client.post(
        "/upload_edi",
        data={"file": (io.BytesIO(b"dummy edi"), "order_rejected_cache.edi")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert first.status_code == 400

    monkeypatch.setattr("src.order_service.resources.orders.redis_client.get", lambda key: '{"token": "%s"}' % token if key == "session:7" else store.get(key))

    second = client.post(
        "/reject_order",
        json={"po_number": "PO-CACHED-REJ-1"},
        headers=headers,
    )
    assert second.status_code == 201
    body = second.get_json()
    assert body["order_status"] == "rejected"
    assert body["store_number"] == 1001


def test_reject_order_uses_latest_cached_draft_when_body_empty(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda key: '{"token": "%s"}' % token if key == "session:7" else None,
    )
    store = {}
    monkeypatch.setattr("src.order_service.resources.orders.redis_client.setex", lambda key, ttl, value: store.__setitem__(key, value))

    payload = {
        "status": "rejected",
        "order_status": "rejected",
        "errors": ["Product/barcode not found"],
        "user_store_number": 1001,
        "total_amount": 50.0,
        "currency": "INR",
        "references": {},
        "metadata": {
            "customer_code": "CUST003",
            "company_code": "COMP001",
            "po_number": "PO-CACHED-REJ-2",
            "order_creation_date": "20260614",
            "barcode": "7890123456780",
            "ordered_quantity": 1,
            "tax_rate": 18.0,
            "unit_price": 50.0,
            "ship_by_date": "20260620",
        },
    }
    monkeypatch.setattr("src.order_service.resources.orders.transform_edifact_to_json", lambda *_args, **_kwargs: payload)

    first = client.post(
        "/upload_edi",
        data={"file": (io.BytesIO(b"dummy edi"), "order_rejected_cache2.edi")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert first.status_code == 400

    monkeypatch.setattr("src.order_service.resources.orders.redis_client.get", lambda key: '{"token": "%s"}' % token if key == "session:7" else store.get(key))

    second = client.post(
        "/reject_order",
        json={},
        headers=headers,
    )
    assert second.status_code == 201
    body = second.get_json()
    assert body["order_status"] == "rejected"
    assert body["po_number"] == "PO-CACHED-REJ-2"


def test_upload_edi_persists_na_codes_when_number_lookup_used(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )
    monkeypatch.setattr(
        "src.order_service.resources.orders.transform_edifact_to_json",
        lambda *_args, **_kwargs: {
            "status": "accepted",
            "user_store_number": 1001,
            "order_status": "pending",
            "total_amount": 100.0,
            "currency": "INR",
            "references": {
                "customer_id": 1,
                "company_id": 2,
                "product_id": 3,
                "customer_lookup_column": "customer_no",
                "company_lookup_column": "company_no",
            },
            "metadata": {
                "customer_code": "12345",
                "company_code": "67890",
                "po_number": "PO-NA-CODE-1",
                "order_creation_date": "20260614",
                "barcode": "8901234567890",
                "ordered_quantity": 2,
                "tax_rate": 18.0,
                "unit_price": 50.0,
                "ship_by_date": "20260620",
            },
        },
    )

    response = client.post(
        "/upload_edi",
        data={"file": (io.BytesIO(b"dummy edi"), "order_na_code.edi")},
        headers=headers,
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["customer_code"] == "NA"
    assert body["company_code"] == "NA"


def test_upload_edi_rejected_with_missing_refs_persists_na_codes(client, app, monkeypatch):
    headers = _auth_header(app)
    token = headers["Authorization"].split()[1]

    monkeypatch.setattr(
        "src.order_service.resources.orders.redis_client.get",
        lambda _key: '{"token": "%s"}' % token,
    )
    monkeypatch.setattr(
        "src.order_service.resources.orders.transform_edifact_to_json",
        lambda *_args, **_kwargs: {
            "status": "rejected",
            "order_status": "rejected",
            "errors": ["Customer not found", "Company not found"],
            "user_store_number": 1001,
            "total_amount": 0,
            "currency": "INR",
            "references": {},
            "metadata": {
                "customer_code": "CUST003",
                "company_code": "240116",
                "po_number": "PO-REJ-NA-1",
                "order_creation_date": "20260614",
                "barcode": "8901234567890",
                "ordered_quantity": 2,
                "tax_rate": 18.0,
                "unit_price": 50.0,
                "ship_by_date": "20260620",
            },
        },
    )

    response = client.post(
        "/upload_edi?confirm_rejected_import=true",
        data={"file": (io.BytesIO(b"dummy edi"), "order_rejected_na.edi")},
        headers=headers,
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["order_status"] == "rejected"
    assert body["customer_code"] == "NA"
    assert body["company_code"] == "NA"