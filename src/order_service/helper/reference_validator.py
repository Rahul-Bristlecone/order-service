import os

from sqlalchemy import inspect, text

from src.order_service.extentions.db import db


def _find_first_existing_column(table_columns, candidates):
    for candidate in candidates:
        if candidate in table_columns:
            return candidate
    return None


def _find_existing_columns(table_columns, candidates):
    return [candidate for candidate in candidates if candidate in table_columns]


def _find_existing_table(inspector, table_candidates):
    all_tables = inspector.get_table_names()
    lowered = {name.lower(): name for name in all_tables}
    for candidate in table_candidates:
        matched = lowered.get(candidate.lower())
        if matched:
            return matched
    return None


def _read_list_env(env_key, default_values):
    value = os.getenv(env_key, "").strip()
    if not value:
        return default_values
    return [item.strip() for item in value.split(",") if item.strip()]


def _lookup_reference(table_candidates, lookup_value, lookup_column_candidates, id_column_candidates, extra_columns=None):
    extra_columns = extra_columns or []

    inspector = inspect(db.engine)
    table_name = _find_existing_table(inspector, table_candidates)
    if not table_name:
        return None, f"Reference table not found from candidates {table_candidates}"

    table_columns = {column["name"] for column in inspector.get_columns(table_name)}
    lookup_cols = _find_existing_columns(table_columns, lookup_column_candidates)
    id_col = _find_first_existing_column(table_columns, id_column_candidates)

    if not lookup_cols:
        return None, (
            f"No lookup column found in '{table_name}' for candidates {lookup_column_candidates}. "
            f"Available columns: {sorted(table_columns)}"
        )
    if not id_col:
        return None, (
            f"No id column found in '{table_name}' for candidates {id_column_candidates}. "
            f"Available columns: {sorted(table_columns)}"
        )

    selected_extras = [column for column in extra_columns if column in table_columns]
    select_columns = [id_col] + selected_extras
    select_sql = ", ".join(select_columns)

    for lookup_col in lookup_cols:
        row = db.session.execute(
            text(f"SELECT {select_sql} FROM {table_name} WHERE {lookup_col} = :lookup_value LIMIT 1"),
            {"lookup_value": lookup_value},
        ).mappings().first()

        if row:
            return row, None, lookup_col

    return None, (
        f"No matching record found in '{table_name}' for value={lookup_value} "
        f"using columns {lookup_cols}"
    ), None


def validate_references_from_database(customer_code, company_code, store_number, barcode, edi_price):
    errors = []
    references = {}

    customer_row, customer_error, customer_lookup_column = _lookup_reference(
        table_candidates=_read_list_env("REF_CUSTOMER_TABLES", ["customers", "customer"]),
        lookup_value=customer_code,
        lookup_column_candidates=_read_list_env(
            "REF_CUSTOMER_LOOKUP_COLUMNS",
            ["customer_code", "code", "customer_no", "customer_number", "customer_id", "id"],
        ),
        id_column_candidates=_read_list_env("REF_CUSTOMER_ID_COLUMNS", ["customer_id", "id"]),
    )
    if customer_error:
        if "No matching record found" in customer_error:
            errors.append("Customer not found")
        else:
            errors.append(f"Failed to validate customer in database: {customer_error}")
    else:
        references["customer_id"] = customer_row.get("customer_id") or customer_row.get("id")
        references["customer_lookup_column"] = customer_lookup_column

    company_row, company_error, company_lookup_column = _lookup_reference(
        table_candidates=_read_list_env("REF_COMPANY_TABLES", ["company", "companies"]),
        lookup_value=company_code,
        lookup_column_candidates=_read_list_env(
            "REF_COMPANY_LOOKUP_COLUMNS",
            ["company_code", "code", "company_no", "company_number", "company_id", "id"],
        ),
        id_column_candidates=_read_list_env("REF_COMPANY_ID_COLUMNS", ["company_id", "id"]),
    )
    if company_error:
        if "No matching record found" in company_error:
            errors.append("Company not found")
        else:
            errors.append(f"Failed to validate company in database: {company_error}")
    else:
        references["company_id"] = company_row.get("company_id") or company_row.get("id")
        references["company_lookup_column"] = company_lookup_column

    store_row, store_error, _store_lookup_column = _lookup_reference(
        table_candidates=_read_list_env("REF_STORE_TABLES", ["stores", "store"]),
        lookup_value=int(store_number),
        lookup_column_candidates=_read_list_env(
            "REF_STORE_LOOKUP_COLUMNS",
            ["store_number", "store_no", "store_code"],
        ),
        id_column_candidates=_read_list_env("REF_STORE_ID_COLUMNS", ["store_id", "location_id", "id"]),
    )
    if store_error:
        if "No matching record found" in store_error:
            errors.append("Store/location not found")
        else:
            errors.append(f"Failed to validate store in database: {store_error}")
    else:
        references["store_id"] = (
            store_row.get("store_id") or store_row.get("location_id") or store_row.get("id")
        )

    product_row, product_error, _product_lookup_column = _lookup_reference(
        table_candidates=_read_list_env("REF_PRODUCT_TABLES", ["products", "items", "item"]),
        lookup_value=barcode,
        lookup_column_candidates=_read_list_env(
            "REF_PRODUCT_LOOKUP_COLUMNS",
            ["barcode", "product_barcode", "item_barcode", "ean", "upc", "sku", "item_code"],
        ),
        id_column_candidates=_read_list_env("REF_PRODUCT_ID_COLUMNS", ["product_id", "item_id", "id"]),
        extra_columns=_read_list_env(
            "REF_PRODUCT_PRICE_COLUMNS",
            ["price", "unit_price", "selling_price", "mrp", "item_price"],
        ),
    )
    if product_error:
        if "No matching record found" in product_error:
            errors.append("Product/barcode not found")
        else:
            errors.append(f"Failed to validate product in database: {product_error}")
    else:
        references["product_id"] = product_row.get("product_id") or product_row.get("id")
        product_price = product_row.get("price")
        if product_price is None:
            product_price = product_row.get("unit_price")
        if product_price is None:
            product_price = product_row.get("selling_price")

        if product_price is not None:
            if abs(float(product_price) - float(edi_price)) > 0.0001:
                errors.append(
                    f"Price mismatch for product '{barcode}'. EDIFACT price: {edi_price}, DB price: {product_price}"
                )

    return references, errors