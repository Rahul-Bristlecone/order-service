from pathlib import Path

import requests

from order_service.utils import edifact_transformer as tr


def test_segment_helper_functions():
    assert tr._as_segment_list(None) == []
    assert tr._as_segment_list("A") == ["A"]
    assert tr._as_segment_list(["A", "B"]) == [["A", "B"]]
    assert tr._as_segment_list([{"A": ["1"]}]) == [{"A": ["1"]}]

    assert tr._extract_qualifier_value({"BY": ["1001"]}, "BY") == "1001"
    assert tr._extract_qualifier_value(["BY", "", "1002"], "BY") == "1002"
    assert tr._extract_qualifier_value("NAD+BY+1003::9", "BY") == "1003"
    assert tr._extract_qualifier_value("NAD+SU+COMP::9", "BY") == ""

    assert tr._extract_tax_rate({"TAX": [["7", "GST", "", "", "18"]]}) == 18.0
    assert tr._extract_tax_rate({"TAX": "TAX+7+GST+++12.5"}) == 12.5


def test_transform_database_mode(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "database")
    monkeypatch.setattr(
        tr,
        "parse_edifact",
        lambda _path: {
            "UNB": ["UNOC:3", "x", "CUST001", "x", "COMP001"],
            "UNH": ["1", "ORDERS"],
            "BGM": ["220", "PO123"],
            "DTM": [["137", "20260614", "102"], ["56", "20260620", "102"]],
            "NAD": [["BY", "1001"], ["SU", "COMP001"]],
            "LIN": [["1", "", "8901234567890", "EN"]],
            "QTY": [["21", "2"]],
            "PRI": [["AAA", "10.5"]],
            "TAX": [["7", "GST", "", "", "18"]],
            "CUX": [["2:INR:9"]],
        },
    )

    monkeypatch.setattr(
        tr,
        "validate_references_from_database",
        lambda **_kwargs: (
            {
                "customer_id": 1,
                "company_id": 2,
                "store_id": 3,
                "product_id": 4,
            },
            [],
        ),
    )

    edi = tmp_path / "ok.edi"
    edi.write_text("dummy", encoding="utf-8")
    accepted = tr.transform_edifact_to_json(str(edi), None)
    assert accepted["status"] == "accepted"
    assert accepted["user_store_number"] == 1001

    monkeypatch.setattr(tr, "validate_references_from_database", lambda **_kwargs: ({"customer_id": 1}, ["Customer not found"]))
    rejected = tr.transform_edifact_to_json(str(edi), None)
    assert rejected["status"] == "rejected"
    assert "Customer not found" in rejected["errors"]


def test_transform_service_mode_errors_and_fallback(monkeypatch, tmp_path: Path):
    # Invalid store number plus request exceptions should populate errors list.
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "service")
    monkeypatch.setattr(
        tr,
        "parse_edifact",
        lambda _path: {
            "UNB": ["UNOC:3", "x", "CUST001", "x", "COMP001"],
            "UNH": ["1", "ORDERS"],
            "BGM": ["220", "PO123"],
            "DTM": [["137", "20260614", "102"], ["56", "20260620", "102"]],
            "NAD": [["BY", "1001"], ["SU", "COMP001"]],
            "LIN": [["1", "", "8901234567890", "EN"]],
            "QTY": [["21", "2"]],
            "PRI": [["AAA", "10.5"]],
            "TAX": [["7", "GST", "", "", "18"]],
        },
    )

    def _boom(*_args, **_kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(tr.requests, "get", _boom)

    edi = tmp_path / "service.edi"
    edi.write_text("dummy", encoding="utf-8")
    result = tr.transform_edifact_to_json(
        str(edi),
        {
            "customer_service": "http://customer",
            "company_service": "http://company",
            "location_service": "http://location",
            "product_service": "http://product",
        },
    )
    assert result["status"] == "rejected"
    assert any("Failed to validate customer" in err for err in result["errors"])

    # No service URLs should fall back to accepted payload without references.
    monkeypatch.setattr(
        tr,
        "parse_edifact",
        lambda _path: {
            "UNB": ["UNOC:3", "x", "CUST001", "x", "COMP001"],
            "UNH": ["1", "ORDERS"],
            "BGM": ["220", "PO999"],
            "DTM": [["137", "20260614", "102"], ["56", "20260620", "102"]],
            "NAD": [["BY", "1001"], ["SU", "COMP001"]],
            "LIN": [["1", "", "8901234567890", "EN"]],
            "QTY": [["21", "2"]],
            "PRI": [["AAA", "10.5"]],
        },
    )
    fallback = tr.transform_edifact_to_json(str(edi), None)
    assert fallback["status"] == "accepted"
    assert fallback["order_status"] == "pending"


def test_transform_parsing_edge_branches(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REFERENCE_VALIDATION_SOURCE", "none")

    # Exercise recursive qualifier path and non-supported type fallback.
    assert tr._extract_qualifier_value([["BY", "", "1009"]], "BY") == "1009"
    assert tr._extract_qualifier_value(["ZZ", "x"], "BY") == ""
    assert tr._extract_qualifier_value(123, "BY") == ""

    monkeypatch.setattr(
        tr,
        "parse_edifact",
        lambda _path: {
            "UNB": ["UNOC:3", "x", "CUST001", "x", "COMP001"],
            "UNH": ["1", "ORDERS"],
            "BGM": ["220", "PO-EDGE-1"],
            "DTM": [["137", "20260614", "102"], ["56", "20260620", "102"]],
            "NAD": [["BY", "1001"], ["SU", "COMP001"]],
            # Dict path in LIN parsing
            "LIN": [{"X": ["abc", "8901234567890"]}],
            # Force quantity/price parse exceptions to exercise warning branches
            "QTY": [["21", "abc"]],
            "PRI": [["AAA", "bad-price"]],
            # Cover dict + list + string CUX parsing paths
            "CUX": [{"A": ["2:USD:9"]}, ["2", "EUR"], "CUX+2:GBP:9"],
            "TAX": "TAX+7+GST+++18",
        },
    )

    edi = tmp_path / "edge.edi"
    edi.write_text("dummy", encoding="utf-8")
    result = tr.transform_edifact_to_json(str(edi), None)

    assert result["status"] == "rejected"
    assert "Invalid or missing quantities" in result["errors"]
    assert "Invalid or missing price" in result["errors"]
