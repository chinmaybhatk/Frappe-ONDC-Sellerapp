import frappe
from frappe.model.document import Document


class ONDCSettings(Document):
    def validate(self):
        if self.participant_type == "BPP" and not self.webhook_url:
            frappe.throw("Webhook URL is required for BPP participants")

        # Validate domain based on participant type
        if self.participant_type == "BAP" and self.domain and self.domain.startswith("ONDC:LOG"):
            frappe.throw("BAP cannot be registered for logistics domain")

        # Validate GPS format if provided
        if self.get("store_gps"):
            parts = self.store_gps.split(",")
            if len(parts) != 2:
                frappe.throw("Store GPS must be in format: latitude,longitude (e.g. 12.9716,77.5946)")
            try:
                float(parts[0])
                float(parts[1])
            except ValueError:
                frappe.throw("Store GPS coordinates must be valid numbers")

        # Validate operating hours format
        for field in ("operating_hours_start", "operating_hours_end"):
            val = self.get(field)
            if val and ":" not in val:
                frappe.throw(f"{field} must be in HH:MM format (e.g. 09:00)")

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

    @frappe.whitelist()
    def generate_keys(self):
        """Generate Ed25519 signing and X25519 encryption key pairs"""
        import nacl.signing
        import nacl.public
        import base64

        # Generate Ed25519 signing key pair
        signing_key = nacl.signing.SigningKey.generate()
        self.signing_private_key = base64.b64encode(signing_key.encode()).decode()
        self.signing_public_key = base64.b64encode(
            signing_key.verify_key.encode()
        ).decode()

        # Generate X25519 encryption key pair
        enc_key = nacl.public.PrivateKey.generate()
        self.encryption_private_key = base64.b64encode(enc_key.encode()).decode()
        self.encryption_public_key = base64.b64encode(
            enc_key.public_key.encode()
        ).decode()

        self.save()
        frappe.msgprint("Key pairs generated successfully")
