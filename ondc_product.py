import frappe
from frappe.model.document import Document
import json


class ONDCProduct(Document):
    def before_insert(self):
        """Auto-generate ONDC Product ID before Frappe's autoname runs.
        autoname is set to 'field:ondc_product_id', so this field MUST
        be populated before insert — validate() runs too late."""
        if not self.ondc_product_id:
            self.ondc_product_id = self.generate_ondc_product_id()

    def validate(self):
        # Safety net: also generate on update if somehow missing
        if not self.ondc_product_id:
            self.ondc_product_id = self.generate_ondc_product_id()

        # Validate minimum and maximum quantity
        if self.minimum_quantity and self.maximum_quantity:
            if self.minimum_quantity > self.maximum_quantity:
                frappe.throw("Minimum quantity cannot be greater than maximum quantity")

    def generate_ondc_product_id(self):
        """Generate unique ONDC product ID.
        Format: PROD-{item_code}-{8char_hash} or PROD-{hash} if no item_code."""
        prefix = f"PROD-{self.item_code}" if self.item_code else "PROD"
        return f"{prefix}-{frappe.generate_hash(length=8).upper()}"

    @frappe.whitelist()
    def sync_to_ondc(self):
        """Sync product to ONDC network"""
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)

        product_data = self.get_ondc_format()
        response = client.update_catalog(product_data)

        if response.get("success"):
            frappe.msgprint("Product synced to ONDC successfully")
            self.db_set("last_sync_date", frappe.utils.now_datetime())
        else:
            frappe.throw(f"Sync failed: {response.get('error')}")

    def get_ondc_format(self):
        """
        Convert to ONDC catalog item format.
        Includes all mandatory ONDC fields: id, descriptor, location_id,
        fulfillment_id, statutory requirements, tags, and @ondc/org/* fields.
        """
        settings = frappe.get_single("ONDC Settings")

        # Build images list
        images = []
        for img in self.images:
            images.append(img.image_url)

        # Build descriptor
        item = {
            "id": self.ondc_product_id,
            "descriptor": {
                "name": self.product_name or "",
                "code": f"1:{self.ondc_product_id.replace('-', '')}",
                "short_desc": self.short_desc or "",
                "long_desc": self.long_desc or "",
                "symbol": images[0] if images else "",
                "images": images,
            },
            "category_id": self.category_code or "",
            "location_id": self.get("location_id") or f"LOC-{settings.city}",
            "fulfillment_id": self.get("fulfillment_id") or "F1",
            "price": {
                "currency": self.currency or "INR",
                "value": str(self.price or 0),
                "maximum_value": str(self.maximum_price or self.price or 0),
            },
            "quantity": {
                "available": {
                    "count": str(max(int(self.available_quantity or 0), 99)),
                },
                "maximum": {
                    "count": str(int(self.maximum_quantity or 999)),
                },
                "unitized": {
                    "measure": {
                        "unit": "unit",
                        "value": str(int(self.minimum_quantity or 1)),
                    }
                },
            },
            # ONDC-specific fields (configurable per product, not hardcoded)
            "@ondc/org/returnable": bool(self.get("is_returnable")),
            "@ondc/org/cancellable": bool(self.get("is_cancellable")),
            "@ondc/org/seller_pickup_return": bool(self.get("seller_pickup_return")),
            "@ondc/org/time_to_ship": self.get("time_to_ship") or settings.get("default_time_to_ship") or "P1D",
            "@ondc/org/return_window": self.get("return_window") or settings.get("default_return_window") or "PT72H",
            "@ondc/org/available_on_cod": bool(self.get("available_on_cod")),
            "@ondc/org/contact_details_consumer_care": (
                self.get("consumer_care_contact")
                or f"{settings.legal_entity_name or 'Support'},{settings.get('consumer_care_email') or ''},{settings.get('consumer_care_phone') or ''}"
            ),
            # Tags in proper ONDC tag group format
            "tags": [
                {
                    "code": "origin",
                    "list": [
                        {"code": "country", "value": self.country_of_origin or "IND"},
                    ],
                },
                {
                    "code": "attribute",
                    "list": [
                        {"code": "brand", "value": self.brand or ""},
                        {"code": "manufacturer", "value": self.manufacturer or ""},
                    ],
                },
            ],
        }

        # Add statutory requirements for packaged commodities (if any field is set)
        statutory_fields = {
            "manufacturer_or_packer_name": self.get("manufacturer_name") or "",
            "manufacturer_or_packer_address": self.get("manufacturer_address") or "",
            "common_or_generic_name_of_commodity": self.get("common_generic_name") or "",
            "net_quantity_or_measure_of_commodity_in_pkg": self.get("net_quantity_or_measure") or "",
            "month_year_of_manufacture_packing_import": self.get("month_year_of_manufacture") or "",
        }

        # Only add statutory reqs if at least one field is set
        has_statutory = any(v for v in statutory_fields.values())
        if has_statutory:
            item["@ondc/org/statutory_reqs_packaged_commodities"] = statutory_fields

        return item
