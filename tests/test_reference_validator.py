from types import SimpleNamespace

from src.order_service.helper import reference_validator as rv


class _ExecResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _Inspector:
    def __init__(self, tables, columns_by_table):
        self._tables = tables
        self._columns = columns_by_table

    def get_table_names(self):
        return self._tables

    def get_columns(self, table_name):
        return [{"name": name} for name in self._columns.get(table_name, [])]


def test_basic_helpers(monkeypatch):
    assert rv._find_first_existing_column({"a", "b"}, ["x", "b"]) == "b"
    assert rv._find_first_existing_column({"a", "b"}, ["x", "y"]) is None
    assert rv._find_existing_columns({"a", "b", "c"}, ["x", "b", "c"]) == ["b", "c"]

    inspector = _Inspector(["Customers", "Products"], {})
    assert rv._find_existing_table(inspector, ["customers"]) == "Customers"
    assert rv._find_existing_table(inspector, ["missing"]) is None

    monkeypatch.setenv("REF_TABLES", "a,b, c")
    assert rv._read_list_env("REF_TABLES", ["x"]) == ["a", "b", "c"]
    monkeypatch.delenv("REF_TABLES", raising=False)
    assert rv._read_list_env("REF_TABLES", ["x"]) == ["x"]


def test_lookup_reference_table_not_found(monkeypatch):
    monkeypatch.setattr(rv, "inspect", lambda _engine: _Inspector([], {}))
    monkeypatch.setattr(rv, "db", SimpleNamespace(engine=object(), session=SimpleNamespace(execute=lambda *_: None)))

    row, err = rv._lookup_reference(
        table_candidates=["customers"],
        lookup_value="C1",
        lookup_column_candidates=["code"],
        id_column_candidates=["id"],
    )[:2]

    assert row is None
    assert "Reference table not found" in err


def test_lookup_reference_missing_columns(monkeypatch):
    monkeypatch.setattr(
        rv,
        "inspect",
        lambda _engine: _Inspector(["customers"], {"customers": ["name"]}),
    )
    monkeypatch.setattr(rv, "db", SimpleNamespace(engine=object(), session=SimpleNamespace(execute=lambda *_: None)))

    row, err = rv._lookup_reference(
        table_candidates=["customers"],
        lookup_value="C1",
        lookup_column_candidates=["code"],
        id_column_candidates=["id"],
    )[:2]
    assert row is None
    assert "No lookup column found" in err

    row, err = rv._lookup_reference(
        table_candidates=["customers"],
        lookup_value="C1",
        lookup_column_candidates=["name"],
        id_column_candidates=["id"],
    )[:2]
    assert row is None
    assert "No id column found" in err


def test_lookup_reference_success_and_no_match(monkeypatch):
    monkeypatch.setattr(
        rv,
        "inspect",
        lambda _engine: _Inspector(
            ["products"],
            {"products": ["id", "barcode", "sku", "price"]},
        ),
    )
    monkeypatch.setattr(rv, "db", SimpleNamespace(engine=object(), session=SimpleNamespace(execute=lambda *_: _ExecResult(None))))

    calls = {"n": 0}

    def _execute(_sql, _params):
        calls["n"] += 1
        if calls["n"] == 1:
            return _ExecResult(None)
        return _ExecResult({"id": 11, "price": 55.5})

    monkeypatch.setattr(rv.db, "session", SimpleNamespace(execute=_execute))

    row, err, lookup_col = rv._lookup_reference(
        table_candidates=["products"],
        lookup_value="890",
        lookup_column_candidates=["sku", "barcode"],
        id_column_candidates=["id"],
        extra_columns=["price", "missing"],
    )
    assert err is None
    assert lookup_col == "barcode"
    assert row["id"] == 11

    monkeypatch.setattr(rv.db, "session", SimpleNamespace(execute=lambda *_: _ExecResult(None)))
    row, err, lookup_col = rv._lookup_reference(
        table_candidates=["products"],
        lookup_value="does-not-exist",
        lookup_column_candidates=["barcode"],
        id_column_candidates=["id"],
    )
    assert row is None
    assert lookup_col is None
    assert "No matching record found" in err


def test_validate_references_from_database_success_and_failures(monkeypatch):
    responses = [
        ({"customer_id": 1}, None, "customer_no"),
        ({"company_id": 2}, None, "company_no"),
        ({"store_id": 3}, None, "store_number"),
        ({"product_id": 4, "price": 10.0}, None, "barcode"),
    ]

    monkeypatch.setattr(rv, "_lookup_reference", lambda **_kwargs: responses.pop(0))
    refs, errs = rv.validate_references_from_database("C1", "CO1", 1001, "890", 10.0)

    assert errs == []
    assert refs["customer_id"] == 1
    assert refs["company_id"] == 2
    assert refs["store_id"] == 3
    assert refs["product_id"] == 4
    assert refs["customer_lookup_column"] == "customer_no"
    assert refs["company_lookup_column"] == "company_no"

    responses = [
        (None, "No matching record found in 'customers'", None),
        (None, "fatal company issue", None),
        (None, "No matching record found in 'stores'", None),
        ({"id": 9, "unit_price": 99.0}, None, None),
    ]
    monkeypatch.setattr(rv, "_lookup_reference", lambda **_kwargs: responses.pop(0))
    refs, errs = rv.validate_references_from_database("C1", "CO1", 1001, "890", 10.0)

    assert refs["product_id"] == 9
    assert "Customer not found" in errs
    assert any("Failed to validate company" in err for err in errs)
    assert "Store/location not found" in errs
    assert any("Price mismatch for product '890'" in err for err in errs)


def test_validate_references_fallback_ids_and_selling_price(monkeypatch):
    responses = [
        ({"id": 101}, None, "code"),
        ({"id": 202}, None, "code"),
        ({"location_id": 303}, None, "store_number"),
        ({"id": 404, "selling_price": 11.0}, None, "barcode"),
    ]

    monkeypatch.setattr(rv, "_lookup_reference", lambda **_kwargs: responses.pop(0))
    refs, errs = rv.validate_references_from_database("C1", "CO1", 1001, "890", 10.0)

    assert refs["customer_id"] == 101
    assert refs["company_id"] == 202
    assert refs["store_id"] == 303
    assert refs["product_id"] == 404
    assert any("Price mismatch for product '890'" in err for err in errs)


def test_validate_references_direct_ids_without_price_mismatch(monkeypatch):
    responses = [
        ({"customer_id": 1}, None, "customer_code"),
        ({"company_id": 2}, None, "company_code"),
        ({"store_id": 3}, None, "store_number"),
        ({"product_id": 4}, None, "barcode"),
    ]

    monkeypatch.setattr(rv, "_lookup_reference", lambda **_kwargs: responses.pop(0))
    refs, errs = rv.validate_references_from_database("C1", "CO1", 1001, "890", 10.0)

    assert refs["customer_id"] == 1
    assert refs["company_id"] == 2
    assert refs["store_id"] == 3
    assert refs["product_id"] == 4
    assert errs == []
