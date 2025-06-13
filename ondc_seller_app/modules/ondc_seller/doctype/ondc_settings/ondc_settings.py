import frappe
from frappe.model.document import Document

class ONDCSettings(Document):
    def validate(self):
        if self.participant_type == "BPP" and not self.webhook_url:
            frappe.throw("Webhook URL is required for BPP participants")
        
        # Validate domain based on participant type
        if self.participant_type == "BAP" and self.domain.startswith("ONDC:LOG"):
            frappe.throw("BAP cannot be registered for logistics domain")
    
    @frappe.whitelist()
    def register_on_network(self):
        """Register the participant on ONDC network"""
        from ondc_seller_app.api.ondc_client import ONDCClient
        
        client = ONDCClient(self)
        response = client.subscribe()
        
        if response.get("success"):
            frappe.msgprint("Successfully registered on ONDC network")
        else:
            frappe.throw(f"Registration failed: {response.get('error')}")