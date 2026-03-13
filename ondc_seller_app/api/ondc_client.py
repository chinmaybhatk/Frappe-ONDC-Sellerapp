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

        signing_key = nacl.signing.SigningKey(raw_key)
        message = signing_string.encode()
        signed = signing_key.sign(message)
        signature_bytes = signed.signature
        signature_base64 = base64.b64encode(signature_bytes).decode()

        # Get the public key
        verify_key = signing_key.verify_key
        public_key_base64 = base64.b64encode(verify_key.encode()).decode()

        auth_header = (
            f'Signature keyId="{self.settings.subscriber_id}|{self.settings.public_key_id}|ed25519",'
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

        verify_key = signing_key.verify_key
        public_key_base64 = base64.b64encode(verify_key.encode()).decode()

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
                "context": {
                    "domain": search_params["context"]["domain"],
                    "country": search_params["context"]["country"],
                    "city": search_params["context"]["city"],
                    "action": "on_search",
                    "core_version": "1.2.0",
                    "bap_id": search_params["context"]["bap_id"],
                    "bap_uri": search_params["context"]["bap_uri"],
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "message_id": search_params["context"].get("message_id", search_params.get("message_id", "")),
                    "transaction_id": search_params["context"]["transaction_id"],
                    "bpp_id": self.settings.subscriber_id,
                    "bpp_uri": self.settings.subscriber_url,
                },
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
        """Construct on_select callback body"""
        try:
            selected_items = select_request["message"]["order"]["items"]
            order_value = 0
            quote_items = []

            for item in selected_items:
                # Calculate price
                item_price = float(item["price"]["value"])
                item_quantity = int(item["quantity"]["count"])
                item_total = item_price * item_quantity
                order_value += item_total

                quote_items.append(
                    {
                        "id": item["id"],
                        "quantity": item["quantity"],
                        "price": item["price"],
                    }
                )

            quote = {
                "price": {
                    "currency": "INR",
                    "value": str(order_value),
                },
                "breakup": [
                    {
                        "@type": "delivery",
                        "price": {"currency": "INR", "value": "0"},
                    },
                    {
                        "@type": "discount",
                        "price": {"currency": "INR", "value": "0"},
                    },
                ],
                "ttl": "PT30M",
            }

            response_body = {
                "context": {
                    "domain": select_request["context"]["domain"],
                    "country": select_request["context"]["country"],
                    "city": select_request["context"]["city"],
                    "action": "on_select",
                    "core_version": "1.2.0",
                    "bap_id": select_request["context"]["bap_id"],
                    "bap_uri": select_request["context"]["bap_uri"],
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "message_id": select_request["context"].get("message_id", select_request.get("message_id", "")),
                    "transaction_id": select_request["context"]["transaction_id"],
                    "bpp_id": self.settings.subscriber_id,
                    "bpp_uri": self.settings.subscriber_url,
                },
                "message": {
                    "order": {
                        "items": quote_items,
                        "quote": quote,
                        "fulfillment": select_request["message"]["order"][
                            "fulfillment"
                        ],
                    }
                },
            }

            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_select: {str(e)}",
                "ONDC On Select",
            )
            raise

    def construct_on_init(self, init_request):
        """Construct on_init callback body"""
        try:
            response_body = {
                "context": {
                    "domain": init_request["context"]["domain"],
                    "country": init_request["context"]["country"],
                    "city": init_request["context"]["city"],
                    "action": "on_init",
                    "core_version": "1.2.0",
                    "bap_id": init_request["context"]["bap_id"],
                    "bap_uri": init_request["context"]["bap_uri"],
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "message_id": init_request["context"].get("message_id", init_request.get("message_id", "")),
                    "transaction_id": init_request["context"]["transaction_id"],
                    "bpp_id": self.settings.subscriber_id,
                    "bpp_uri": self.settings.subscriber_url,
                },
                "message": {
                    "order": {
                        "id": str(datetime.utcnow().timestamp()),
                        "state": "Created",
                        "items": init_request["message"]["order"]["items"],
                        "quote": init_request["message"]["order"]["quote"],
                        "fulfillment": init_request["message"]["order"][
                            "fulfillment"
                        ],
                        "billing": init_request["message"]["order"].get(
                            "billing"
                        ),
                        "payment": {
                            "uri": "https://example.com/pay",
                            "tl_method": "http/get",
                            "params": {"transaction_id": ""},
                            "status": "NOT-PAID",
                        },
                    }
                },
            }

            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_init: {str(e)}",
                "ONDC On Init",
            )
            raise

    def construct_on_confirm(self, confirm_request):
        """Construct on_confirm callback body"""
        try:
            order_id = str(datetime.utcnow().timestamp())

            response_body = {
                "context": {
                    "domain": confirm_request["context"]["domain"],
                    "country": confirm_request["context"]["country"],
                    "city": confirm_request["context"]["city"],
                    "action": "on_confirm",
                    "core_version": "1.2.0",
                    "bap_id": confirm_request["context"]["bap_id"],
                    "bap_uri": confirm_request["context"]["bap_uri"],
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "message_id": confirm_request["context"].get("message_id", confirm_request.get("message_id", "")),
                    "transaction_id": confirm_request["context"]["transaction_id"],
                    "bpp_id": self.settings.subscriber_id,
                    "bpp_uri": self.settings.subscriber_url,
                },
                "message": {
                    "order": {
                        "id": order_id,
                        "state": "Confirmed",
                        "items": confirm_request["message"]["order"]["items"],
                        "quote": confirm_request["message"]["order"]["quote"],
                        "fulfillment": {
                            "id": "fulfillment_001",
                            "type": confirm_request["message"]["order"][
                                "fulfillment"
                            ]["type"],
                            "state": {"descriptor": {"code": "Pending"}},
                            "tracking": False,
                            "contact": confirm_request["message"]["order"][
                                "fulfillment"
                            ]["end"]["contact"],
                        },
                        "billing": confirm_request["message"]["order"].get(
                            "billing"
                        ),
                        "payment": {
                            "uri": "https://example.com/pay",
                            "tl_method": "http/get",
                            "params": {"transaction_id": order_id},
                            "status": "PAID",
                        },
                    }
                },
            }

            # Save the order
            order_doc = frappe.new_doc("ONDC Order")
            order_doc.order_id = order_id
            order_doc.status = "Confirmed"
            order_doc.request_body = json.dumps(confirm_request)
            order_doc.response_body = json.dumps(response_body)
            order_doc.save()

            return response_body
        except Exception as e:
            frappe.log_error(
                f"Error constructing on_confirm: {str(e)}",
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
                "context": {
                    "domain": status_request["context"]["domain"],
                    "country": status_request["context"]["country"],
                    "city": status_request["context"]["city"],
                    "action": "on_status",
                    "core_version": "1.2.0",
                    "bap_id": status_request["context"]["bap_id"],
                    "bap_uri": status_request["context"]["bap_uri"],
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "message_id": status_request["context"].get("message_id", status_request.get("message_id", "")),
                    "transaction_id": status_request["context"]["transaction_id"],
                    "bpp_id": self.settings.subscriber_id,
                    "bpp_uri": self.settings.subscriber_url,
                },
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
                "context": {
                    "domain": update_request["context"]["domain"],
                    "country": update_request["context"]["country"],
                    "city": update_request["context"]["city"],
                    "action": "on_update",
                    "core_version": "1.2.0",
                    "bap_id": update_request["context"]["bap_id"],
                    "bap_uri": update_request["context"]["bap_uri"],
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "message_id": update_request["context"].get("message_id", update_request.get("message_id", "")),
                    "transaction_id": update_request["context"]["transaction_id"],
                    "bpp_id": self.settings.subscriber_id,
                    "bpp_uri": self.settings.subscriber_url,
                },
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
                "context": {
                    "domain": cancel_request["context"]["domain"],
                    "country": cancel_request["context"]["country"],
                    "city": cancel_request["context"]["city"],
                    "action": "on_cancel",
                    "core_version": "1.2.0",
                    "bap_id": cancel_request["context"]["bap_id"],
                    "bap_uri": cancel_request["context"]["bap_uri"],
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "message_id": cancel_request["context"].get("message_id", cancel_request.get("message_id", "")),
                    "transaction_id": cancel_request["context"]["transaction_id"],
                    "bpp_id": self.settings.subscriber_id,
                    "bpp_uri": self.settings.subscriber_url,
                },
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
