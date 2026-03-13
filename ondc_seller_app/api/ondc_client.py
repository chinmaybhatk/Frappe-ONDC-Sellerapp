import frappe
import requests
import json
import nacl.signing
import nacl.encoding
import nacl.public
import base64
from datetime import datetime, timedelta
import hashlib
import uuid


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
    # Context Builder
    # -----------------------------------------------------------------------
    def create_context(self, action, original_context):
        """Build a BPP response context from the incoming request context.

        Args:
            action: The callback action name (e.g. "on_search", "on_status")
            original_context: The context dict from the incoming request

        Returns:
            dict with properly formed ONDC context for the callback
        """
        if not original_context:
            original_context = {}

        return {
            "domain": original_context.get("domain", "ONDC:RET10"),
            "country": original_context.get("country", "IND"),
            "city": original_context.get("city", "*"),
            "action": action,
            "core_version": original_context.get("core_version", "1.2.0"),
            "bap_id": original_context.get("bap_id", ""),
            "bap_uri": original_context.get("bap_uri", ""),
            "bpp_id": self.settings.subscriber_id,
            "bpp_uri": self.settings.subscriber_url,
            "transaction_id": original_context.get("transaction_id", ""),
            "message_id": str(uuid.uuid4()),  # Always generate a new unique message_id for responses
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
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

        signing_key = nacl.signing.SigningKey(raw_key)
        message = signing_string.encode()
        signed = signing_key.sign(message)
        signature_bytes = signed.signature
        signature_base64 = base64.b64encode(signature_bytes).decode()

        # FIX BUG #1: Use unique_key_id (the actual ONDC Settings field name),
        # NOT public_key_id which doesn't exist on the DocType
        auth_header = (
            f'Signature keyId="{self.settings.subscriber_id}|{self.settings.unique_key_id}|ed25519",'
            f'algorithm="ed25519",'
            f'created="{created}",'
            f'expires="{expires}",'
            f'headers="(created) (expires) digest",'
            f'signature="{signature_base64}"'
        )

        return auth_header

    def _calculate_digest(self, request_body):
        """Calculate BLAKE-512 digest of request body"""
        if isinstance(request_body, dict):
            body_str = json.dumps(request_body, separators=(',', ':'), sort_keys=True)
        else:
            body_str = request_body if isinstance(request_body, str) else str(request_body)
        return base64.b64encode(hashlib.blake2b(body_str.encode()).digest()).decode()

    # -----------------------------------------------------------------------
    # Callback Sending
    # -----------------------------------------------------------------------
    def send_callback(self, bap_uri, endpoint, payload):
        """Send callback response to BAP/buyer app.

        Args:
            bap_uri: The BAP subscriber URI (e.g. https://pramaan.ondc.org/alpha)
            endpoint: The callback endpoint (e.g. /on_search, /on_select)
            payload: The response payload dict to send

        Returns:
            dict with status and response details

        IMPORTANT: We serialize the payload to bytes ONCE using the same compact
        format (sort_keys=True, no spaces) that _calculate_digest uses. This
        ensures the BLAKE-512 digest in the Authorization header matches the
        actual body bytes transmitted — fixing the "Invalid Signature" (10001) error.
        """
        try:
            callback_url = f"{bap_uri.rstrip('/')}{endpoint}"

            # Serialize payload to bytes ONCE — same compact format used in digest
            # CRITICAL: sort_keys + compact separators must match _calculate_digest exactly
            body_bytes = json.dumps(
                payload, separators=(',', ':'), sort_keys=True
            ).encode('utf-8')

            # Build auth header AFTER serializing so digest is over exact bytes being sent
            auth_header = self.get_auth_header(body_bytes.decode('utf-8'))

            # Build headers
            headers = self._get_common_headers()
            headers["Expect"] = ""  # Prevent 417 Expectation Failed from nginx
            headers["Authorization"] = auth_header

            frappe.log_error(
                f"Sending callback to {callback_url} ({len(body_bytes)} bytes)",
                "ONDC Callback Debug"
            )

            response = requests.post(
                callback_url,
                data=body_bytes,   # Send pre-serialized bytes, not json=payload
                headers=headers,
                timeout=30,
            )

            frappe.log_error(
                f"Callback {endpoint}: HTTP {response.status_code} - {response.text[:200]}",
                "ONDC Callback Response"
            )

            return {
                "status": "success" if response.status_code in (200, 202) else "error",
                "status_code": response.status_code,
                "response": response.text[:1000],
                "callback_url": callback_url,
            }
        except Exception as e:
            frappe.log_error(
                f"Error sending callback to {endpoint}: {str(e)}",
                "ONDC Callback Error",
            )
            return {
                "status": "error",
                "error": str(e),
                "callback_url": f"{bap_uri}{endpoint}",
            }

    # -----------------------------------------------------------------------
    # Callback Wrapper Methods (construct + send)
    # -----------------------------------------------------------------------
    def on_search(self, data):
        """Process search request: construct on_search payload and send callback to BAP"""
        try:
            payload = self.construct_on_search(data)
            bap_uri = data["context"]["bap_uri"]
            result = self.send_callback(bap_uri, "/on_search", payload)
            return result
        except Exception as e:
            frappe.log_error(
                f"Error in on_search: {str(e)}",
                "ONDC on_search Error",
            )
            raise

    def on_select(self, data):
        """Process select request: construct on_select payload and send callback to BAP"""
        try:
            payload = self.construct_on_select(data)
            bap_uri = data["context"]["bap_uri"]
            result = self.send_callback(bap_uri, "/on_select", payload)
            return result
        except Exception as e:
            frappe.log_error(
                f"Error in on_select: {str(e)}",
                "ONDC on_select Error",
            )
            raise

    def on_init(self, data):
        """Process init request: construct on_init payload and send callback to BAP"""
        try:
            payload = self.construct_on_init(data)
            bap_uri = data["context"]["bap_uri"]
            result = self.send_callback(bap_uri, "/on_init", payload)
            return result
        except Exception as e:
            frappe.log_error(
                f"Error in on_init: {str(e)}",
                "ONDC on_init Error",
            )
            raise

    def on_confirm(self, data):
        """Process confirm request: construct on_confirm payload and send callback to BAP"""
        try:
            payload = self.construct_on_confirm(data)
            bap_uri = data["context"]["bap_uri"]
            result = self.send_callback(bap_uri, "/on_confirm", payload)
            return result
        except Exception as e:
            frappe.log_error(
                f"Error in on_confirm: {str(e)}",
                "ONDC on_confirm Error",
            )
            raise

    def on_status(self, data):
        """Process status request: construct on_status payload and send callback to BAP"""
        try:
            payload = self.construct_on_status(data)
            bap_uri = data["context"]["bap_uri"]
            result = self.send_callback(bap_uri, "/on_status", payload)
            return result
        except Exception as e:
            frappe.log_error(
                f"Error in on_status: {str(e)}",
                "ONDC on_status Error",
            )
            raise

    def on_update(self, data):
        """Process update request: construct on_update payload and send callback to BAP"""
        try:
            payload = self.construct_on_update(data)
            bap_uri = data["context"]["bap_uri"]
            result = self.send_callback(bap_uri, "/on_update", payload)
            return result
        except Exception as e:
            frappe.log_error(
                f"Error in on_update: {str(e)}",
                "ONDC on_update Error",
            )
            raise

    def on_cancel(self, data):
        """Process cancel request: construct on_cancel payload and send callback to BAP"""
        try:
            payload = self.construct_on_cancel(data)
            bap_uri = data["context"]["bap_uri"]
            result = self.send_callback(bap_uri, "/on_cancel", payload)
            return result
        except Exception as e:
            frappe.log_error(
                f"Error in on_cancel: {str(e)}",
                "ONDC on_cancel Error",
            )
            raise

    # -----------------------------------------------------------------------
    # Registry Operations
    # -----------------------------------------------------------------------
    def get_registry_url(self):
        """Get registry URL based on environment"""
        env = self.settings.get("environment") or "preprod"
        return self.base_urls.get(env, self.base_urls["preprod"])["registry"]

    def get_gateway_url(self):
        """Get gateway URL based on environment"""
        env = self.settings.get("environment") or "preprod"
        return self.base_urls.get(env, self.base_urls["preprod"])["gateway"]

    def get_registry_list(self):
        """Fetch subscriber list from ONDC registry"""
        try:
            registry_url = self.get_registry_url()
            response = requests.get(f"{registry_url}/subscribers", timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            frappe.log_error(f"Error fetching registry list: {str(e)}", "ONDC Registry")
            raise

    def lookup_subscriber(self, subscriber_id=None, domain=None):
        """Look up a subscriber in the ONDC registry"""
        try:
            registry_url = self.get_registry_url()
            payload = {}
            if subscriber_id:
                payload["subscriber_id"] = subscriber_id
            if domain:
                payload["domain"] = domain

            headers = self._get_common_headers()
            response = requests.post(
                f"{registry_url}/lookup",
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            frappe.log_error(
                f"Lookup error for {subscriber_id}: {str(e)}",
                "ONDC Lookup"
            )
            raise

    def lookup_subscriber_key(self, subscriber_id, unique_key_id):
        """Look up a subscriber's public key from the ONDC registry"""
        try:
            registry_url = self.get_registry_url()
            payload = {
                "subscriber_id": subscriber_id,
                "unique_key_id": unique_key_id,
            }
            headers = self._get_common_headers()
            response = requests.post(
                f"{registry_url}/lookup",
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            subscribers = response.json()
            if subscribers:
                return subscribers[0].get("signing_public_key", "")
            return ""
        except Exception as e:
            frappe.log_error(f"Lookup error: {str(e)}", "ONDC Lookup")
            raise

    def send_to_gateway(self, action, payload):
        """Send a request to the ONDC gateway"""
        try:
            gateway_url = self.get_gateway_url()
            headers = self._get_common_headers()
            headers["Authorization"] = self.get_auth_header(payload)

            response = requests.post(
                f"{gateway_url}/{action}",
                json=payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            frappe.log_error(f"Send error on {action}: {str(e)}", "ONDC Send")
            raise

    def _get_common_headers(self):
        """Get headers common to all requests"""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get_gateway_auth_header(
        self, message_id, transaction_id, request_body
    ):
        """Generate authorization header for gateway requests"""
        created = int(datetime.utcnow().timestamp())
        expires = int((datetime.utcnow() + timedelta(minutes=5)).timestamp())

        signing_string = (
            f"(created): {created}\n"
            f"(expires): {expires}\n"
            f"digest: BLAKE-512={self._calculate_digest(request_body)}"
        )

        signing_private_key = self.settings.get_password("signing_private_key")
        if not signing_private_key:
            frappe.throw("Signing private key is not set.")

        raw_key = base64.b64decode(signing_private_key)
        if len(raw_key) == 64:
            raw_key = raw_key[:32]

        signing_key = nacl.signing.SigningKey(raw_key)
        message = signing_string.encode()
        signed = signing_key.sign(message)
        signature_bytes = signed.signature
        signature_base64 = base64.b64encode(signature_bytes).decode()

        auth_header = (
            f'Signature keyId="{message_id}",'
            f'algorithm="ed25519",'
            f'created="{created}",'
            f'expires="{expires}",'
            f'headers="(created) (expires) digest",'
            f'signature="{signature_base64}"'
        )

        return auth_header

    # -----------------------------------------------------------------------
    # Encryption / Decryption
    # -----------------------------------------------------------------------
    def encrypt_ack_key(self, buyer_public_key_b64):
        """Encrypt the ACK key using buyer's public key (X25519 ECDH)"""
        try:
            # Generate a random encryption private key
            enc_private_key = nacl.public.PrivateKey.generate()

            # Decode buyer's public key
            buyer_pub_bytes = base64.b64decode(buyer_public_key_b64)
            buyer_public_key = nacl.public.PublicKey(buyer_pub_bytes)

            # Create a shared box
            box = nacl.public.Box(enc_private_key, buyer_public_key)

            # Generate a random message
            message = nacl.utils.random(32)
            encrypted = box.encrypt(message)

            return {
                "encrypted_key": base64.b64encode(encrypted).decode(),
                "public_key": base64.b64encode(bytes(enc_private_key.public_key)).decode(),
            }
        except Exception as e:
            frappe.log_error(f"Encryption error: {str(e)}", "ONDC Encryption")
            raise

    def decrypt_ack_key(self, encrypted_key_b64, buyer_public_key_b64):
        """Decrypt the ACK key using our private key and buyer's public key"""
        try:
            # Get our encryption private key
            enc_private_key_b64 = self.settings.get_password("encryption_private_key")
            if not enc_private_key_b64:
                frappe.throw("Encryption private key is not set.")

            raw_enc_key = base64.b64decode(enc_private_key_b64)
            if len(raw_enc_key) == 64:
                raw_enc_key = raw_enc_key[:32]

            enc_private_key = nacl.public.PrivateKey(raw_enc_key)

            # Decode buyer's public key
            buyer_pub_bytes = base64.b64decode(buyer_public_key_b64)
            buyer_public_key = nacl.public.PublicKey(buyer_pub_bytes)

            # Create a shared box and decrypt
            box = nacl.public.Box(enc_private_key, buyer_public_key)
            encrypted_bytes = base64.b64decode(encrypted_key_b64)
            decrypted = box.decrypt(encrypted_bytes)

            return base64.b64encode(decrypted).decode()
        except Exception as e:
            frappe.log_error(f"Decryption error: {str(e)}", "ONDC Decryption")
            raise

    # -----------------------------------------------------------------------
    # Incoming Request Verification
    # -----------------------------------------------------------------------
    def verify_auth_header(self, auth_header, request_body):
        """Verify an incoming ONDC Authorization header"""
        try:
            # Parse keyId from header
            import re
            key_id_match = re.search(r'keyId="([^"]+)"', auth_header)
            if not key_id_match:
                return False, "Missing keyId in Authorization header"

            key_id = key_id_match.group(1)
            parts = key_id.split("|")
            if len(parts) != 3:
                return False, f"Invalid keyId format: {key_id}"

            subscriber_id, unique_key_id, algorithm = parts

            # Look up public key from registry
            public_key_b64 = self.lookup_subscriber_key(subscriber_id, unique_key_id)
            if not public_key_b64:
                return False, f"Public key not found for {subscriber_id}|{unique_key_id}"

            # Extract signature and timestamps
            sig_match = re.search(r'signature="([^"]+)"', auth_header)
            created_match = re.search(r'created="(\d+)"', auth_header)
            expires_match = re.search(r'expires="(\d+)"', auth_header)

            if not all([sig_match, created_match, expires_match]):
                return False, "Missing required Authorization header fields"

            signature_b64 = sig_match.group(1)
            created = int(created_match.group(1))
            expires = int(expires_match.group(1))

            # Check expiry
            now = int(datetime.utcnow().timestamp())
            if now > expires:
                return False, "Authorization header has expired"

            # Reconstruct signing string
            digest = self._calculate_digest(request_body)
            signing_string = (
                f"(created): {created}\n"
                f"(expires): {expires}\n"
                f"digest: BLAKE-512={digest}"
            )

            # Verify signature
            raw_pub_key = base64.b64decode(public_key_b64)
            verify_key = nacl.signing.VerifyKey(raw_pub_key)
            signature_bytes = base64.b64decode(signature_b64)

            verify_key.verify(signing_string.encode(), signature_bytes)
            return True, "Signature verified successfully"

        except nacl.exceptions.BadSignatureError:
            return False, "Invalid signature"
        except Exception as e:
            frappe.log_error(
                f"Auth verification error: {str(e)}",
                "ONDC Auth Verify",
            )
            return False, f"Verification error: {str(e)}"

    # -----------------------------------------------------------------------
    # on_search Payload Construction
    # -----------------------------------------------------------------------
    def construct_on_search(self, search_params):
        """Construct on_search callback body"""
        try:
            response_body = {
                "context": self.create_context("on_search", search_params.get("context", {})),
                "message": {
                    "catalog": self._get_catalog(),
                },
            }
            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_search: {str(e)}",
                "ONDC On Search",
            )
            raise

    def construct_on_select(self, select_request):
        """Construct on_select callback body.

        FIX BUG #3: Look up item prices from ONDC Product catalog instead of
        assuming item["price"]["value"] exists in the select request from BAP.
        In ONDC protocol, select from BAP sends item IDs and quantities; the BPP
        must look up prices from its own catalog.
        """
        try:
            selected_items = select_request["message"]["order"]["items"]
            order_value = 0
            quote_items = []
            quote_breakup = []
            tax_rate = float(self.settings.get("default_tax_rate") or 0)
            total_tax = 0.0

            # Build a price lookup from our catalog
            product_price_map = {}
            products = frappe.get_all(
                "ONDC Product",
                filters={"is_active": 1},
                fields=["ondc_product_id", "name", "product_name", "price"]
            )
            for p in products:
                product_price_map[p.ondc_product_id or p.name] = {
                    "price": float(p.price or 0),
                    "name": p.product_name,
                }

            for item in selected_items:
                item_id = item.get("id")
                item_quantity = int(item.get("quantity", {}).get("count", 1))

                # Try to get price from the request first (some BAPs include it),
                # otherwise look up from our catalog
                if item.get("price") and item["price"].get("value"):
                    item_price = float(item["price"]["value"])
                elif item_id in product_price_map:
                    item_price = product_price_map[item_id]["price"]
                else:
                    frappe.log_error(
                        f"Item {item_id} not found in catalog, using 0",
                        "ONDC on_select Warning"
                    )
                    item_price = 0

                item_total = item_price * item_quantity
                order_value += item_total

                quote_items.append(
                    {
                        "id": item_id,
                        "quantity": {"count": item_quantity},
                        "fulfillment_id": "F1",
                    }
                )

                # Item breakup entry
                quote_breakup.append({
                    "title": product_price_map.get(item_id, {}).get("name", item_id),
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/item_quantity": {"count": item_quantity},
                    "@ondc/org/title_type": "item",
                    "price": {"currency": "INR", "value": str(item_total)},
                    "item": {
                        "price": {"currency": "INR", "value": str(item_price)},
                        "quantity": {
                            "available": {"count": str(item_quantity)},
                            "maximum": {"count": "10"},
                        },
                    },
                })

                # Tax breakup
                item_tax = round(item_total * tax_rate / 100, 2) if tax_rate > 0 else 0
                total_tax += item_tax
                quote_breakup.append({
                    "title": "Tax",
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/title_type": "tax",
                    "price": {"currency": "INR", "value": str(item_tax)},
                })

            # Delivery charges breakup
            delivery_charge = float(self.settings.get("default_delivery_charge") or 0)
            quote_breakup.append({
                "title": "Delivery charges",
                "@ondc/org/item_id": "F1",
                "@ondc/org/title_type": "delivery",
                "price": {"currency": "INR", "value": str(delivery_charge)},
            })

            total_value = order_value + total_tax + delivery_charge

            quote = {
                "price": {
                    "currency": "INR",
                    "value": str(total_value),
                },
                "breakup": quote_breakup,
                "ttl": "P1D",
            }

            # Build provider info
            provider_id = self.settings.subscriber_id
            fulfillments = select_request["message"]["order"].get("fulfillments",
                select_request["message"]["order"].get("fulfillment", []))
            if isinstance(fulfillments, dict):
                fulfillments = [fulfillments]

            response_body = {
                "context": self.create_context("on_select", select_request.get("context", {})),
                "message": {
                    "order": {
                        "provider": {
                            "id": provider_id,
                        },
                        "items": quote_items,
                        "fulfillments": [{
                            "id": "F1",
                            "type": "Delivery",
                            "@ondc/org/provider_name": self.settings.store_name or provider_id,
                            "@ondc/org/category": "Standard Delivery",
                            "tracking": False,
                            "state": {"descriptor": {"code": "Serviceable"}},
                        }],
                        "quote": quote,
                    }
                },
            }

            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_select: {str(e)}\n{frappe.get_traceback()}",
                "ONDC On Select",
            )
            raise

    def construct_on_init(self, init_request):
        """Construct on_init callback body"""
        try:
            order = init_request["message"]["order"]
            selected_items = order.get("items", [])
            order_value = 0
            quote_items = []
            quote_breakup = []
            tax_rate = float(self.settings.get("default_tax_rate") or 0)
            total_tax = 0.0

            # Build a price lookup from our catalog
            product_price_map = {}
            products = frappe.get_all(
                "ONDC Product",
                filters={"is_active": 1},
                fields=["ondc_product_id", "name", "product_name", "price"]
            )
            for p in products:
                product_price_map[p.ondc_product_id or p.name] = {
                    "price": float(p.price or 0),
                    "name": p.product_name,
                }

            for item in selected_items:
                item_id = item.get("id")
                item_qty = int(item.get("quantity", {}).get("count", 1))

                if item.get("price") and item["price"].get("value"):
                    item_price = float(item["price"]["value"])
                elif item_id in product_price_map:
                    item_price = product_price_map[item_id]["price"]
                else:
                    item_price = 0

                item_total = item_price * item_qty
                order_value += item_total

                quote_items.append({
                    "id": item_id,
                    "quantity": {"count": item_qty},
                    "fulfillment_id": "F1",
                })

                quote_breakup.append({
                    "title": product_price_map.get(item_id, {}).get("name", item_id),
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/item_quantity": {"count": item_qty},
                    "@ondc/org/title_type": "item",
                    "price": {"currency": "INR", "value": str(item_total)},
                    "item": {
                        "price": {"currency": "INR", "value": str(item_price)},
                        "quantity": {
                            "available": {"count": str(item_qty)},
                            "maximum": {"count": "10"},
                        },
                    },
                })

                item_tax = round(item_total * tax_rate / 100, 2) if tax_rate > 0 else 0
                total_tax += item_tax
                quote_breakup.append({
                    "title": "Tax",
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/title_type": "tax",
                    "price": {"currency": "INR", "value": str(item_tax)},
                })

            delivery_charge = float(self.settings.get("default_delivery_charge") or 0)
            quote_breakup.append({
                "title": "Delivery charges",
                "@ondc/org/item_id": "F1",
                "@ondc/org/title_type": "delivery",
                "price": {"currency": "INR", "value": str(delivery_charge)},
            })

            total_value = order_value + total_tax + delivery_charge

            # Build billing info
            billing = order.get("billing", {})
            fulfillments = order.get("fulfillments", order.get("fulfillment", []))
            if isinstance(fulfillments, dict):
                fulfillments = [fulfillments]

            provider_id = self.settings.subscriber_id

            response_body = {
                "context": self.create_context("on_init", init_request.get("context", {})),
                "message": {
                    "order": {
                        "provider": {"id": provider_id},
                        "items": quote_items,
                        "billing": billing,
                        "fulfillments": [{
                            "id": "F1",
                            "type": "Delivery",
                            "@ondc/org/provider_name": self.settings.store_name or provider_id,
                            "@ondc/org/category": "Standard Delivery",
                            "tracking": False,
                            "end": fulfillments[0].get("end", {}) if fulfillments else {},
                            "state": {"descriptor": {"code": "Serviceable"}},
                        }],
                        "quote": {
                            "price": {"currency": "INR", "value": str(total_value)},
                            "breakup": quote_breakup,
                            "ttl": "P1D",
                        },
                        "payment": {
                            "@ondc/org/buyer_app_finder_fee_type": "percent",
                            "@ondc/org/buyer_app_finder_fee_amount": "3",
                            "@ondc/org/settlement_details": [{
                                "settlement_counterparty": "buyer-app",
                                "settlement_phase": "sale-amount",
                                "settlement_type": "upi",
                                "upi_address": self.settings.upi_address or "",
                                "settlement_bank_account_no": self.settings.bank_account_no or "",
                                "settlement_ifsc_code": self.settings.ifsc_code or "",
                                "bank_name": self.settings.bank_name or "",
                                "branch_name": self.settings.branch_name or "",
                            }],
                        },
                    }
                },
            }

            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_init: {str(e)}\n{frappe.get_traceback()}",
                "ONDC On Init",
            )
            raise

    def construct_on_confirm(self, confirm_request):
        """Construct on_confirm callback body.

        FIX BUG #2: Look up the order ID from ONDC Order DocType using the
        transaction_id from context, rather than assuming order ID comes from
        the confirm request payload (BAP does not send an order ID in confirm).
        """
        try:
            context = confirm_request.get("context", {})
            transaction_id = context.get("transaction_id", "")
            order_data = confirm_request["message"]["order"]

            # Generate a unique order ID or look up existing one by transaction_id
            order_id = None
            existing_orders = frappe.get_all(
                "ONDC Order",
                filters={"transaction_id": transaction_id},
                fields=["name"],
                limit=1
            )
            if existing_orders:
                order_id = existing_orders[0].name
            else:
                order_id = f"ORD-{transaction_id[:8].upper()}"

            items = order_data.get("items", [])
            quote_items = []
            quote_breakup = []
            order_value = 0
            tax_rate = float(self.settings.get("default_tax_rate") or 0)
            total_tax = 0.0

            # Build a price lookup from our catalog
            product_price_map = {}
            products = frappe.get_all(
                "ONDC Product",
                filters={"is_active": 1},
                fields=["ondc_product_id", "name", "product_name", "price"]
            )
            for p in products:
                product_price_map[p.ondc_product_id or p.name] = {
                    "price": float(p.price or 0),
                    "name": p.product_name,
                }

            for item in items:
                item_id = item.get("id")
                item_qty = int(item.get("quantity", {}).get("count", 1))

                if item.get("price") and item["price"].get("value"):
                    item_price = float(item["price"]["value"])
                elif item_id in product_price_map:
                    item_price = product_price_map[item_id]["price"]
                else:
                    item_price = 0

                item_total = item_price * item_qty
                order_value += item_total

                quote_items.append({
                    "id": item_id,
                    "quantity": {"count": item_qty},
                    "fulfillment_id": "F1",
                })

                quote_breakup.append({
                    "title": product_price_map.get(item_id, {}).get("name", item_id),
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/item_quantity": {"count": item_qty},
                    "@ondc/org/title_type": "item",
                    "price": {"currency": "INR", "value": str(item_total)},
                    "item": {
                        "price": {"currency": "INR", "value": str(item_price)},
                        "quantity": {
                            "available": {"count": str(item_qty)},
                            "maximum": {"count": "10"},
                        },
                    },
                })

                item_tax = round(item_total * tax_rate / 100, 2) if tax_rate > 0 else 0
                total_tax += item_tax
                quote_breakup.append({
                    "title": "Tax",
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/title_type": "tax",
                    "price": {"currency": "INR", "value": str(item_tax)},
                })

            delivery_charge = float(self.settings.get("default_delivery_charge") or 0)
            quote_breakup.append({
                "title": "Delivery charges",
                "@ondc/org/item_id": "F1",
                "@ondc/org/title_type": "delivery",
                "price": {"currency": "INR", "value": str(delivery_charge)},
            })

            total_value = order_value + total_tax + delivery_charge

            billing = order_data.get("billing", {})
            fulfillments = order_data.get("fulfillments", order_data.get("fulfillment", []))
            if isinstance(fulfillments, dict):
                fulfillments = [fulfillments]

            provider_id = self.settings.subscriber_id

            response_body = {
                "context": self.create_context("on_confirm", confirm_request.get("context", {})),
                "message": {
                    "order": {
                        "id": order_id,
                        "state": "Accepted",
                        "provider": {"id": provider_id},
                        "items": quote_items,
                        "billing": billing,
                        "fulfillments": [{
                            "id": "F1",
                            "type": "Delivery",
                            "@ondc/org/provider_name": self.settings.store_name or provider_id,
                            "@ondc/org/category": "Standard Delivery",
                            "tracking": False,
                            "state": {"descriptor": {"code": "Pending"}},
                            "end": fulfillments[0].get("end", {}) if fulfillments else {},
                        }],
                        "quote": {
                            "price": {"currency": "INR", "value": str(total_value)},
                            "breakup": quote_breakup,
                            "ttl": "P1D",
                        },
                        "payment": {
                            "uri": order_data.get("payment", {}).get("uri", ""),
                            "tl_method": order_data.get("payment", {}).get("tl_method", ""),
                            "params": order_data.get("payment", {}).get("params", {}),
                            "type": order_data.get("payment", {}).get("type", "ON-ORDER"),
                            "status": "PAID",
                            "@ondc/org/buyer_app_finder_fee_type": "percent",
                            "@ondc/org/buyer_app_finder_fee_amount": "3",
                            "@ondc/org/settlement_details": [{
                                "settlement_counterparty": "buyer-app",
                                "settlement_phase": "sale-amount",
                                "settlement_type": "upi",
                                "upi_address": self.settings.upi_address or "",
                                "settlement_bank_account_no": self.settings.bank_account_no or "",
                                "settlement_ifsc_code": self.settings.ifsc_code or "",
                                "bank_name": self.settings.bank_name or "",
                                "branch_name": self.settings.branch_name or "",
                            }],
                        },
                    }
                },
            }

            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_confirm: {str(e)}\n{frappe.get_traceback()}",
                "ONDC On Confirm",
            )
            raise

    def construct_on_status(self, status_request):
        """Construct on_status callback body"""
        try:
            context = status_request.get("context", {})
            transaction_id = context.get("transaction_id", "")
            order_message = status_request.get("message", {}).get("order", {})
            order_id = order_message.get("id", "")

            # Look up order from DB
            order_data = None
            if order_id:
                try:
                    order_data = frappe.get_doc("ONDC Order", order_id)
                except frappe.DoesNotExistError:
                    pass

            if not order_data and transaction_id:
                existing = frappe.get_all(
                    "ONDC Order",
                    filters={"transaction_id": transaction_id},
                    fields=["name"],
                    limit=1
                )
                if existing:
                    order_data = frappe.get_doc("ONDC Order", existing[0].name)

            if not order_data:
                # Return a minimal status response if order not found
                frappe.log_error(
                    f"Order not found for status: order_id={order_id}, txn={transaction_id}",
                    "ONDC on_status Warning",
                )
                return {
                    "context": self.create_context("on_status", context),
                    "message": {
                        "order": {
                            "id": order_id or f"ORD-{transaction_id[:8].upper()}",
                            "state": "Accepted",
                        }
                    },
                }

            provider_id = self.settings.subscriber_id
            order_state = order_data.get("order_status", "Accepted")
            fulfillment_state = order_data.get("fulfillment_status", "Pending")

            response_body = {
                "context": self.create_context("on_status", context),
                "message": {
                    "order": {
                        "id": order_data.name,
                        "state": order_state,
                        "provider": {"id": provider_id},
                        "fulfillments": [{
                            "id": "F1",
                            "type": "Delivery",
                            "state": {"descriptor": {"code": fulfillment_state}},
                            "tracking": False,
                        }],
                    }
                },
            }

            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_status: {str(e)}",
                "ONDC On Status",
            )
            raise

    def construct_on_update(self, update_request):
        """Construct on_update callback body"""
        try:
            context = update_request.get("context", {})
            order_message = update_request.get("message", {}).get("order", {})
            order_id = order_message.get("id", "")

            # Get order from DB
            order_data = None
            if order_id:
                try:
                    order_data = frappe.get_doc("ONDC Order", order_id)
                except frappe.DoesNotExistError:
                    pass

            provider_id = self.settings.subscriber_id
            items = order_message.get("items", [])

            response_body = {
                "context": self.create_context("on_update", context),
                "message": {
                    "order": {
                        "id": order_id,
                        "state": order_data.get("order_status", "Accepted") if order_data else "Accepted",
                        "provider": {"id": provider_id},
                        "items": items,
                        "fulfillments": [{
                            "id": "F1",
                            "type": "Delivery",
                            "state": {
                                "descriptor": {
                                    "code": order_data.get("fulfillment_status", "Pending") if order_data else "Pending"
                                }
                            },
                        }],
                        "quote": order_message.get("quote", {}),
                        "payment": order_message.get("payment", {}),
                    }
                },
            }

            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_update: {str(e)}",
                "ONDC On Update",
            )
            raise

    def construct_on_cancel(self, cancel_request):
        """Construct on_cancel callback body"""
        try:
            context = cancel_request.get("context", {})
            order_message = cancel_request.get("message", {}).get("order", {})
            order_id = order_message.get("id", "")

            # Get order from DB
            order_data = None
            if order_id:
                try:
                    order_data = frappe.get_doc("ONDC Order", order_id)
                except frappe.DoesNotExistError:
                    pass

            response_body = {
                "context": self.create_context("on_cancel", cancel_request.get("context", {})),
                "message": {"order": {"id": order_id, "state": "Cancelled"}},
            }

            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_cancel: {str(e)}",
                "ONDC On Cancel",
            )
            raise

    def _get_catalog(self):
        """Construct catalog from ONDC Products"""
        try:
            products = frappe.get_all(
                "ONDC Product",
                filters={"is_active": 1},
                fields=["name", "ondc_product_id", "product_name", "short_desc", "long_desc", "price", "category_code", "fulfillment_id", "available_quantity", "maximum_quantity"]
            )

            items = []
            for product in products:
                items.append({
                    "id": product.ondc_product_id or product.name,
                    "descriptor": {
                        "name": product.product_name,
                        "short_desc": product.short_desc or "",
                        "long_desc": product.long_desc or "",
                        "images": [self.settings.store_logo] if self.settings.store_logo else [],
                    },
                    "price": {
                        "currency": "INR",
                        "value": str(product.price or 0),
                        "maximum_value": str(product.price or 0),
                    },
                    "category_id": product.category_code or "Grocery",
                    "fulfillment_id": product.fulfillment_id or "F1",
                    "quantity": {
                        "available": {
                            "count": str(product.available_quantity or 0),
                        },
                        "maximum": {
                            "count": str(product.maximum_quantity or 10),
                        },
                    },
                })

            # Build provider using store info from ONDC Settings
            provider = {
                "id": self.settings.subscriber_id,
                "descriptor": {
                    "name": self.settings.store_name or self.settings.subscriber_id,
                    "short_desc": self.settings.store_short_desc or "",
                    "long_desc": self.settings.store_long_desc or "",
                    "images": [self.settings.store_logo] if self.settings.store_logo else [],
                },
                "locations": [{
                    "id": "L1",
                    "gps": self.settings.store_gps or "",
                    "address": {
                        "locality": self.settings.store_locality or "",
                        "city": self.settings.store_city_name or "",
                        "state": self.settings.store_state or "",
                        "area_code": self.settings.store_area_code or "",
                    },
                }],
                "fulfillments": [{
                    "id": "F1",
                    "type": "Delivery",
                    "contact": {
                        "phone": self.settings.consumer_care_phone or "",
                        "email": self.settings.consumer_care_email or "",
                    },
                }],
                "items": items,
            }

            # ONDC on_search catalog MUST include bpp/descriptor and bpp/fulfillments
            # at the top level (in addition to bpp/providers) per ONDC 1.2.0 spec.
            # Missing these causes DOMAIN-ERROR code 10001 from Pramaan gateway.
            catalog = {
                "bpp/descriptor": {
                    "name": self.settings.store_name or self.settings.subscriber_id,
                    "short_desc": self.settings.store_short_desc or "",
                    "long_desc": self.settings.store_long_desc or "",
                    "images": [self.settings.store_logo] if self.settings.store_logo else [],
                },
                "bpp/fulfillments": [{
                    "id": "F1",
                    "type": "Delivery",
                    "contact": {
                        "phone": self.settings.consumer_care_phone or "",
                        "email": self.settings.consumer_care_email or "",
                    },
                }],
                "bpp/providers": [provider],
            }
            return catalog
        except Exception as e:
            frappe.log_error(f"Error fetching catalog: {str(e)}", "ONDC Catalog")
            raise
