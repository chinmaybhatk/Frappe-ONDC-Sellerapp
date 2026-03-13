import frappe
import requests
import json
import nacl.signing
import nacl.encoding
import nacl.public
import base64
from datetime import datetime, timedelta
import hashlib


class ONDCClient:
    def __init__(self, settings):
        self.settings = settings
        self.base_urls = {
            "staging": {
                "registry": "https://staging.registry.ondc.org",
                "gateway": "https://pilot-gateway-1.beckn.nsdl.co.in",
            },
            "preprod": {
                "registry": "https://preprod.registry.ondc.org",
                "gateway": "https://preprod.gateway.ondc.org",
            },
            "prod": {
                "registry": "https://prod.registry.ondc.org",
                "gateway": "https://prod.gateway.ondc.org",
            },
        }

    # -----------------------------------------------------------------------
    # Authentication
    # -----------------------------------------------------------------------
    def get_auth_header(self, request_body):
        """Generate authorization header for ONDC requests (Ed25519 signing)"""
        created = int(datetime.utcnow().timestamp())
        expires = int((datetime.utcnow() + timedelta(minutes=5)).timestamp())

        signing_string = (
            f"(created): {created}\n"
            f"(expires): {expires}\n"
            f"digest: BLAKE-512={self._calculate_digest(request_body)}"
        )

        # Frappe Password fields must be retrieved via get_password()
        signing_private_key = self.settings.get_password("signing_private_key")
        if not signing_private_key:
            frappe.throw("Signing private key is not set. Please generate key pairs first.")

        # Decode base64 to get raw bytes, then extract 32-byte seed if needed
        raw_key = base64.b64decode(signing_private_key)
        if len(raw_key) == 64:
            # Full Ed25519 key (seed + public key) — take only the 32-byte seed
            raw_key = raw_key[:32]

        # Use nacl to sign
        signing_key = nacl.signing.SigningKey(raw_key)
        public_key_bytes = bytes(signing_key.verify_key)
        signature_bytes = signing_key.sign(signing_string.encode()).signature

        signature = base64.b64encode(signature_bytes).decode()
        public_key = base64.b64encode(public_key_bytes).decode()

        auth_header = (
            f'Signature keyId="{self.settings.subscriber_id}|{self.settings.subscriber_uri}|ed25519",'
            f'algorithm="ed25519",'
            f'created="{created}",'
            f'expires="{expires}",'
            f'headers="(created) (expires) digest",'
            f'signature="{signature}"'
        )
        return auth_header

    def _calculate_digest(self, body):
        """Calculate BLAKE-512 digest of request body"""
        if isinstance(body, str):
            body_bytes = body.encode()
        else:
            body_bytes = body
        digest = hashlib.blake2b(body_bytes).digest()
        return base64.b64encode(digest).decode()

    # -----------------------------------------------------------------------
    # API Calls
    # -----------------------------------------------------------------------
    def call_registry_api(self, url_path, method="GET", data=None):
        """
        Make an API call to the ONDC Registry API.
        """
        env = self.settings.environment or "staging"
        base_url = self.base_urls[env]["registry"]
        url = f"{base_url}{url_path}"

        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(data or ""),
            "Expect": "100-continue",  # Add Expect header to prevent 417 errors
        }

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=30)
            elif method == "POST":
                response = requests.post(url, json=data, headers=headers, timeout=30)
            elif method == "PUT":
                response = requests.put(url, json=data, headers=headers, timeout=30)
            else:
                frappe.throw(f"Unsupported HTTP method: {method}")

            return {
                "status_code": response.status_code,
                "data": response.json() if response.text else None,
            }
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"Registry API Error: {str(e)}", "ONDC Client")
            frappe.throw(f"Failed to call registry API: {str(e)}")

    def call_gateway_api(self, url_path, data=None):
        """
        Make an API call to the ONDC Gateway API.
        """
        env = self.settings.environment or "staging"
        base_url = self.base_urls[env]["gateway"]
        url = f"{base_url}{url_path}"

        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(json.dumps(data) if data else ""),
            "Expect": "100-continue",  # Add Expect header to prevent 417 errors
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=30)
            return {
                "status_code": response.status_code,
                "data": response.json() if response.text else None,
            }
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"Gateway API Error: {str(e)}", "ONDC Client")
            frappe.throw(f"Failed to call gateway API: {str(e)}")

    # -----------------------------------------------------------------------
    # Webhook Validation
    # -----------------------------------------------------------------------
    def validate_webhook_signature(self, request_body, auth_header):
        """
        Validate the signature of incoming ONDC webhook requests
        """
        if not auth_header:
            frappe.throw("Missing Authorization header")

        # Extract signature components
        try:
            auth_parts = {}
            for part in auth_header.split(","):
                key, value = part.strip().split("=", 1)
                auth_parts[key] = value.strip('"')
        except ValueError:
            frappe.throw("Invalid Authorization header format")

        # Validate signature components
        required_parts = ["keyId", "algorithm", "created", "expires", "signature", "headers"]
        for part in required_parts:
            if part not in auth_parts:
                frappe.throw(f"Missing {part} in Authorization header")

        # Check expiry
        expires = int(auth_parts["expires"])
        if datetime.utcnow().timestamp() > expires:
            frappe.throw("Request signature has expired")

        # Extract public key from keyId
        # keyId format: "subscriber_id|subscriber_uri|public_key_format"
        key_id_parts = auth_parts["keyId"].split("|")
        if len(key_id_parts) < 3:
            frappe.throw("Invalid keyId format")

        subscriber_id = key_id_parts[0]
        public_key_base64 = self._get_public_key_from_registry(subscriber_id)
        if not public_key_base64:
            frappe.throw(f"Public key not found for subscriber {subscriber_id}")

        # Decode public key
        try:
            public_key_bytes = base64.b64decode(public_key_base64)
            verify_key = nacl.signing.VerifyKey(public_key_bytes)
        except Exception as e:
            frappe.throw(f"Failed to decode public key: {str(e)}")

        # Reconstruct the signing string
        headers = auth_parts["headers"].split()
        signing_string_parts = []
        for header in headers:
            header_value = None
            if header == "(created)":
                header_value = auth_parts["created"]
            elif header == "(expires)":
                header_value = auth_parts["expires"]
            elif header == "digest":
                header_value = f"BLAKE-512={self._calculate_digest(request_body)}"
            else:
                frappe.throw(f"Unknown header in signing string: {header}")
            signing_string_parts.append(f"{header}: {header_value}")

        signing_string = "\n".join(signing_string_parts)

        # Verify signature
        try:
            signature = base64.b64decode(auth_parts["signature"])
            verify_key.verify(signing_string.encode(), signature)
            return True
        except Exception as e:
            frappe.log_error(f"Signature verification failed: {str(e)}", "ONDC Client")
            return False

    def _get_public_key_from_registry(self, subscriber_id):
        """
        Fetch the public key of a subscriber from the ONDC Registry
        """
        try:
            response = self.call_registry_api(f"/subscribers/{subscriber_id}")
            if response["status_code"] == 200:
                # Extract public key from response
                data = response["data"]
                if isinstance(data, list) and len(data) > 0:
                    return data[0].get("signing_public_key")
                elif isinstance(data, dict):
                    return data.get("signing_public_key")
            return None
        except Exception as e:
            frappe.log_error(
                f"Failed to fetch public key for {subscriber_id}: {str(e)}",
                "ONDC Client",
            )
            return None

    # -----------------------------------------------------------------------
    # Lookup & Discovery
    # -----------------------------------------------------------------------
    def search_products(self, search_params, **kwargs):
        """
        Search for products on ONDC network.
        Query format: {"query": {"intent": {...}}}
        """
        message = {
            "context": self._get_context("search", **kwargs),
            "message": {"intent": search_params},
        }
        return self.call_gateway_api("/search", message)

    def select_products(self, cart_items, **kwargs):
        """
        Select items from search results.
        Cart format: {"order": {"items": [...], "provider": {...}}}
        """
        message = {
            "context": self._get_context("select", **kwargs),
            "message": {"order": cart_items},
        }
        return self.call_gateway_api("/select", message)

    def get_quotation(self, order_details, **kwargs):
        """
        Get quotation for selected items.
        Order format: {"order": {...}}
        """
        message = {
            "context": self._get_context("init", **kwargs),
            "message": {"order": order_details},
        }
        return self.call_gateway_api("/init", message)

    def confirm_order(self, order_details, **kwargs):
        """
        Confirm an order.
        Order format: {"order": {...}}
        """
        message = {
            "context": self._get_context("confirm", **kwargs),
            "message": {"order": order_details},
        }
        return self.call_gateway_api("/confirm", message)

    def track_order(self, order_id, **kwargs):
        """
        Track order status.
        """
        message = {
            "context": self._get_context("track", **kwargs),
            "message": {"order_id": order_id},
        }
        return self.call_gateway_api("/track", message)

    def cancel_order(self, order_id, cancellation_reason_id=None, **kwargs):
        """
        Cancel an order.
        """
        message = {
            "context": self._get_context("cancel", **kwargs),
            "message": {
                "order_id": order_id,
                "cancellation_reason_id": cancellation_reason_id,
            },
        }
        return self.call_gateway_api("/cancel", message)

    def get_order_status(self, order_id, **kwargs):
        """
        Get status of an order.
        """
        message = {
            "context": self._get_context("status", **kwargs),
            "message": {"order_id": order_id},
        }
        return self.call_gateway_api("/status", message)

    def update_order(self, order_details, **kwargs):
        """
        Update an order (for seller order updates like pickup confirmation).
        Order format: {"order": {...}}
        """
        message = {
            "context": self._get_context("update", **kwargs),
            "message": {"order": order_details},
        }
        return self.call_gateway_api("/update", message)

    # -----------------------------------------------------------------------
    # Helper Methods
    # -----------------------------------------------------------------------
    def _get_context(self, action, **kwargs):
        """
        Build the context for an ONDC request.
        """
        context = {
            "domain": self.settings.domain or "nic2:category:sub_category",
            "action": action,
            "core_version": "1.0.0",
            "bap_id": self.settings.bap_id,
            "bap_uri": self.settings.bap_uri,
            "bpp_id": kwargs.get("bpp_id"),
            "bpp_uri": kwargs.get("bpp_uri"),
            "transaction_id": kwargs.get("transaction_id", self._generate_id()),
            "message_id": kwargs.get("message_id", self._generate_id()),
            "city": kwargs.get("city", "*"),
            "country": "IND",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "key": self.settings.subscriber_id,
        }
        return {k: v for k, v in context.items() if v is not None}

    @staticmethod
    def _generate_id():
        """
        Generate a unique ID for transactions and messages.
        """
        import uuid
        return str(uuid.uuid4())
