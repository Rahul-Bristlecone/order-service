import re
import os
import requests
import logging
from order_service.helper.edifact_parser import parse_edifact
from order_service.helper.reference_validator import validate_references_from_database
from order_service.config.services import get_service_timeout

logger = logging.getLogger(__name__)


def _extract_dtm_value(segments, qualifier):
    value = ""
    for dtm in _as_segment_list(segments.get("DTM", [])):
        extracted = _extract_qualifier_value(dtm, qualifier)
        if extracted:
            value = extracted
    return value


def _as_segment_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            return value
        if value and all(not isinstance(item, (list, dict)) for item in value):
            return [value]
        return value
    return [value]


def _extract_qualifier_value(segment_value, qualifier):
    if isinstance(segment_value, dict):
        values = segment_value.get(qualifier, [])
        return values[0] if values else ""
    if isinstance(segment_value, list):
        if segment_value and str(segment_value[0]) == qualifier and len(segment_value) > 1:
            # Some EDIFACT segments include placeholder empties after qualifier,
            # e.g. NAD+BY++32112 where value is the first non-empty token after BY.
            for item in segment_value[1:]:
                text = str(item).strip()
                if text:
                    return text
            return ""
        for item in segment_value:
            found = _extract_qualifier_value(item, qualifier)
            if found:
                return found
        return ""
    if isinstance(segment_value, str):
        match = re.search(rf"{re.escape(qualifier)}[+:]([^:+']+)", segment_value)
        return match.group(1) if match else ""
    return ""


def _extract_tax_rate(segments):
    tax_rate = 0.0
    for tax in _as_segment_list(segments.get("TAX", [])):
        try:
            values = []
            if isinstance(tax, list):
                values = [str(item).strip() for item in tax if str(item).strip()]
            elif isinstance(tax, str):
                values = [part.strip() for part in tax.split("+") if part.strip()]

            for value in reversed(values):
                if re.fullmatch(r"\d+(\.\d+)?", value):
                    tax_rate = float(value)
                    break
            if tax_rate:
                break
        except (TypeError, ValueError):
            continue
    return tax_rate


def transform_edifact_to_json(file_path: str, service_urls: dict = None) -> dict:
    """
    Extract order data from EDIFACT file and transform to JSON.
    Validates extracted data against external reference services before returning.
    
    Args:
        file_path: Path to EDIFACT file
        service_urls: Dictionary with service endpoints:
            {
                "customer_service": "http://customer-service:5001",
                "company_service": "http://company-service:5002",
                "location_service": "http://location-service:5003",
                "product_service": "http://product-service:5004"
            }
    
    Returns:
        dict with status ("accepted" or "rejected") and validated order data or errors
    """
    segments = parse_edifact(file_path)
    errors = []
    
    logger.info(f"Parsing EDIFACT file: {file_path}")
    logger.debug(f"All segments: {segments}")

    # Extract values from EDIFACT segments
    unb = segments.get("UNB", []) if "UNB" in segments else []
    customer_code = unb[2] if isinstance(unb, list) and len(unb) > 2 else ""
    company_code = unb[4] if isinstance(unb, list) and len(unb) > 4 else ""
    order_type = segments.get("UNH", [])[1] if "UNH" in segments else ""
    po_number = segments.get("BGM", [])[1] if "BGM" in segments else ""
    
    logger.debug(f"Extracted - Customer: {customer_code}, Company: {company_code}, PO: {po_number}")

    # DTM qualifier mapping
    order_creation_date = _extract_dtm_value(segments, "137")
    ship_by_date = _extract_dtm_value(segments, "56")

    # Store number → from NAD qualifiers
    store_number = ""
    nad_by = ""
    nad_su = ""
    if "NAD" in segments:
        for nad in _as_segment_list(segments["NAD"]):
            by_value = _extract_qualifier_value(nad, "BY")
            su_value = _extract_qualifier_value(nad, "SU")
            if by_value:
                nad_by = by_value
            if su_value:
                nad_su = su_value

    if nad_by:
        store_number = nad_by
        if not customer_code:
            customer_code = nad_by
    if nad_su and not company_code:
        company_code = nad_su

    # Barcode → from LIN
    barcode = ""
    if "LIN" in segments:
        for lin in _as_segment_list(segments["LIN"]):
            try:
                if isinstance(lin, dict):
                    values = list(lin.values())[0] if lin else []
                    for value in values:
                        if value and str(value).isdigit():
                            barcode = str(value)
                            break
                elif isinstance(lin, list) and len(lin) >= 3:
                    for value in lin:
                        if value and str(value).isdigit():
                            barcode = str(value)
                            break
                elif isinstance(lin, str):
                    match = re.search(r"\+\+([0-9]+)", lin)
                    if match:
                        barcode = match.group(1)
            except (ValueError, IndexError, KeyError, AttributeError, TypeError):
                continue

    # Quantities → from QTY qualifier 21
    quantities = 0
    if "QTY" in segments:
        logger.debug(f"QTY segments found: {segments['QTY']}")
        for qty in _as_segment_list(segments["QTY"]):
            try:
                logger.debug(f"Processing QTY: {qty} (type: {type(qty)})")
                value = _extract_qualifier_value(qty, "21")
                if value:
                    quantities = int(float(value))
                    logger.info(f"Extracted quantities: {quantities}")
            except (ValueError, IndexError, KeyError, AttributeError) as e:
                logger.warning(f"Error extracting quantities: {e}")
                continue

    # Price → from PRI qualifier AAA
    price = 0.0
    if "PRI" in segments:
        logger.debug(f"PRI segments found: {segments['PRI']}")
        for pri in _as_segment_list(segments["PRI"]):
            try:
                logger.debug(f"Processing PRI: {pri} (type: {type(pri)})")
                value = _extract_qualifier_value(pri, "AAA")
                if value:
                    price = float(value)
                    logger.info(f"Extracted price: {price}")
            except (ValueError, IndexError, KeyError, AttributeError) as e:
                logger.warning(f"Error extracting price: {e}")
                continue

    # Currency → from CUX segment
    currency = "INR"  # default
    if "CUX" in segments:
        for cux in _as_segment_list(segments["CUX"]):
            try:
                if isinstance(cux, dict):
                    cux_values = list(cux.values())[0] if cux else []
                    if cux_values and isinstance(cux_values, list) and ":" in str(cux_values[0]):
                        currency = str(cux_values[0]).split(":")[1]
                elif isinstance(cux, list) and len(cux) > 1:
                    for item in cux:
                        if isinstance(item, str) and len(item) == 3 and item.isalpha():
                            currency = item
                            break
                elif isinstance(cux, str):
                    match = re.search(r":([A-Z]{3}):", cux)
                    if match:
                        currency = match.group(1)
            except (ValueError, IndexError, KeyError, AttributeError, TypeError):
                continue

    # Tax rate -> from TAX segment, e.g. TAX+7+GST+++18'
    tax_rate = _extract_tax_rate(segments)

    # Validate required fields exist in EDIFACT
    logger.info(f"Validation check - store_number: {store_number}, po_number: {po_number}, quantities: {quantities}, price: {price}, tax_rate: {tax_rate}")
    
    if not store_number:
        errors.append("Store number not found in EDIFACT")
    if not po_number:
        errors.append("PO number not found in EDIFACT")
    if not customer_code:
        errors.append("Customer code not found in EDIFACT")
    if not company_code:
        errors.append("Company code not found in EDIFACT")
    if not barcode:
        errors.append("Product barcode not found in EDIFACT")
    if quantities <= 0:
        errors.append("Invalid or missing quantities")
    if price <= 0:
        errors.append("Invalid or missing price")

    # If basic validation fails, return early
    if errors:
        return {
            "status": "rejected",
            "order_status": "rejected",
            "errors": errors,
            "raw_data": {
                "customer_code": customer_code,
                "company_code": company_code,
                "po_number": po_number,
                "order_creation_date": order_creation_date,
                "store_number": store_number,
                "barcode": barcode,
                "quantities": quantities,
                "price": price,
                "currency": currency,
                "tax_rate": tax_rate,
            }
        }

    validation_source = os.getenv("REFERENCE_VALIDATION_SOURCE", "database").strip().lower()

    # Validate against local database reference tables (default mode)
    if validation_source in {"database", "db"}:
        references, db_errors = validate_references_from_database(
            customer_code=customer_code,
            company_code=company_code,
            store_number=store_number,
            barcode=barcode,
            edi_price=price,
        )
        if db_errors:
            return {
                "status": "rejected",
                "order_status": "rejected",
                "errors": db_errors,
                "references": references,
                "user_store_number": int(store_number) if store_number else None,
                "total_amount": price * quantities,
                "currency": currency,
                "metadata": {
                    "customer_code": customer_code,
                    "company_code": company_code,
                    "order_type": order_type,
                    "po_number": po_number,
                    "order_creation_date": order_creation_date,
                    "barcode": barcode,
                    "ordered_quantity": quantities,
                    "quantity": quantities,
                    "tax_rate": tax_rate,
                    "unit_price": price,
                    "ship_by_date": ship_by_date,
                },
            }

        return {
            "status": "accepted",
            "user_store_number": int(store_number) if store_number else None,
            "order_status": "pending",
            "total_amount": price * quantities,
            "currency": currency,
            "references": references,
            "metadata": {
                "customer_code": customer_code,
                "company_code": company_code,
                "order_type": order_type,
                "po_number": po_number,
                "order_creation_date": order_creation_date,
                "barcode": barcode,
                "ordered_quantity": quantities,
                "quantity": quantities,
                "tax_rate": tax_rate,
                "unit_price": price,
                "ship_by_date": ship_by_date
            }
        }

    # Validate against external services if explicitly configured
    if validation_source in {"service", "services"} and service_urls:
        references = {}
        store_num = int(store_number) if str(store_number).isdigit() else None
        
        # Validate customer via Customer Service
        try:
            customer_url = f"{service_urls.get('customer_service', '')}/customers/code/{customer_code}"
            customer_response = requests.get(customer_url, timeout=get_service_timeout())
            if customer_response.status_code == 200:
                customer_data = customer_response.json()
                references["customer_id"] = customer_data.get("customer_id")
            else:
                errors.append(f"Customer code '{customer_code}' not found in customer service")
        except requests.RequestException as e:
            errors.append(f"Failed to validate customer: {str(e)}")

        # Validate company via Company Service
        try:
            company_url = f"{service_urls.get('company_service', '')}/companies/code/{company_code}"
            company_response = requests.get(company_url, timeout=get_service_timeout())
            if company_response.status_code == 200:
                company_data = company_response.json()
                references["company_id"] = company_data.get("company_id")
            else:
                errors.append(f"Company code '{company_code}' not found in company service")
        except requests.RequestException as e:
            errors.append(f"Failed to validate company: {str(e)}")

        # Validate store via Location Service
        try:
            store_num = int(store_number)
            store_url = f"{service_urls.get('location_service', '')}/locations/store/{store_num}"
            store_response = requests.get(store_url, timeout=get_service_timeout())
            if store_response.status_code == 200:
                store_data = store_response.json()
                references["store_id"] = store_data.get("location_id")
            else:
                errors.append(f"Store number '{store_number}' not found in location service")
        except (ValueError, TypeError):
            errors.append(f"Invalid store number format: '{store_number}'")
        except requests.RequestException as e:
            errors.append(f"Failed to validate store: {str(e)}")

        # Validate product via Product Service
        try:
            product_url = f"{service_urls.get('product_service', '')}/products/barcode/{barcode}"
            product_response = requests.get(product_url, timeout=get_service_timeout())
            if product_response.status_code == 200:
                product_data = product_response.json()
                references["product_id"] = product_data.get("product_id")
                product_price = float(product_data.get("price", 0))
                if product_price != float(price):
                    errors.append(
                        f"Price mismatch for product '{barcode}'. "
                        f"EDIFACT price: {price}, Product service price: {product_price}"
                    )
            else:
                errors.append(f"Product barcode '{barcode}' not found in product service")
        except requests.RequestException as e:
            errors.append(f"Failed to validate product: {str(e)}")

        # If service validations failed, return errors
        if errors:
            return {
                "status": "rejected",
                "order_status": "rejected",
                "errors": errors,
                "references": references,
                "user_store_number": store_num,
                "total_amount": price * quantities,
                "currency": currency,
                "metadata": {
                    "customer_code": customer_code,
                    "company_code": company_code,
                    "order_type": order_type,
                    "po_number": po_number,
                    "order_creation_date": order_creation_date,
                    "barcode": barcode,
                    "ordered_quantity": quantities,
                    "quantity": quantities,
                    "tax_rate": tax_rate,
                    "unit_price": price,
                    "ship_by_date": ship_by_date,
                },
            }

        # All validations passed, return accepted with references
        return {
            "status": "accepted",
            "user_store_number": store_num,
            "order_status": "pending",
            "total_amount": price * quantities,
            "currency": currency,
            "references": references,
            "metadata": {
                "customer_code": customer_code,
                "company_code": company_code,
                "order_type": order_type,
                "po_number": po_number,
                "order_creation_date": order_creation_date,
                "barcode": barcode,
                "ordered_quantity": quantities,
                "quantity": quantities,
                "tax_rate": tax_rate,
                "unit_price": price,
                "ship_by_date": ship_by_date
            }
        }
    
    # No service URLs provided, return data without reference validation
    return {
        "status": "accepted",
        "user_store_number": int(store_number) if store_number else None,
        "order_status": "pending",
        "total_amount": price * quantities,
        "currency": currency,
        "metadata": {
            "customer_code": customer_code,
            "company_code": company_code,
            "order_type": order_type,
            "po_number": po_number,
            "order_creation_date": order_creation_date,
            "barcode": barcode,
            "ordered_quantity": quantities,
            "quantity": quantities,
            "tax_rate": tax_rate,
            "unit_price": price,
            "ship_by_date": ship_by_date
        }
    }
