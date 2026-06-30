import json

from order_service.resources import orders as ord_mod


def test_orders_helpers_parsing_and_flags():
    assert ord_mod._parse_order_creation_date("20260614") is not None
    assert ord_mod._parse_order_creation_date("2026-06-14") is not None
    assert ord_mod._parse_order_creation_date("bad-date") is None

    assert ord_mod._parse_ship_by_date("20260620") is not None
    assert ord_mod._parse_ship_by_date("2026-06-20") is not None
    assert ord_mod._parse_ship_by_date("invalid") is None

    assert ord_mod._is_order_status_enum_mismatch_error("invalid enum value in order_status")
    assert not ord_mod._is_order_status_enum_mismatch_error("some other error")

    assert ord_mod._to_bool("true")
    assert ord_mod._to_bool("on")
    assert not ord_mod._to_bool("off")
    assert not ord_mod._to_bool(None)

    assert ord_mod._first_present(None, "", "value") == "value"
    assert ord_mod._first_present(None, " ", None) is None


def test_orders_rejection_payload_and_message_helpers():
    payload = {
        "errors": ["Customer not found", "Price mismatch"],
        "storeNumber": 1001,
        "currency": "INR",
        "metadata": {
            "customerCode": "CUST001",
            "companyCode": "COMP001",
            "poNumber": "PO-1",
            "barcode": "890",
            "orderedQuantity": 2,
            "unitPrice": 50.0,
            "shipByDate": "20260620",
        },
    }
    normalized = ord_mod._build_rejected_order_payload(payload)
    assert normalized["status"] == "rejected"
    assert normalized["user_store_number"] == 1001
    assert normalized["metadata"]["customer_code"] == "CUST001"
    assert normalized["metadata"]["po_number"] == "PO-1"

    message = ord_mod._build_rejection_prompt_message(normalized)
    assert "Customer 'CUST001' does not exist" in message
    assert "Price mismatch" in message

    assert ord_mod._extract_po_number_from_payload(normalized) == "PO-1"
    keys = ord_mod._get_rejected_draft_keys(7, "PO-1")
    assert keys[0].endswith(":po:PO-1")
    assert keys[1].endswith(":latest")


def test_orders_cache_draft_helpers(monkeypatch):
    store = {}

    def _setex(key, _ttl, value):
        store[key] = value

    def _get(key):
        return store.get(key)

    monkeypatch.setattr(ord_mod.redis_client, "setex", _setex)
    monkeypatch.setattr(ord_mod.redis_client, "get", _get)

    payload = {
        "metadata": {"po_number": "PO-CACHE-1"},
        "status": "rejected",
        "errors": ["Customer not found"],
    }
    ord_mod._store_rejected_import_draft(7, payload)

    loaded = ord_mod._load_rejected_import_draft(7, "PO-CACHE-1")
    assert loaded["status"] == "rejected"

    store["rejected_import:7:latest"] = "not-json"
    loaded_fallback = ord_mod._load_rejected_import_draft(7)
    assert loaded_fallback is None

    def _boom(*_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(ord_mod.redis_client, "setex", _boom)
    ord_mod._store_rejected_import_draft(7, payload)

    monkeypatch.setattr(ord_mod.redis_client, "get", _boom)
    assert ord_mod._load_rejected_import_draft(7) is None


def test_orders_code_normalizers():
    assert ord_mod._normalize_code_for_number_lookup("ABC", "customer_no") == "NA"
    assert ord_mod._normalize_code_for_number_lookup("ABC", "customer_code") == "ABC"

    assert ord_mod._normalize_rejected_code_for_missing_reference("ABC", None, "rejected") == "NA"
    assert ord_mod._normalize_rejected_code_for_missing_reference("ABC", 1, "rejected") == "ABC"
    assert ord_mod._normalize_rejected_code_for_missing_reference("ABC", None, "accepted") == "ABC"
