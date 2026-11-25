"""Version 1 field dictionary. Occurrence IDs distinguish repeated physical rows."""
ROOTS = {"invoice", "recipe", "menu"}
_FIELDS = {
    "invoice": "document_kind document_number supplier_name supplier_id supplier_address customer_name customer_id customer_address issue_date due_date currency subtotal total tax_total discount_total charge_total calculated_total",
    "line_item": "description sku quantity purchase_unit pack_count pack_unit_quantity pack_unit pack_total_quantity unit_price price_basis discount discount_rate tax tax_rate line_total calculated_line_total currency",
    "tax": "name basis rate amount currency",
    "charge": "name amount currency",
    "discount": "name rate amount currency",
    "menu": "venue_name version date currency",
    "section": "name translated_name description",
    "menu_item": "name translated_name description translated_description price currency dietary_claim allergen_claim crossed_out replacement_price",
    "variant": "name size price currency crossed_out replacement_price",
    "supplement": "name price currency",
    "bundle": "name translated_name description price currency",
    "bundle_choice": "name group description item_ref",
    "recipe": "title version yield_quantity yield_unit yield_type description",
    "subrecipe": "name title yield_quantity yield_unit",
    "ingredient": "name quantity quantity_text quantity_min quantity_max unit preparation optional crossed_out replacement_quantity",
    "substitution": "name quantity unit preparation",
    "step": "instruction duration_minutes time_text temperature_c temperature_f temperature_text",
    "unknown": "description",
}
FIELDS = {k: tuple(v.split()) for k, v in _FIELDS.items()}
CALCULATED_ONLY = {"calculated_total", "calculated_line_total", "pack_total_quantity"}
FAMILIES = {
    "invoice": ("invoice", "line_item", "tax", "charge", "discount"),
    "menu": ("menu", "section", "menu_item", "variant", "supplement", "bundle", "bundle_choice"),
    "recipe": ("recipe", "subrecipe", "ingredient", "substitution", "step"),
    "unknown": ("unknown",),
}
