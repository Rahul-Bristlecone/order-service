import json
import os
import logging
import tempfile
from datetime import datetime, UTC
from flask import request
from flask.views import MethodView
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask_smorest import Blueprint, abort
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from werkzeug.exceptions import HTTPException

from order.utils.edifact_transformer import transform_edifact_to_json
from order.config.services import get_service_urls

from order.extentions.db import db
from order.extentions.redis_client import redis_client
from order.models.order_model import OrderModel
from order.schema.order_schema import OrderSchema, PlainOrderSchema, UpdateOrderSchema

# Create logger
logger = logging.getLogger(__name__)

# Create blueprint for Orders
blp = Blueprint("orders", __name__, description="Operations on orders")

REJECTED_IMPORT_DRAFT_TTL_SECONDS = 30 * 60


def _parse_order_creation_date(raw_value):
    if not raw_value:
        return None

    value = str(raw_value).strip()
    for fmt in ["%Y%m%d", "%Y-%m-%d", "%Y%m%d%H%M", "%Y%m%d%H%M%S"]:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _parse_ship_by_date(raw_value):
    if not raw_value:
        return None

    value = str(raw_value).strip()
    for fmt in ["%Y%m%d", "%Y-%m-%d"]:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _derive_order_status(raw_status, ship_by_date_raw):
    # Preserve explicit terminal states if passed by caller.
    if raw_status in {"shipped", "delivered", "cancelled", "rejected"}:
        return raw_status

    ship_by_date = _parse_ship_by_date(ship_by_date_raw)
    today = datetime.now(UTC).date()
    if ship_by_date and ship_by_date < today:
        return "cancelled"
    if ship_by_date and ship_by_date >= today:
        return "pending"
    return "processing"

def _is_order_status_enum_mismatch_error(error_text):
    lowered = str(error_text or "").lower()
    if "order_status" not in lowered:
        return False
    return (
        "enum" in lowered
        or "data truncated" in lowered
        or "invalid" in lowered
        or "incorrect" in lowered
    )


def _refresh_expired_pending_orders(user_id):
    """Cancel pending orders whose ship-by date has already passed."""
    pending_orders = OrderModel.query.filter_by(user_id=user_id, order_status="pending").all()
    if not pending_orders:
        return

    today = datetime.now(UTC).date()
    has_updates = False

    for order in pending_orders:
        ship_by_date = _parse_ship_by_date(order.ship_by_date)
        if ship_by_date and ship_by_date < today:
            order.order_status = "cancelled"
            has_updates = True

    if has_updates:
        db.session.commit()


def _refresh_order_if_expired(order):
    """Cancel a single pending order when ship-by date has passed."""
    if not order or order.order_status != "pending":
        return

    ship_by_date = _parse_ship_by_date(order.ship_by_date)
    today = datetime.now(UTC).date()
    if ship_by_date and ship_by_date < today:
        order.order_status = "cancelled"
        db.session.commit()


def _validate_active_session(user_id):
    auth_header = request.headers.get("Authorization", "")
    token_parts = auth_header.split()
    if len(token_parts) != 2:
        abort(401, message="Session expired or revoked")

    token = token_parts[1]
    cached_session = redis_client.get(f"session:{user_id}")
    if not cached_session:
        abort(401, message="Session expired or revoked")

    try:
        session_data = json.loads(cached_session)
        cached_token = session_data.get("token")
    except Exception:
        abort(401, message="Invalid session data")

    if cached_token != token:
        abort(401, message="Session expired or revoked")


def _to_bool(value):
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _first_present(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _build_rejection_prompt_message(order_data):
    metadata = (order_data or {}).get("metadata", {}) or {}
    errors = (order_data or {}).get("errors", []) or []

    customer_code = metadata.get("customer_code")
    company_code = metadata.get("company_code")
    store_number = (order_data or {}).get("user_store_number") or metadata.get("store_number")
    barcode = metadata.get("barcode")

    messages = []
    lowered_errors = [str(err).lower() for err in errors]

    if any("customer" in err for err in lowered_errors):
        messages.append(f"Customer '{customer_code or 'UNKNOWN'}' does not exist")
    if any("company" in err for err in lowered_errors):
        messages.append(f"Company '{company_code or 'UNKNOWN'}' does not exist")
    if any("store" in err or "location" in err for err in lowered_errors):
        messages.append(f"Store/location '{store_number or 'UNKNOWN'}' does not exist")
    if any("price mismatch" in err for err in lowered_errors):
        messages.append("Price mismatch for the selected product")
    if any("product" in err or "barcode" in err for err in lowered_errors):
        messages.append(f"Product/barcode '{barcode or 'UNKNOWN'}' does not exist")

    if not messages:
        messages = [str(err) for err in errors if str(err).strip()]

    return "; ".join(messages)


def _build_rejected_order_payload(raw_payload):
    payload = dict(raw_payload or {})
    metadata = dict(payload.get("metadata") or {})
    raw_data = dict(payload.get("raw_data") or payload.get("rawData") or {})
    parsed_order = dict(payload.get("parsed_order") or payload.get("parsedOrder") or {})

    alias_map = {
        "storeNumber": "store_number",
        "userStoreNumber": "user_store_number",
        "customerCode": "customer_code",
        "companyCode": "company_code",
        "poNumber": "po_number",
        "orderCreationDate": "order_creation_date",
        "orderedQuantity": "ordered_quantity",
        "unitPrice": "unit_price",
        "shipByDate": "ship_by_date",
        "taxRate": "tax_rate",
    }

    for source_key, target_key in alias_map.items():
        if target_key not in payload and payload.get(source_key) is not None:
            payload[target_key] = payload.get(source_key)
        if target_key not in metadata and metadata.get(source_key) is not None:
            metadata[target_key] = metadata.get(source_key)
        if target_key not in raw_data and raw_data.get(source_key) is not None:
            raw_data[target_key] = raw_data.get(source_key)
        if target_key not in parsed_order and parsed_order.get(source_key) is not None:
            parsed_order[target_key] = parsed_order.get(source_key)

    # Accept either nested metadata keys or flat keys from frontend fallback state.
    for key in (
        "customer_code",
        "company_code",
        "po_number",
        "order_creation_date",
        "barcode",
        "ordered_quantity",
        "quantity",
        "tax_rate",
        "unit_price",
        "ship_by_date",
        "store_number",
    ):
        if key not in metadata:
            metadata[key] = _first_present(
                payload.get(key),
                parsed_order.get(key),
                raw_data.get(key),
            )

    errors = payload.get("errors", [])
    if isinstance(errors, str):
        errors = [errors]

    resolved_store_number = _first_present(
        payload.get("user_store_number"),
        payload.get("store_number"),
        payload.get("store"),
        payload.get("store_no"),
        payload.get("store_code"),
        parsed_order.get("user_store_number"),
        parsed_order.get("store_number"),
        parsed_order.get("store"),
        raw_data.get("store_number"),
        raw_data.get("store"),
        metadata.get("user_store_number"),
        metadata.get("store_number"),
        metadata.get("store"),
    )

    normalized_payload = {
        "status": "rejected",
        "order_status": "rejected",
        "errors": errors,
        "user_store_number": resolved_store_number,
        "total_amount": payload.get("total_amount", 0),
        "currency": payload.get("currency", "INR"),
        "references": payload.get("references") or {},
        "metadata": metadata,
    }

    return normalized_payload


def _extract_po_number_from_payload(payload):
    payload = payload or {}
    metadata = payload.get("metadata") or {}
    return (
        payload.get("po_number")
        or payload.get("poNumber")
        or metadata.get("po_number")
        or metadata.get("poNumber")
    )


def _get_rejected_draft_keys(user_id, po_number=None):
    keys = [f"rejected_import:{user_id}:latest"]
    normalized_po = (po_number or "").strip()
    if normalized_po:
        keys.insert(0, f"rejected_import:{user_id}:po:{normalized_po}")
    return keys


def _store_rejected_import_draft(user_id, order_data):
    serialized = json.dumps(order_data or {})
    po_number = (_extract_po_number_from_payload(order_data) or "").strip()

    try:
        for key in _get_rejected_draft_keys(user_id, po_number):
            redis_client.setex(key, REJECTED_IMPORT_DRAFT_TTL_SECONDS, serialized)
    except Exception as exc:
        logger.warning("Unable to cache rejected import draft for user_id=%s: %s", user_id, str(exc))


def _load_rejected_import_draft(user_id, po_number=None):
    try:
        for key in _get_rejected_draft_keys(user_id, po_number):
            cached = redis_client.get(key)
            if not cached:
                continue
            try:
                return json.loads(cached)
            except Exception:
                continue
    except Exception as exc:
        logger.warning("Unable to load rejected import draft for user_id=%s: %s", user_id, str(exc))
    return None


def _handle_reject_order_request():
    user_id = int(get_jwt_identity())
    _validate_active_session(user_id)

    raw_payload = request.get_json(silent=True) or {}
    if not isinstance(raw_payload, dict):
        abort(400, message="Invalid request body")

    po_number = (_extract_po_number_from_payload(raw_payload) or "").strip()

    if not raw_payload:
        cached_order_data = _load_rejected_import_draft(user_id, po_number)
        if not cached_order_data:
            abort(400, message="No rejected import draft found. Upload EDI again and retry.")
        return create_order_from_payload(cached_order_data)

    rejected_payload = _build_rejected_order_payload(raw_payload)

    if not rejected_payload.get("user_store_number"):
        cached_order_data = _load_rejected_import_draft(
            user_id,
            po_number or _extract_po_number_from_payload(rejected_payload),
        )
        if cached_order_data:
            if not rejected_payload.get("user_store_number"):
                rejected_payload["user_store_number"] = cached_order_data.get("user_store_number")

            cached_metadata = cached_order_data.get("metadata") or {}
            metadata = rejected_payload.get("metadata") or {}
            for key, value in cached_metadata.items():
                if metadata.get(key) is None:
                    metadata[key] = value
            rejected_payload["metadata"] = metadata

    return create_order_from_payload(rejected_payload)


def _find_existing_order_by_po(user_id, po_number):
    normalized_po_number = (po_number or "").strip()
    if not normalized_po_number:
        return None

    return OrderModel.query.filter_by(
        user_id=user_id,
        po_number=normalized_po_number,
    ).first()


def _normalize_code_for_number_lookup(code_value, lookup_column):
    if lookup_column in {"customer_no", "customer_number", "company_no", "company_number"}:
        return "NA"
    return code_value


def _normalize_rejected_code_for_missing_reference(code_value, reference_id, validation_status):
    if validation_status == "rejected" and not reference_id:
        return "NA"
    return code_value

def create_order_from_payload(order_data):
    """
    Shared logic to create an order in the database.
    Validates JWT, checks Redis session, validates order data, and persists the order if valid.
    """
    order_data = dict(order_data or {})
    user_id = int(get_jwt_identity())
    _validate_active_session(user_id)

    validation_status = order_data.get("status")
    validation_errors = order_data.get("errors", [])

    # Extract validated order fields
    incoming_store_number = order_data.pop("user_store_number", None)
    payload_store_number = order_data.pop("store_number", None)
    requested_status = order_data.pop("order_status", None)
    if validation_status == "rejected" and not requested_status:
        requested_status = "rejected"
    total_amount = order_data.pop("total_amount", 0)
    currency = order_data.pop("currency", "INR")

    metadata = order_data.pop("metadata", {}) or {}
    references = order_data.pop("references", {}) or {}

    customer_code = (metadata.get("customer_code") or "").strip()
    company_code = metadata.get("company_code")
    barcode = metadata.get("barcode")
    po_number = (metadata.get("po_number") or "").strip()
    order_creation_date = metadata.get("order_creation_date")
    ship_by_date = metadata.get("ship_by_date")
    ordered_quantity = int(metadata.get("ordered_quantity", metadata.get("quantity", 0)) or 0)
    tax_rate = float(metadata.get("tax_rate", 0) or 0)
    unit_price = float(metadata.get("unit_price", 0) or 0)

    customer_code = _normalize_code_for_number_lookup(
        customer_code,
        references.get("customer_lookup_column"),
    )
    company_code = _normalize_code_for_number_lookup(
        company_code,
        references.get("company_lookup_column"),
    )

    customer_code = _normalize_rejected_code_for_missing_reference(
        customer_code,
        references.get("customer_id"),
        validation_status,
    )
    company_code = _normalize_rejected_code_for_missing_reference(
        company_code,
        references.get("company_id"),
        validation_status,
    )

    order_status = _derive_order_status(requested_status, ship_by_date)

    order_data.pop("status", None)  # Remove status flag

    existing_order = _find_existing_order_by_po(user_id, po_number)
    if existing_order:
        logger.warning(
            "Duplicate order pre-check hit for user_id=%s, po_number=%s (existing order_id=%s)",
            user_id,
            po_number,
            existing_order.order_id,
        )
        abort(409, message="Order already exists for the same user_id + po_number")

    # Use business key store_number directly (validated against stores table)
    resolved_store_number = incoming_store_number or payload_store_number

    if not resolved_store_number:
        abort(
            400,
            message=(
                "Store number is required. Ensure NAD+BY store_number exists in stores table."
            ),
        )
    if not customer_code or not company_code or not barcode or not po_number:
        if validation_status == "rejected" and validation_errors:
            error_message = "EDIFACT validation failed: " + "; ".join(validation_errors)
            abort(400, message=error_message)
        abort(400, message="Missing required EDIFACT business fields")
    if ordered_quantity <= 0 or unit_price <= 0:
        if validation_status == "rejected" and validation_errors:
            error_message = "EDIFACT validation failed: " + "; ".join(validation_errors)
            abort(400, message=error_message)
        abort(400, message="Invalid quantity or unit price")

    # Create and persist order with validated data
    order = OrderModel(
        user_id=user_id,
        store_number=resolved_store_number,
        customer_id=references.get("customer_id"),
        company_id=references.get("company_id"),
        product_id=references.get("product_id"),
        customer_code=customer_code,
        company_code=company_code,
        barcode=barcode,
        po_number=po_number,
        ship_by_date=ship_by_date,
        ordered_quantity=ordered_quantity,
        quantity_to_deliver=ordered_quantity,
        quantity_delivered=None,
        tax_rate=tax_rate,
        unit_price=unit_price,
        poa_status=0,
        asn_status=0,
        invoice_status=0,
        order_status=order_status,
        total_amount=total_amount,
        currency=currency,
        created_at=_parse_order_creation_date(order_creation_date) or datetime.now(UTC),
    )

    try:
        db.session.add(order)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        mysql_error_code = None
        if getattr(exc, "orig", None) and getattr(exc.orig, "args", None):
            mysql_error_code = exc.orig.args[0]

        # MySQL duplicate-key error code.
        if mysql_error_code == 1062:
            abort(409, message="Order already exists for the same user_id + po_number")

        if _is_order_status_enum_mismatch_error(exc):
            abort(
                500,
                message=(
                    "Database schema mismatch: order_status enum is out of date. "
                    "Please run DB migration for order_status enum values."
                ),
            )

        logger.error(
            "IntegrityError while creating order for user_id=%s, customer_code=%s, po_number=%s: %s",
            user_id,
            customer_code,
            po_number,
            str(exc),
            exc_info=True,
        )
        abort(400, message=f"Database integrity error (not duplicate key): {str(exc)}")
    except SQLAlchemyError as exc:
        db.session.rollback()
        if _is_order_status_enum_mismatch_error(exc):
            abort(
                500,
                message=(
                    "Database schema mismatch: order_status enum is out of date. "
                    "Please run DB migration for order_status enum values."
                ),
            )
        abort(500, message="Error inserting order into database")

    return order


# -------------------------------
# Endpoint: /order/<order_id>
# -------------------------------
@blp.route("/order/<int:order_id>")
class OrderResource(MethodView):
    @jwt_required()
    @blp.response(200, OrderSchema)
    def get(self, order_id):
        user_id = int(get_jwt_identity())
        _validate_active_session(user_id)
        order = OrderModel.query.filter_by(order_id=order_id, user_id=user_id).first()
        if not order:
            abort(404, message="Order not found")
        _refresh_order_if_expired(order)
        return order

    @jwt_required()
    @blp.arguments(UpdateOrderSchema)
    @blp.response(200, OrderSchema)
    def patch(self, update_data, order_id):
        user_id = int(get_jwt_identity())
        _validate_active_session(user_id)

        if not update_data:
            abort(400, message="At least one field must be provided for update")

        order = OrderModel.query.filter_by(order_id=order_id, user_id=user_id).first()
        if not order:
            abort(404, message="Order not found")

        if "quantity_to_deliver" in update_data:
            quantity_to_deliver = int(update_data["quantity_to_deliver"])
            if quantity_to_deliver < 0:
                abort(400, message="quantity_to_deliver must be zero or greater")
            order.quantity_to_deliver = quantity_to_deliver

        if "ship_by_date" in update_data:
            ship_by_date_raw = update_data["ship_by_date"]
            parsed_ship_by_date = _parse_ship_by_date(ship_by_date_raw)
            if not parsed_ship_by_date:
                abort(400, message="Invalid ship_by_date. Use YYYY-MM-DD or YYYYMMDD")
            order.ship_by_date = parsed_ship_by_date.isoformat()
            order.order_status = _derive_order_status(order.order_status, order.ship_by_date)

        new_price = None
        if "unit_price" in update_data:
            new_price = float(update_data["unit_price"])
        if "price" in update_data:
            new_price = float(update_data["price"])

        if new_price is not None:
            if new_price <= 0:
                abort(400, message="price must be greater than zero")
            order.unit_price = new_price

        poa_status_updated_to_sent = False
        for field_name in ("poa_status", "asn_status", "invoice_status"):
            if field_name in update_data:
                field_value = int(update_data[field_name])
                if field_value not in {0, 1}:
                    abort(400, message=f"{field_name} must be either 0 or 1")
                setattr(order, field_name, field_value)
                if field_name == "poa_status" and field_value == 1:
                    poa_status_updated_to_sent = True

        # Business rule: once POA is sent, order should move to outstanding.
        if poa_status_updated_to_sent:
            order.order_status = "outstanding"

        # Keep total amount aligned with current mutable commercial values.
        effective_qty = order.quantity_to_deliver if order.quantity_to_deliver is not None else order.ordered_quantity
        order.total_amount = float(effective_qty or 0) * float(order.unit_price or 0)

        try:
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.error("Error updating order_id=%s for user_id=%s: %s", order_id, user_id, str(exc), exc_info=True)
            if "outstanding" in str(exc).lower() and "enum" in str(exc).lower():
                abort(
                    500,
                    message=(
                        "Database schema mismatch: order_status enum does not include 'outstanding'. "
                        "Please run DB migration for order_status enum values."
                    ),
                )
            abort(500, message="Error updating order")

        return order


# -------------------------------
# Endpoint: /orders
# -------------------------------
@blp.route("/orders")
class OrderList(MethodView):
    @jwt_required()
    @blp.response(200, OrderSchema(many=True))
    def get(self):
        user_id = int(get_jwt_identity())
        _refresh_expired_pending_orders(user_id)
        return OrderModel.query.filter_by(user_id=user_id).all()


# -------------------------------
# Endpoint: /create_order
# -------------------------------
@blp.route("/create_order")
class OrderCreate(MethodView):
    @jwt_required()
    @blp.arguments(PlainOrderSchema)
    @blp.response(201, OrderSchema)
    def post(self, order_data):
        return create_order_from_payload(order_data)

    
@blp.route("/upload_edi")
class UploadEdiResource(MethodView):
    @jwt_required()
    @blp.response(201, OrderSchema)
    def post(self):
        file_path = None
        try:
            user_id = int(get_jwt_identity())
            _validate_active_session(user_id)

            if "file" not in request.files:
                abort(400, message="No file uploaded")

            edi_file = request.files["file"]
            file_path = os.path.join(tempfile.gettempdir(), edi_file.filename)
            logger.info(f"Saving EDIFACT file to {file_path}")
            edi_file.save(file_path)

            # Transform EDIFACT → JSON with external service validation
            logger.info(f"Transforming EDIFACT file: {edi_file.filename}")
            service_urls = get_service_urls()
            order_data = transform_edifact_to_json(file_path, service_urls)

            logger.info(f"Transformation result - Status: {order_data.get('status')}")

            po_number = ((order_data.get("metadata") or {}).get("po_number") or "").strip()
            existing_order = _find_existing_order_by_po(user_id, po_number)
            if existing_order:
                abort(409, message="Order already exists for the same user_id + po_number")

            confirm_rejected_import = (
                _to_bool(request.args.get("confirm_rejected_import"))
                or _to_bool(request.form.get("confirm_rejected_import"))
                or _to_bool(request.headers.get("X-Confirm-Rejected-Import"))
            )

            if order_data.get("status") == "rejected" and not confirm_rejected_import:
                _store_rejected_import_draft(user_id, order_data)
                prompt_message = _build_rejection_prompt_message(order_data)
                abort(
                    400,
                    message=(
                        f"Import validation error: {prompt_message}. "
                        "Click OK to continue importing this order with status 'rejected'."
                    ),
                )

            # Reuse the same order creation logic
            logger.info("Creating order from validated EDIFACT data")
            result = create_order_from_payload(order_data)
            logger.info(f"Order created successfully with ID: {result.order_id}")
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing EDIFACT file: {str(e)}", exc_info=True)
            abort(500, message=f"Error processing EDIFACT file: {str(e)}")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    logger.warning(f"Failed to remove temporary file: {file_path}")


@blp.route("/reject_order")
class RejectOrderResource(MethodView):
    @jwt_required()
    @blp.response(201, OrderSchema)
    def post(self):
        return _handle_reject_order_request()


@blp.route("/orders/reject_order")
class RejectOrderResourceAlias1(MethodView):
    @jwt_required()
    @blp.response(201, OrderSchema)
    def post(self):
        return _handle_reject_order_request()


@blp.route("/orders/reject")
class RejectOrderResourceAlias2(MethodView):
    @jwt_required()
    @blp.response(201, OrderSchema)
    def post(self):
        return _handle_reject_order_request()


@blp.route("/order/reject")
class RejectOrderResourceAlias3(MethodView):
    @jwt_required()
    @blp.response(201, OrderSchema)
    def post(self):
        return _handle_reject_order_request()