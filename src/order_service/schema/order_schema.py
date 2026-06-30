from marshmallow import Schema, fields, pre_load, EXCLUDE

# Plain schema for orders (basic fields)
class PlainOrderSchema(Schema):
    user_id = fields.Int(dump_only=True)
    order_id = fields.Int(dump_only=True)
    store_number = fields.Int(dump_only=True)
    customer_id = fields.Int(dump_only=True)
    company_id = fields.Int(dump_only=True)
    product_id = fields.Int(dump_only=True)
    customer_code = fields.Str(dump_only=True)
    company_code = fields.Str(dump_only=True)
    barcode = fields.Str(dump_only=True)
    po_number = fields.Str(dump_only=True)
    order_creation_date = fields.Str(dump_only=True)
    ship_by_date = fields.Str(dump_only=True)
    ordered_quantity = fields.Int(dump_only=True)
    quantity_to_deliver = fields.Int(dump_only=True, allow_none=True)
    quantity_delivered = fields.Int(dump_only=True, allow_none=True)
    tax_rate = fields.Float(dump_only=True)
    unit_price = fields.Float(dump_only=True)
    poa_status = fields.Int(dump_only=True)
    asn_status = fields.Int(dump_only=True)
    invoice_status = fields.Int(dump_only=True)
    
    # Validation result fields (from EDIFACT transformer)
    status = fields.Str(load_only=True)  # "accepted" or "rejected"
    errors = fields.List(fields.Str(), load_only=True)  # validation errors
    user_store_number = fields.Int(load_only=True)  # store identifier
    references = fields.Dict(load_only=True)
    
    # Order fields
    order_status = fields.Str(dump_only=True)
    total_amount = fields.Float(dump_only=True)
    currency = fields.Str(dump_only=True)
    
    # Metadata from EDIFACT (for reference, not persisted)
    metadata = fields.Dict(load_only=True)
    
    # Dates
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)


# Extended schema for orders with nested relationships
class OrderSchema(PlainOrderSchema):
    user_id = fields.Int(dump_only=True)
    store_number = fields.Int(dump_only=True)


class UpdateOrderSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    quantity_to_deliver = fields.Int(load_only=True)
    ship_by_date = fields.Str(load_only=True)
    unit_price = fields.Float(load_only=True)
    price = fields.Float(load_only=True)
    poa_status = fields.Int(load_only=True)
    asn_status = fields.Int(load_only=True)
    invoice_status = fields.Int(load_only=True)

    @pre_load
    def normalize_status_keys(self, data, **kwargs):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        alias_map = {
            "poaStatus": "poa_status",
            "asnStatus": "asn_status",
            "invoiceStatus": "invoice_status",
        }

        for source_key, target_key in alias_map.items():
            if source_key in normalized:
                if target_key not in normalized:
                    normalized[target_key] = normalized[source_key]
                normalized.pop(source_key, None)

        return normalized