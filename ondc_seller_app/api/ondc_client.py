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
        """
        try:
            callback_url = f"{bap_uri.rstrip('/')}{endpoint}"

            # Build headers with auth and Expect: "" to prevent 417 errors
            headers = self._get_common_headers()
            headers["Expect"] = ""  # Prevent 417 Expectation Failed from nginx
            headers["Authorization"] = self.get_auth_header(payload)

            frappe.log_error(
                f"Sending callback to {callback_url}",
                "ONDC Callback Debug"
            )

            response = requests.post(
                callback_url,
                json=payload,
                headers=headers,
                timeout=30,
            )

            frappe.log_error(
                f"Callback response: {response.status_code} - {response.text[:500]}",
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
                f"Error sending callback to {bap_uri}{endpoint}: {str(e)}",
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
    # Registry Subscription
    # -----------------------------------------------------------------------
    def get_registry_list(self):
        """Get list of active subscribers from registry"""
        try:
            environment = self.settings.environment
            registry_url = f"{self.base_urls[environment]['registry']}/subscribers"
            headers = self._get_common_headers()

            response = requests.get(registry_url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            frappe.log_error(f"Error fetching registry list: {str(e)}", "ONDC Registry")
            raise

    def register_subscriber(self):
        """Register subscriber on ONDC registry"""
        try:
            body = {
                "subscriber_id": self.settings.subscriber_id,
                "subscriber_url": self.settings.subscriber_url,
                "type": "BPP",
                "signing_public_key": self.settings.public_signing_key,
                "encryption_public_key": self.settings.public_encryption_key,
                "country": "IND",
                "domain": "ONDC:FIS12",
                "city": "*",
            }

            environment = self.settings.environment
            registry_url = f"{self.base_urls[environment]['registry']}/subscribe"
            headers = self._get_common_headers()

            # Add the expected header to prevent 417 errors
            headers["Expect"] = ""
            headers.update({"Content-Type": "application/json"})

            response = requests.post(
                registry_url, json=body, headers=headers, timeout=30
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            frappe.log_error(
                f"Error registering subscriber: {str(e)}", "ONDC Subscriber Registration"
            )
            raise

    def lookup(self, search_request):
        """Send lookup request to ONDC gateway"""
        try:
            environment = self.settings.environment
            gateway_url = f"{self.base_urls[environment]['gateway']}/lookup"

            headers = self._get_common_headers()
            headers["Expect"] = ""
            headers["Authorization"] = self.get_auth_header(json.dumps(search_request))

            response = requests.post(
                gateway_url, json=search_request, headers=headers, timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"Lookup error: {str(e)}", "ONDC Lookup")
            raise

    def send(self, message_id, transaction_id, endpoint, action, request_body):
        """Send a request to ONDC gateway"""
        try:
            environment = self.settings.environment
            gateway_url = f"{self.base_urls[environment]['gateway']}/{action}"

            headers = self._get_common_headers()
            headers["Expect"] = ""
            headers["Message-Id"] = message_id
            headers["Authorization"] = self.get_auth_header(json.dumps(request_body))
            if transaction_id:
                headers["X-Gateway-Authorization"] = self._get_gateway_auth_header(
                    message_id, transaction_id, json.dumps(request_body)
                )

            response = requests.post(
                gateway_url, json=request_body, headers=headers, timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
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
    # Encryption/Decryption
    # -----------------------------------------------------------------------
    def encrypt_request(self, receiver_public_key_base64, request_body):
        """Encrypt request body using receiver's public key (X25519)"""
        try:
            # Decode the base64 receiver public key
            receiver_public_key_bytes = base64.b64decode(
                receiver_public_key_base64
            )

            # Create a public key object from the bytes
            receiver_public_key = nacl.public.PublicKey(
                receiver_public_key_bytes
            )

            # Create a random private key and its corresponding public key
            private_key = nacl.public.PrivateKey.generate()
            public_key = private_key.public_key

            # Create a box using the random private key and receiver's public key
            box = nacl.public.Box(private_key, receiver_public_key)

            # Serialize request_body to JSON if it's a dict
            if isinstance(request_body, dict):
                json_string = json.dumps(request_body)
            else:
                json_string = request_body

            # Encrypt the JSON string
            encrypted = box.encrypt(json_string.encode())

            # Return encrypted data and the ephemeral public key
            return {
                "encrypted_data": base64.b64encode(encrypted.ciphertext).decode(),
                "ephemeral_public_key": base64.b64encode(
                    public_key.encode()
                ).decode(),
            }
        except Exception as e:
            frappe.log_error(f"Encryption error: {str(e)}", "ONDC Encryption")
            raise

    def decrypt_response(self, encrypted_data_base64, ephemeral_public_key_base64):
        """Decrypt response using private key (X25519)"""
        try:
            # Get the private key from settings
            encryption_private_key = self.settings.get_password(
                "encryption_private_key"
            )
            if not encryption_private_key:
                frappe.throw("Encryption private key is not set.")

            # Decode the base64 private key
            private_key_bytes = base64.b64decode(encryption_private_key)

            # Create a private key object from the bytes
            private_key = nacl.public.PrivateKey(private_key_bytes)

            # Decode the ephemeral public key
            ephemeral_public_key_bytes = base64.b64decode(
                ephemeral_public_key_base64
            )
            ephemeral_public_key = nacl.public.PublicKey(
                ephemeral_public_key_bytes
            )

            # Create a box using the private key and ephemeral public key
            box = nacl.public.Box(private_key, ephemeral_public_key)

            # Decode the encrypted data from base64
            encrypted_data = base64.b64decode(encrypted_data_base64)

            # Decrypt the data
            decrypted = box.decrypt(encrypted_data)

            # Parse the decrypted JSON
            return json.loads(decrypted.decode())
        except Exception as e:
            frappe.log_error(f"Decryption error: {str(e)}", "ONDC Decryption")
            raise

    # -----------------------------------------------------------------------
    # Construct Callback Payloads
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
            order_data = init_request["message"]["order"]
            provider_id = self.settings.subscriber_id
            tax_rate = float(self.settings.get("default_tax_rate") or 0)

            # Rebuild quote with proper breakup from our catalog
            items = order_data.get("items", [])
            quote_breakup = []
            total_value = 0.0
            total_tax = 0.0

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

            response_items = []
            for item in items:
                item_id = item.get("id")
                item_qty = int(item.get("quantity", {}).get("count", 1))
                if item.get("price") and item["price"].get("value"):
                    item_price = float(item["price"]["value"])
                elif item_id in product_price_map:
                    item_price = product_price_map[item_id]["price"]
                else:
                    item_price = 0

                line_total = item_price * item_qty
                total_value += line_total
                item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
                total_tax += item_tax

                response_items.append({
                    "id": item_id,
                    "quantity": {"count": item_qty},
                    "fulfillment_id": "F1",
                })
                quote_breakup.append({
                    "title": product_price_map.get(item_id, {}).get("name", item_id),
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/item_quantity": {"count": item_qty},
                    "@ondc/org/title_type": "item",
                    "price": {"currency": "INR", "value": str(line_total)},
                    "item": {"price": {"currency": "INR", "value": str(item_price)}},
                })
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

            grand_total = total_value + total_tax + delivery_charge

            # Fulfillment from request
            fulfillments = order_data.get("fulfillments",
                [order_data["fulfillment"]] if "fulfillment" in order_data else [])
            if isinstance(fulfillments, dict):
                fulfillments = [fulfillments]

            response_body = {
                "context": self.create_context("on_init", init_request.get("context", {})),
                "message": {
                    "order": {
                        "provider": {"id": provider_id},
                        "items": response_items,
                        "billing": order_data.get("billing"),
                        "fulfillments": [{
                            "id": "F1",
                            "type": "Delivery",
                            "tracking": False,
                            "end": fulfillments[0].get("end", {}) if fulfillments else {},
                        }],
                        "quote": {
                            "price": {"currency": "INR", "value": str(grand_total)},
                            "breakup": quote_breakup,
                            "ttl": "P1D",
                        },
                        "payment": {
                            "type": order_data.get("payment", {}).get("type", "ON-ORDER"),
                            "status": "NOT-PAID",
                            "@ondc/org/buyer_app_finder_fee_type": "percent",
                            "@ondc/org/buyer_app_finder_fee_amount": "3",
                            "@ondc/org/settlement_details": [{
                                "settlement_counterparty": "seller-app",
                                "settlement_type": "neft",
                            }],
                        },
                        "tags": [{
                            "code": "bpp_terms",
                            "list": [
                                {"code": "tax_number", "value": self.settings.get("gstin") or ""},
                                {"code": "provider_tax_number", "value": self.settings.get("gstin") or ""},
                            ],
                        }],
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

        FIX BUG #2: Do NOT create a new ONDC Order here — process_confirm in
        webhook.py already creates the ONDC Order before calling client.on_confirm().
        This method should just build the response payload using the existing order.
        """
        try:
            order_data = confirm_request["message"]["order"]
            context = confirm_request.get("context", {})

            # Find the order that was already created by process_confirm
            order_id = order_data.get("id") or ""
            existing_order = None
            if order_id:
                orders = frappe.get_all(
                    "ONDC Order",
                    filters={"ondc_order_id": order_id},
                    fields=["name", "ondc_order_id"],
                    limit=1,
                )
                if orders:
                    existing_order = frappe.get_doc("ONDC Order", orders[0].name)

            # If no order found by order_id, try by transaction_id
            if not existing_order:
                transaction_id = context.get("transaction_id", "")
                if transaction_id:
                    orders = frappe.get_all(
                        "ONDC Order",
                        filters={"transaction_id": transaction_id},
                        fields=["name", "ondc_order_id"],
                        order_by="creation desc",
                        limit=1,
                    )
                    if orders:
                        existing_order = frappe.get_doc("ONDC Order", orders[0].name)
                        order_id = existing_order.ondc_order_id

            if not order_id:
                order_id = frappe.generate_hash(length=16)

            provider_id = self.settings.subscriber_id
            tax_rate = float(self.settings.get("default_tax_rate") or 0)
            now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")

            # Build items and quote from the order data
            items = order_data.get("items", [])
            quote_breakup = []
            total_value = 0.0
            total_tax = 0.0
            response_items = []

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

                line_total = item_price * item_qty
                total_value += line_total
                item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
                total_tax += item_tax

                response_items.append({
                    "id": item_id,
                    "quantity": {"count": item_qty},
                    "fulfillment_id": order_data.get("fulfillments", [{}])[0].get("id", "F1") if order_data.get("fulfillments") else "F1",
                })
                quote_breakup.append({
                    "title": product_price_map.get(item_id, {}).get("name", item_id),
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/item_quantity": {"count": item_qty},
                    "@ondc/org/title_type": "item",
                    "price": {"currency": "INR", "value": str(line_total)},
                    "item": {"price": {"currency": "INR", "value": str(item_price)}},
                })
                quote_breakup.append({
                    "title": "Tax",
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/title_type": "tax",
                    "price": {"currency": "INR", "value": str(item_tax)},
                })

            delivery_charge = float(self.settings.get("default_delivery_charge") or 0)
            quote_breakup.append({
                "title": "Delivery charges",
                "@ondc/org/item_id": order_data.get("fulfillments", [{}])[0].get("id", "F1") if order_data.get("fulfillments") else "F1",
                "@ondc/org/title_type": "delivery",
                "price": {"currency": "INR", "value": str(delivery_charge)},
            })

            grand_total = total_value + total_tax + delivery_charge

            # Fulfillment
            fulfillments = order_data.get("fulfillments", [])
            if isinstance(fulfillments, dict):
                fulfillments = [fulfillments]
            if not fulfillments and "fulfillment" in order_data:
                fulfillments = [order_data["fulfillment"]]

            fulfillment_id = fulfillments[0].get("id", "F1") if fulfillments else "F1"
            fulfillment_end = fulfillments[0].get("end", {}) if fulfillments else {}

            store_gps = self.settings.get("store_gps") or "0.0,0.0"

            response_body = {
                "context": self.create_context("on_confirm", context),
                "message": {
                    "order": {
                        "id": order_id,
                        "state": "Accepted",
                        "provider": {
                            "id": provider_id,
                            "locations": [{"id": "L1"}],
                        },
                        "items": response_items,
                        "billing": order_data.get("billing", {}),
                        "fulfillments": [{
                            "id": fulfillment_id,
                            "type": "Delivery",
                            "@ondc/org/provider_name": self.settings.store_name or provider_id,
                            "tracking": False,
                            "state": {"descriptor": {"code": "Pending"}},
                            "start": {
                                "location": {
                                    "id": "L1",
                                    "descriptor": {"name": self.settings.store_name or "Store"},
                                    "gps": store_gps,
                                },
                                "time": {
                                    "range": {
                                        "start": now_str,
                                        "end": (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                    },
                                },
                                "contact": {
                                    "phone": self.settings.consumer_care_phone or "",
                                    "email": self.settings.consumer_care_email or "",
                                },
                            },
                            "end": fulfillment_end,
                        }],
                        "quote": {
                            "price": {"currency": "INR", "value": str(grand_total)},
                            "breakup": quote_breakup,
                            "ttl": "P1D",
                        },
                        "payment": order_data.get("payment", {
                            "type": "ON-ORDER",
                            "status": "PAID",
                        }),
                        "created_at": now_str,
                        "updated_at": now_str,
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
            # Fetch the order
            order_id = status_request["message"].get("order_id")
            order_doc = frappe.get_doc("ONDC Order", order_id)

            response_body = {
                "context": self.create_context("on_status", status_request.get("context", {})),
                "message": {
                    "order": {
                        "id": order_id,
                        "state": order_doc.status,
                        "fulfillment": {
                            "id": "fulfillment_001",
                            "state": {"descriptor": {"code": "Pending"}},
                        },
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
            order_id = update_request["message"].get("order_id")
            order_doc = frappe.get_doc("ONDC Order", order_id)
            order_doc.status = update_request["message"]["order"].get(
                "state", "Confirmed"
            )
            order_doc.save()

            response_body = {
                "context": self.create_context("on_update", update_request.get("context", {})),
                "message": {"order": {"id": order_id, "state": order_doc.status}},
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
            order_id = cancel_request["message"].get("order_id")
            order_doc = frappe.get_doc("ONDC Order", order_id)
            order_doc.status = "Cancelled"
            order_doc.save()

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

            return {"bpp/providers": [provider]}
        except Exception as e:
            frappe.log_error(f"Error fetching catalog: {str(e)}", "ONDC Catalog")
            raise
