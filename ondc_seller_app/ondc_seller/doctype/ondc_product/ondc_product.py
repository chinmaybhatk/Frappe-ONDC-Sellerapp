import frappe
from frappe.model.document import Document
import json

class ONDCProduct(Document):
    def validate(self):
        if not self.ondc_product_id:
            self.ondc_product_id = self.generate_ondc_product_id()
        
        # Validate minimum and maximum quantity
        if self.minimum_quantity and self.maximum_quantity:
            if self.minimum_quantity > self.maximum_quantity:
                frappe.throw("Minimum quantity cannot be greater than maximum quantity")
    
    def generate_ondc_product_id(self):
        """Generate unique ONDC product ID"""
        return f"PROD-{self.item_code}-{frappe.generate_hash(length=8).upper()}"
    
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
        """Convert to ONDC catalog format"""
        return {
            "descriptor": {
                "name": self.product_name,
                "code": self.ondc_product_id,
                "short_desc": self.short_desc,
                "long_desc": self.long_desc,
                "images": [{
                    "url": img.image_url,
                    "size_type": img.size_type
                } for img in self.images]
            },
            "category_id": self.category_code,
            "price": {
                "currency": "INR",
                "value": str(self.price),
                "maximum_value": str(self.maximum_price or self.price)
            },
            "quantity": {
                "available": {
                    "count": int(self.available_quantity or 0)
                },
                "maximum": {
                    "count": int(self.maximum_quantity or 999)
                },
                "minimum": {
                    "count": int(self.minimum_quantity or 1)
                }
            },
            "@ondc/org/returnable": True,
            "@ondc/org/cancellable": True,
            "@ondc/org/seller_pickup_return": True,
            "@ondc/org/time_to_ship": "P1D",
            "@ondc/org/available_on_cod": True,
            "@ondc/org/contact_details_consumer_care": settings.webhook_url,
            "tags": {
                "brand": self.brand,
                "manufacturer": self.manufacturer,
                "country_of_origin": self.country_of_origin
            }
        }