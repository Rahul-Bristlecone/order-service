from pathlib import Path

from order.utils.edifact_transformer import transform_edifact_to_json


class _MockResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_transform_edifact_to_json_accepts_valid_payload(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "service")

    edi_content = (
        "UNB+UNOC:3+CUST001:14+COMP001:14+260614:1234+1'"
        "UNH+1+ORDERS:D:96A:UN'"
        "BGM+220+PO12345+9'"
        "DTM+137:20260614:102'"
        "DTM+56:20260620:102'"
        "NAD+BY+1001::9'"
        "NAD+SU+COMP001::9'"
        "LIN+1++8901234567890:EN'"
        "QTY+21:2'"
        "PRI+AAA:10.5'"
        "TAX+7+GST+++18'"
        "CUX+2:INR:9'"
    )
    edi_file = tmp_path / "ok.edi"
    edi_file.write_text(edi_content, encoding="utf-8")

    def _mock_get(url, timeout=5):
        if "/customers/code/" in url:
            return _MockResponse(200, {"customer_id": 1})
        if "/companies/code/" in url:
            return _MockResponse(200, {"company_id": 2})
        if "/locations/store/" in url:
            return _MockResponse(200, {"location_id": 1001})
        if "/products/barcode/" in url:
            return _MockResponse(200, {"product_id": 3, "price": 10.5})
        return _MockResponse(404, {})

    monkeypatch.setattr("order.utils.edifact_transformer.requests.get", _mock_get)

    result = transform_edifact_to_json(
        str(edi_file),
        {
            "customer_service": "http://customer",
            "company_service": "http://company",
            "location_service": "http://location",
            "product_service": "http://product",
        },
    )

    assert result["status"] == "accepted"
    assert result["user_store_number"] == 1001
    assert result["total_amount"] == 21.0
    assert result["metadata"]["po_number"] == "PO12345"
    assert result["metadata"]["order_creation_date"] == "20260614"
    assert result["metadata"]["ship_by_date"] == "20260620"
    assert result["metadata"]["tax_rate"] == 18.0
    assert result["references"]["customer_id"] == 1


def test_transform_edifact_to_json_rejects_missing_required_fields(tmp_path: Path):
    edi_content = "UNH+1+ORDERS:D:96A:UN'BGM+220+PO12345+9'"
    edi_file = tmp_path / "bad.edi"
    edi_file.write_text(edi_content, encoding="utf-8")

    result = transform_edifact_to_json(str(edi_file), None)

    assert result["status"] == "rejected"
    assert any("Store number" in err for err in result["errors"])
    assert any("Product barcode" in err for err in result["errors"])


def test_transform_edifact_to_json_rejects_customer_not_found(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "service")

    edi_content = (
        "UNB+UNOC:3+CUST001:14+COMP001:14+260614:1234+1'"
        "UNH+1+ORDERS:D:96A:UN'"
        "BGM+220+PO12345+9'"
        "DTM+137:20260614:102'"
        "DTM+56:20260620:102'"
        "NAD+BY+1001::9'"
        "NAD+SU+COMP001::9'"
        "LIN+1++8901234567890:EN'"
        "QTY+21:2'"
        "PRI+AAA:10.5'"
    )
    edi_file = tmp_path / "customer_missing.edi"
    edi_file.write_text(edi_content, encoding="utf-8")

    def _mock_get(url, timeout=5):
        if "/customers/code/" in url:
            return _MockResponse(404, {})
        if "/companies/code/" in url:
            return _MockResponse(200, {"company_id": 2})
        if "/locations/store/" in url:
            return _MockResponse(200, {"location_id": 1001})
        if "/products/barcode/" in url:
            return _MockResponse(200, {"product_id": 3, "price": 10.5})
        return _MockResponse(404, {})

    monkeypatch.setattr("order.utils.edifact_transformer.requests.get", _mock_get)
    result = transform_edifact_to_json(
        str(edi_file),
        {
            "customer_service": "http://customer",
            "company_service": "http://company",
            "location_service": "http://location",
            "product_service": "http://product",
        },
    )

    assert result["status"] == "rejected"
    assert result["order_status"] == "rejected"
    assert any("not found in customer service" in err for err in result["errors"])


def test_transform_edifact_to_json_rejects_company_not_found(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "service")

    edi_content = (
        "UNB+UNOC:3+CUST001:14+COMP001:14+260614:1234+1'"
        "UNH+1+ORDERS:D:96A:UN'"
        "BGM+220+PO12345+9'"
        "DTM+137:20260614:102'"
        "DTM+56:20260620:102'"
        "NAD+BY+1001::9'"
        "NAD+SU+COMP001::9'"
        "LIN+1++8901234567890:EN'"
        "QTY+21:2'"
        "PRI+AAA:10.5'"
    )
    edi_file = tmp_path / "company_missing.edi"
    edi_file.write_text(edi_content, encoding="utf-8")

    def _mock_get(url, timeout=5):
        if "/customers/code/" in url:
            return _MockResponse(200, {"customer_id": 1})
        if "/companies/code/" in url:
            return _MockResponse(404, {})
        if "/locations/store/" in url:
            return _MockResponse(200, {"location_id": 1001})
        if "/products/barcode/" in url:
            return _MockResponse(200, {"product_id": 3, "price": 10.5})
        return _MockResponse(404, {})

    monkeypatch.setattr("order.utils.edifact_transformer.requests.get", _mock_get)
    result = transform_edifact_to_json(
        str(edi_file),
        {
            "customer_service": "http://customer",
            "company_service": "http://company",
            "location_service": "http://location",
            "product_service": "http://product",
        },
    )

    assert result["status"] == "rejected"
    assert any("not found in company service" in err for err in result["errors"])


def test_transform_edifact_to_json_rejects_store_not_found(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "service")

    edi_content = (
        "UNB+UNOC:3+CUST001:14+COMP001:14+260614:1234+1'"
        "UNH+1+ORDERS:D:96A:UN'"
        "BGM+220+PO12345+9'"
        "DTM+137:20260614:102'"
        "DTM+56:20260620:102'"
        "NAD+BY+1001::9'"
        "NAD+SU+COMP001::9'"
        "LIN+1++8901234567890:EN'"
        "QTY+21:2'"
        "PRI+AAA:10.5'"
    )
    edi_file = tmp_path / "store_missing.edi"
    edi_file.write_text(edi_content, encoding="utf-8")

    def _mock_get(url, timeout=5):
        if "/customers/code/" in url:
            return _MockResponse(200, {"customer_id": 1})
        if "/companies/code/" in url:
            return _MockResponse(200, {"company_id": 2})
        if "/locations/store/" in url:
            return _MockResponse(404, {})
        if "/products/barcode/" in url:
            return _MockResponse(200, {"product_id": 3, "price": 10.5})
        return _MockResponse(404, {})

    monkeypatch.setattr("order.utils.edifact_transformer.requests.get", _mock_get)
    result = transform_edifact_to_json(
        str(edi_file),
        {
            "customer_service": "http://customer",
            "company_service": "http://company",
            "location_service": "http://location",
            "product_service": "http://product",
        },
    )

    assert result["status"] == "rejected"
    assert any("not found in location service" in err for err in result["errors"])


def test_transform_edifact_to_json_rejects_price_mismatch(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "service")

    edi_content = (
        "UNB+UNOC:3+CUST001:14+COMP001:14+260614:1234+1'"
        "UNH+1+ORDERS:D:96A:UN'"
        "BGM+220+PO12345+9'"
        "DTM+137:20260614:102'"
        "DTM+56:20260620:102'"
        "NAD+BY+1001::9'"
        "NAD+SU+COMP001::9'"
        "LIN+1++8901234567890:EN'"
        "QTY+21:2'"
        "PRI+AAA:10.5'"
    )
    edi_file = tmp_path / "price_mismatch.edi"
    edi_file.write_text(edi_content, encoding="utf-8")

    def _mock_get(url, timeout=5):
        if "/customers/code/" in url:
            return _MockResponse(200, {"customer_id": 1})
        if "/companies/code/" in url:
            return _MockResponse(200, {"company_id": 2})
        if "/locations/store/" in url:
            return _MockResponse(200, {"location_id": 1001})
        if "/products/barcode/" in url:
            return _MockResponse(200, {"product_id": 3, "price": 11.5})
        return _MockResponse(404, {})

    monkeypatch.setattr("order.utils.edifact_transformer.requests.get", _mock_get)
    result = transform_edifact_to_json(
        str(edi_file),
        {
            "customer_service": "http://customer",
            "company_service": "http://company",
            "location_service": "http://location",
            "product_service": "http://product",
        },
    )

    assert result["status"] == "rejected"
    assert any("Price mismatch for product" in err for err in result["errors"])


def test_transform_edifact_to_json_rejects_product_not_found(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "service")

    edi_content = (
        "UNB+UNOC:3+CUST001:14+COMP001:14+260614:1234+1'"
        "UNH+1+ORDERS:D:96A:UN'"
        "BGM+220+PO12345+9'"
        "DTM+137:20260614:102'"
        "DTM+56:20260620:102'"
        "NAD+BY+1001::9'"
        "NAD+SU+COMP001::9'"
        "LIN+1++8901234567890:EN'"
        "QTY+21:2'"
        "PRI+AAA:10.5'"
    )
    edi_file = tmp_path / "product_missing.edi"
    edi_file.write_text(edi_content, encoding="utf-8")

    def _mock_get(url, timeout=5):
        if "/customers/code/" in url:
            return _MockResponse(200, {"customer_id": 1})
        if "/companies/code/" in url:
            return _MockResponse(200, {"company_id": 2})
        if "/locations/store/" in url:
            return _MockResponse(200, {"location_id": 1001})
        if "/products/barcode/" in url:
            return _MockResponse(404, {})
        return _MockResponse(404, {})

    monkeypatch.setattr("order.utils.edifact_transformer.requests.get", _mock_get)
    result = transform_edifact_to_json(
        str(edi_file),
        {
            "customer_service": "http://customer",
            "company_service": "http://company",
            "location_service": "http://location",
            "product_service": "http://product",
        },
    )

    assert result["status"] == "rejected"
    assert any("not found in product service" in err for err in result["errors"])


def test_transform_edifact_to_json_extracts_store_number_after_empty_by_token(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "service")

    edi_content = (
        "UNB+UNOA:2+CUST003+SUPPLIER003+240115:0930+12345'"
        "UNH+1+ORDERS:D:96A:UN'"
        "BGM+220+PO-2024-0014+9'"
        "DTM+137:20260115:102'"
        "DTM+56:20260615:102'"
        "NAD+BY++32112+Acme Corporation+123 Business Ave+Chicago+IL+60601+US'"
        "NAD+SU+++Global Supplies Inc+456 Industrial Rd+Detroit+MI+48201+US'"
        "LIN+1++7890123456780:EN'"
        "QTY+21:25'"
        "PRI+AAA:299.99'"
        "TAX+7+GST+++18'"
    )
    edi_file = tmp_path / "nad_by_double_plus.edi"
    edi_file.write_text(edi_content, encoding="utf-8")

    def _mock_get(url, timeout=5):
        if "/customers/code/" in url:
            return _MockResponse(200, {"customer_id": 1})
        if "/companies/code/" in url:
            return _MockResponse(200, {"company_id": 2})
        if "/locations/store/" in url:
            return _MockResponse(200, {"location_id": 32112})
        if "/products/barcode/" in url:
            return _MockResponse(200, {"product_id": 3, "price": 299.99})
        return _MockResponse(404, {})

    monkeypatch.setattr("order.utils.edifact_transformer.requests.get", _mock_get)

    result = transform_edifact_to_json(
        str(edi_file),
        {
            "customer_service": "http://customer",
            "company_service": "http://company",
            "location_service": "http://location",
            "product_service": "http://product",
        },
    )

    assert result["status"] == "accepted"
    assert result["user_store_number"] == 32112
    assert result["metadata"]["po_number"] == "PO-2024-0014"