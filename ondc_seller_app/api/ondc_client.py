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
        signed = signing_key.sign(signing_string.encode())
        signature = base64.b64encode(signed.signature).decode()

        subscriber_id = self.settings.subscriber_id
        unique_key_id = self.settings.unique_key_id or "default"

        auth_header = (
            f'Signature keyId="{subscriber_id}|{unique_key_id}|ed25519",'
            f'algorithm="ed25519",'
            f'created="{created}",'
            f'expires="{expires}",'
            f'headers="(created) (expires) digest",'
            f'signature="{signature}"'
        )
        return auth_header

    def _calculate_digest(self, request_body):
        """Calculate BLAKE-512 digest of request body"""
        # Serialize with compact separators — same as send_callback
        body_str = json.dumps(request_body, separators=(",", ":"), ensure_ascii=False)
        body_bytes = body_str.encode("utf-8")
        digest = hashlib.blake2b(body_bytes, digest_size=64).digest()
        return base64.b64encode(digest).decode()

    def verify_signature(self, auth_header, body):
        """Verify ONDC signature from incoming request"""
        # Parse auth header
        # …existing stub…
        return True

    # -----------------------------------------------------------------------
    # Key management helpers
    # -----------------------------------------------------------------------
    @staticmethod
    def generate_signing_keys():
        """Generate Ed25519 signing key pair"""
        signing_key = nacl.signing.SigningKey.generate()
        private_key = base64.b64encode(signing_key.encode()).decode()
        public_key = base64.b64encode(
            signing_key.verify_key.encode()
        ).decode()
        return private_key, public_key

    @staticmethod
    def generate_encryption_keys():
        """Generate X25519 encryption key pair"""
        private_key = nacl.public.PrivateKey.generate()
        public_key = private_key.public_key
        return (
            base64.b64encode(private_key.encode()).decode(),
            base64.b64encode(public_key.encode()).decode(),
        )

    # -----------------------------------------------------------------------
    # Callback
    # -----------------------------------------------------------------------
    def send_callback(self, callback_url, endpoint, payload):
        """Send callback to BAP"""
        if not callback_url:
            return {"success": False, "error": "No callback URL provided"}

        # Ensure callback_url does not double-slash
        url = callback_url.rstrip("/") + endpoint

        # CRITICAL: Serialize body ONCE with compact separators so the bytes
        # sent over the wire are identical to those used for digest calculation
        # in get_auth_header / _calculate_digest.  Using requests.post(json=)
        # would re-serialize with default separators (spaces), causing a digest
        # mismatch and "Invalid Signature" at the receiver.
        body_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        auth_header = self.get_auth_header(payload)
        headers = {
            "Content-Type": "application/json",
            "Authorization": auth_header,
        }

        # Debug: log the exact digest and auth header for troubleshooting
        body_digest = hashlib.blake2b(body_bytes, digest_size=64).digest()
        body_digest_b64 = base64.b64encode(body_digest).decode()
        frappe.log_error(
            f"Callback to {url}\n"
            f"Body length: {len(body_bytes)} bytes\n"
            f"Body BLAKE-512 digest: {body_digest_b64}\n"
            f"Auth header: {auth_header}\n"
            f"Body (first 300 chars): {body_bytes[:300].decode('utf-8', errors='replace')}",
            "ONDC Callback Debug",
        )

        try:
            response = requests.post(url=url, headers=headers, data=body_bytes, timeout=30)
            # Log the response body for debugging (even on success)
            resp_text = response.text[:1000] if response.text else "(empty)"
            frappe.log_error(
                f"Callback Response from {url}\n"
                f"Status: {response.status_code}\n"
                f"Response body: {resp_text}",
                "ONDC Callback Response",
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.exceptions.RequestException as e:
            # Capture response body for debugging 400/500 errors
            error_detail = str(e)
            response_body = ""
            if hasattr(e, "response") and e.response is not None:
                try:
                    response_body = e.response.text[:500]
                    error_detail = f"{str(e)} | Response: {response_body}"
                except Exception:
                    pass
            frappe.log_error(
                f"ONDC Callback Error to {url}:\n{error_detail}\n\n"
                f"Auth header sent: {auth_header}\n\n"
                f"Payload context: {json.dumps(payload.get('context', {}), indent=2)}",
                "ONDC Callback"
            )
            return {"success": False, "error": str(e), "response_body": response_body}

    # -----------------------------------------------------------------------
    # Context
    # -----------------------------------------------------------------------
    def create_context(self, action, request_context=None):
        """Create ONDC-compliant context for response messages.

        Follows Beckn/ONDC context specification:
        - domain, country, city, core_version come from incoming context (or defaults)
        - bpp_id and bpp_uri are always *this* seller's subscriber_id / base URL
        - bap_id and bap_uri are copied from the *incoming* context so the
          response is routed back to the correct buyer platform
        - message_id is preserved from the incoming request
        - transaction_id is preserved to maintain the order session
        - timestamp is always fresh (UTC ISO-8601)
        """
        env = (self.settings.environment or "preprod").lower()
        base = self.base_urls.get(env, self.base_urls["preprod"])

        ctx = {
            "domain": (request_context or {}).get("domain", self.settings.domain or "ONDC:RET10"),
            "country": (request_context or {}).get("country", "IND"),
            "city": (request_context or {}).get("city", self.settings.city or "std:080"),
            "action": action,
            "core_version": (request_context or {}).get("core_version", "1.2.0"),
            "bpp_id": self.settings.subscriber_id,
            "bpp_uri": f"https://{self.settings.subscriber_id}",
            "bap_id": (request_context or {}).get("bap_id", ""),
            "bap_uri": (request_context or {}).get("bap_uri", ""),
            "transaction_id": (request_context or {}).get("transaction_id", ""),
            "message_id": (request_context or {}).get("message_id", ""),
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        return ctx

    # -----------------------------------------------------------------------
    # Lookup
    # -----------------------------------------------------------------------
    def lookup_bap(self, bap_id):
        """Lookup BAP details from ONDC registry"""
        env = (self.settings.environment or "preprod").lower()
        base = self.base_urls.get(env, self.base_urls["preprod"])

        try:
            response = requests.post(
                f"{base['registry']}/lookup",
                json={"subscriber_id": bap_id, "type": "BAP"},
                timeout=10,
            )
            if response.ok:
                return response.json()
        except Exception as e:
            frappe.log_error(f"BAP Lookup Error: {str(e)}", "ONDC BAP Lookup")
        return None

    # -----------------------------------------------------------------------
    # Subscribe
    # -----------------------------------------------------------------------
    def subscribe(self):
        """Subscribe to ONDC network (called during setup)"""
        env = (self.settings.environment or "preprod").lower()
        base = self.base_urls.get(env, self.base_urls["preprod"])

        subscribe_payload = {
            "context": {
                "operation": {"ops_no": 1},
            },
            "message": {
                "request_id": frappe.generate_hash(length=32),
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "entity": {
                    "subscriber_id": self.settings.subscriber_id,
                    "unique_key_id": self.settings.unique_key_id or "default",
                    "callback_url": f"https://{self.settings.subscriber_id}",
                    "key_pair": {
                        "signing_public_key": self.settings.signing_public_key,
                        "encryption_public_key": self.settings.encryption_public_key,
                        "valid_from": datetime.utcnow().strftime(
                            "%Y-%m-%dT%H:%M:%S.000Z"
                        ),
                        "valid_until": (
                            datetime.utcnow() + timedelta(days=365)
                        ).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    },
                    "city": [self.settings.city or "*"],
                    "domain": [self.settings.domain or "ONDC:RET10"],
                    "type": "BPP",
                },
                "network_participant": [
                    {
                        "subscriber_url": f"https://{self.settings.subscriber_id}",
                        "domain": self.settings.domain or "ONDC:RET10",
                        "type": "BPP",
                        "msn": False,
                    }
                ],
            },
        }

        try:
            response = requests.post(
                f"{base['registry']}/subscribe",
                json=subscribe_payload,
                timeout=30,
            )
            return response.json()
        except Exception as e:
            frappe.log_error(f"ONDC Subscribe Error: {str(e)}", "ONDC Subscribe")
            return {"error": str(e)}

    # -----------------------------------------------------------------------
    # Catalog (on_search)
    # -----------------------------------------------------------------------
    def on_search(self, search_request):
        """Send catalog response"""
        context = self.create_context("on_search", search_request.get("context"))
        catalog = self.build_catalog(search_request)
        payload = {
            "context": context,
            "message": {
                "catalog": catalog,
            },
        }
        return self.send_callback(
            search_request.get("context", {}).get("bap_uri"), "/on_search", payload
        )

    # -----------------------------------------------------------------------
    # Select (on_select)
    # -----------------------------------------------------------------------
    def on_select(self, select_request):
        """Send quote response for selected items"""
        context = self.create_context("on_select", select_request.get("context"))
        order = select_request.get("message", {}).get("order", {})

        items = order.get("items", [])
        quoted_items = []
        total = 0
        breakup = []

        for item in items:
            product = frappe.get_doc("ONDC Product", {"ondc_product_id": item.get("id")})
            if not product:
                continue

            qty = int(item.get("quantity", {}).get("count", 1))
            price = float(product.price or 0)
            item_total = price * qty
            total += item_total

            quoted_items.append(
                {
                    "id": product.ondc_product_id,
                    "fulfillment_id": item.get("fulfillment_id", "F1"),
                    "quantity": {"count": qty},
                }
            )

            breakup.append(
                {
                    "@ondc/org/item_id": product.ondc_product_id,
                    "@ondc/org/item_quantity": {"count": qty},
                    "@ondc/org/title_type": "item",
                    "title": product.product_name,
                    "price": {"currency": "INR", "value": str(item_total)},
                    "item": {"id": product.ondc_product_id},
                }
            )

        # Add delivery charges
        delivery_charge = float(self.settings.get("delivery_charge") or 0)
        if delivery_charge > 0:
            total += delivery_charge
            breakup.append(
                {
                    "@ondc/org/item_id": "F1",
                    "@ondc/org/title_type": "delivery",
                    "title": "Delivery charges",
                    "price": {"currency": "INR", "value": str(delivery_charge)},
                }
            )

        # Add packing charges
        packing_charge = float(self.settings.get("packing_charge") or 0)
        if packing_charge > 0:
            total += packing_charge
            breakup.append(
                {
                    "@ondc/org/item_id": "F1",
                    "@ondc/org/title_type": "packing",
                    "title": "Packing charges",
                    "price": {"currency": "INR", "value": str(packing_charge)},
                }
            )

        fulfillments = order.get("fulfillments", [])
        if not fulfillments:
            fulfillments = [{"id": "F1", "type": "Delivery"}]

        quote_fulfillments = []
        for f in fulfillments:
            qf = {
                "id": f.get("id", "F1"),
                "type": f.get("type", "Delivery"),
                "@ondc/org/provider_name": self.settings.store_name or self.settings.subscriber_id,
                "@ondc/org/category": "Standard Delivery",
                "tracking": False,
                "state": {"descriptor": {"code": "Serviceable"}},
            }

            # Include TAT (Turnaround Time) for delivery
            delivery_tat = self.settings.get("delivery_tat") or "PT60M"
            qf["@ondc/org/TAT"] = delivery_tat

            quote_fulfillments.append(qf)

        payload = {
            "context": context,
            "message": {
                "order": {
                    "provider": {
                        "id": self.settings.subscriber_id,
                    },
                    "items": quoted_items,
                    "fulfillments": quote_fulfillments,
                    "quote": {
                        "price": {"currency": "INR", "value": str(total)},
                        "breakup": breakup,
                        "ttl": "P1D",
                    },
                }
            },
        }
        return self.send_callback(
            select_request.get("context", {}).get("bap_uri"), "/on_select", payload
        )

    # -----------------------------------------------------------------------
    # Init (on_init)
    # -----------------------------------------------------------------------
    def on_init(self, init_request):
        """Send initialization response with payment details"""
        context = self.create_context("on_init", init_request.get("context"))
        order = init_request.get("message", {}).get("order", {})
        items = order.get("items", [])
        fulfillments = order.get("fulfillments", [])
        billing = order.get("billing", {})

        # Recalculate quote
        total = 0
        breakup = []
        quoted_items = []

        for item in items:
            try:
                product = frappe.get_doc("ONDC Product", {"ondc_product_id": item.get("id")})
            except Exception:
                continue

            qty = int(item.get("quantity", {}).get("count", 1))
            price = float(product.price or 0)
            item_total = price * qty
            total += item_total

            quoted_items.append(
                {
                    "id": product.ondc_product_id,
                    "fulfillment_id": item.get("fulfillment_id", "F1"),
                    "quantity": {"count": qty},
                }
            )

            breakup.append(
                {
                    "@ondc/org/item_id": product.ondc_product_id,
                    "@ondc/org/item_quantity": {"count": qty},
                    "@ondc/org/title_type": "item",
                    "title": product.product_name,
                    "price": {"currency": "INR", "value": str(item_total)},
                    "item": {"id": product.ondc_product_id},
                }
            )

        delivery_charge = float(self.settings.get("delivery_charge") or 0)
        if delivery_charge > 0:
            total += delivery_charge
            breakup.append(
                {
                    "@ondc/org/item_id": "F1",
                    "@ondc/org/title_type": "delivery",
                    "title": "Delivery charges",
                    "price": {"currency": "INR", "value": str(delivery_charge)},
                }
            )

        packing_charge = float(self.settings.get("packing_charge") or 0)
        if packing_charge > 0:
            total += packing_charge
            breakup.append(
                {
                    "@ondc/org/item_id": "F1",
                    "@ondc/org/title_type": "packing",
                    "title": "Packing charges",
                    "price": {"currency": "INR", "value": str(packing_charge)},
                }
            )

        init_fulfillments = []
        for f in fulfillments:
            ff = {
                "id": f.get("id", "F1"),
                "type": f.get("type", "Delivery"),
                "tracking": False,
                "state": {"descriptor": {"code": "Serviceable"}},
                "@ondc/org/provider_name": self.settings.store_name or self.settings.subscriber_id,
                "@ondc/org/category": "Standard Delivery",
                "@ondc/org/TAT": self.settings.get("delivery_tat") or "PT60M",
            }
            if f.get("end"):
                ff["end"] = f["end"]
            init_fulfillments.append(ff)

        # Payment
        payment = order.get("payment") or {}
        payment_response = {
            "type": payment.get("type", "ON-ORDER"),
            "collected_by": "BAP",
            "status": "NOT-PAID",
            "@ondc/org/buyer_app_finder_fee_type": payment.get(
                "@ondc/org/buyer_app_finder_fee_type", "percent"
            ),
            "@ondc/org/buyer_app_finder_fee_amount": payment.get(
                "@ondc/org/buyer_app_finder_fee_amount", "3"
            ),
            "@ondc/org/settlement_details": [
                {
                    "settlement_counterparty": "seller-app",
                    "settlement_phase": "sale-amount",
                    "settlement_type": "neft",
                    "settlement_bank_account_no": self.settings.get("bank_account_no") or "1234567890",
                    "settlement_ifsc_code": self.settings.get("ifsc_code") or "SBIN0000001",
                    "beneficiary_name": self.settings.get("beneficiary_name") or self.settings.store_name or "Seller",
                    "bank_name": self.settings.get("bank_name") or "State Bank of India",
                    "branch_name": self.settings.get("branch_name") or "Main Branch",
                }
            ],
        }

        payload = {
            "context": context,
            "message": {
                "order": {
                    "provider": {"id": self.settings.subscriber_id},
                    "items": quoted_items,
                    "billing": billing,
                    "fulfillments": init_fulfillments,
                    "quote": {
                        "price": {"currency": "INR", "value": str(total)},
                        "breakup": breakup,
                        "ttl": "P1D",
                    },
                    "payment": payment_response,
                    "tags": [
                        {
                            "code": "bpp_terms",
                            "list": [
                                {"code": "tax_number", "value": self.settings.get("gst_number") or "22AAAAA0000A1Z5"},
                                {"code": "provider_tax_number", "value": self.settings.get("gst_number") or "22AAAAA0000A1Z5"},
                            ],
                        }
                    ],
                }
            },
        }
        return self.send_callback(
            init_request.get("context", {}).get("bap_uri"), "/on_init", payload
        )

    # -----------------------------------------------------------------------
    # Confirm (on_confirm)
    # -----------------------------------------------------------------------
    def on_confirm(self, confirm_request):
        """Process order confirmation and send on_confirm response"""
        context = self.create_context("on_confirm", confirm_request.get("context"))
        order = confirm_request.get("message", {}).get("order", {})

        # Create ONDC Order in Frappe
        try:
            ondc_order = self._create_ondc_order(order, confirm_request.get("context", {}))
        except Exception as e:
            frappe.log_error(f"Error creating ONDC Order: {str(e)}", "ONDC Order Creation")
            ondc_order = None

        items = order.get("items", [])
        fulfillments = order.get("fulfillments", [])
        billing = order.get("billing", {})
        payment = order.get("payment", {})

        # Build confirmed items
        confirmed_items = []
        total = 0
        breakup = []

        for item in items:
            try:
                product = frappe.get_doc("ONDC Product", {"ondc_product_id": item.get("id")})
            except Exception:
                continue

            qty = int(item.get("quantity", {}).get("count", 1))
            price = float(product.price or 0)
            item_total = price * qty
            total += item_total

            confirmed_items.append(
                {
                    "id": product.ondc_product_id,
                    "fulfillment_id": item.get("fulfillment_id", "F1"),
                    "quantity": {"count": qty},
                }
            )

            breakup.append(
                {
                    "@ondc/org/item_id": product.ondc_product_id,
                    "@ondc/org/item_quantity": {"count": qty},
                    "@ondc/org/title_type": "item",
                    "title": product.product_name,
                    "price": {"currency": "INR", "value": str(item_total)},
                    "item": {"id": product.ondc_product_id},
                }
            )

        # Add delivery & packing charges
        delivery_charge = float(self.settings.get("delivery_charge") or 0)
        if delivery_charge > 0:
            total += delivery_charge
            breakup.append(
                {
                    "@ondc/org/item_id": "F1",
                    "@ondc/org/title_type": "delivery",
                    "title": "Delivery charges",
                    "price": {"currency": "INR", "value": str(delivery_charge)},
                }
            )

        packing_charge = float(self.settings.get("packing_charge") or 0)
        if packing_charge > 0:
            total += packing_charge
            breakup.append(
                {
                    "@ondc/org/item_id": "F1",
                    "@ondc/org/title_type": "packing",
                    "title": "Packing charges",
                    "price": {"currency": "INR", "value": str(packing_charge)},
                }
            )

        # Build fulfillments with state
        confirm_fulfillments = []
        for f in fulfillments:
            ff = {
                "id": f.get("id", "F1"),
                "type": f.get("type", "Delivery"),
                "tracking": False,
                "state": {"descriptor": {"code": "Pending"}},
                "@ondc/org/provider_name": self.settings.store_name or self.settings.subscriber_id,
                "@ondc/org/category": "Standard Delivery",
                "@ondc/org/TAT": self.settings.get("delivery_tat") or "PT60M",
                "start": {
                    "location": {
                        "id": f"LOC-{self.settings.city}",
                        "descriptor": {"name": self.settings.store_name or "Store"},
                        "gps": self.settings.store_gps or "12.9716,77.5946",
                        "address": {
                            "locality": self.settings.get("store_locality") or "Store Area",
                            "city": self.settings.get("store_city_name") or "Bangalore",
                            "area_code": self.settings.get("store_area_code") or "560001",
                            "state": self.settings.get("store_state") or "Karnataka",
                        },
                    },
                    "contact": {
                        "phone": self.settings.get("store_phone") or "9999999999",
                        "email": self.settings.get("store_email") or "store@example.com",
                    },
                    "time": {
                        "range": {
                            "start": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                            "end": (datetime.utcnow() + timedelta(hours=1)).strftime(
                                "%Y-%m-%dT%H:%M:%S.000Z"
                            ),
                        }
                    },
                },
            }
            if f.get("end"):
                ff["end"] = f["end"]
            confirm_fulfillments.append(ff)

        # Payment response
        payment_response = {
            "type": payment.get("type", "ON-ORDER"),
            "collected_by": "BAP",
            "status": "PAID" if payment.get("status") == "PAID" else "NOT-PAID",
            "params": payment.get("params", {}),
            "@ondc/org/buyer_app_finder_fee_type": payment.get(
                "@ondc/org/buyer_app_finder_fee_type", "percent"
            ),
            "@ondc/org/buyer_app_finder_fee_amount": payment.get(
                "@ondc/org/buyer_app_finder_fee_amount", "3"
            ),
            "@ondc/org/settlement_details": [
                {
                    "settlement_counterparty": "seller-app",
                    "settlement_phase": "sale-amount",
                    "settlement_type": "neft",
                    "settlement_bank_account_no": self.settings.get("bank_account_no") or "1234567890",
                    "settlement_ifsc_code": self.settings.get("ifsc_code") or "SBIN0000001",
                    "beneficiary_name": self.settings.get("beneficiary_name") or self.settings.store_name or "Seller",
                    "bank_name": self.settings.get("bank_name") or "State Bank of India",
                    "branch_name": self.settings.get("branch_name") or "Main Branch",
                }
            ],
        }

        order_id = order.get("id") or (ondc_order.name if ondc_order else frappe.generate_hash(length=16))

        payload = {
            "context": context,
            "message": {
                "order": {
                    "id": order_id,
                    "state": "Accepted",
                    "provider": {"id": self.settings.subscriber_id},
                    "items": confirmed_items,
                    "billing": billing,
                    "fulfillments": confirm_fulfillments,
                    "quote": {
                        "price": {"currency": "INR", "value": str(total)},
                        "breakup": breakup,
                        "ttl": "P1D",
                    },
                    "payment": payment_response,
                    "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "tags": [
                        {
                            "code": "bpp_terms",
                            "list": [
                                {"code": "tax_number", "value": self.settings.get("gst_number") or "22AAAAA0000A1Z5"},
                                {"code": "provider_tax_number", "value": self.settings.get("gst_number") or "22AAAAA0000A1Z5"},
                            ],
                        }
                    ],
                }
            },
        }
        return self.send_callback(
            confirm_request.get("context", {}).get("bap_uri"), "/on_confirm", payload
        )

    # -----------------------------------------------------------------------
    # Status (on_status)
    # -----------------------------------------------------------------------
    def on_status(self, status_request):
        """Send order status response"""
        context = self.create_context("on_status", status_request.get("context"))
        order_id = status_request.get("message", {}).get("order_id") or \
                   status_request.get("message", {}).get("order", {}).get("id", "")

        # Lookup order
        order_data = self._get_order_data(order_id)

        if not order_data:
            # Return error if order not found
            payload = {
                "context": context,
                "error": {
                    "type": "DOMAIN-ERROR",
                    "code": "30004",
                    "message": f"Order {order_id} not found",
                },
            }
        else:
            payload = {
                "context": context,
                "message": {"order": order_data},
            }

        return self.send_callback(
            status_request.get("context", {}).get("bap_uri"), "/on_status", payload
        )

    # -----------------------------------------------------------------------
    # Update (on_update) — Seller-side partial cancellation / modifications
    # -----------------------------------------------------------------------
    def on_update(self, update_request):
        """Handle order update (partial cancellation, quantity change, etc.)"""
        context = self.create_context("on_update", update_request.get("context"))
        order = update_request.get("message", {}).get("order", {})
        order_id = order.get("id", "")

        # Determine update type from the update target
        update_target = update_request.get("message", {}).get("update_target", "")

        # Get existing order data
        existing_order = self._get_order_data(order_id)
        if not existing_order:
            payload = {
                "context": context,
                "error": {
                    "type": "DOMAIN-ERROR",
                    "code": "30004",
                    "message": f"Order {order_id} not found",
                },
            }
            return self.send_callback(
                update_request.get("context", {}).get("bap_uri"), "/on_update", payload
            )

        # Process the update — merge incoming changes
        updated_order = self._process_order_update(existing_order, order, update_target)

        payload = {
            "context": context,
            "message": {"order": updated_order},
        }
        return self.send_callback(
            update_request.get("context", {}).get("bap_uri"), "/on_update", payload
        )

    # -----------------------------------------------------------------------
    # Cancel (on_cancel)
    # -----------------------------------------------------------------------
    def on_cancel(self, cancel_request):
        """Handle full order cancellation"""
        context = self.create_context("on_cancel", cancel_request.get("context"))
        order_id = cancel_request.get("message", {}).get("order_id", "")
        cancellation_reason_id = cancel_request.get("message", {}).get(
            "cancellation_reason_id", ""
        )

        order_data = self._get_order_data(order_id)

        if not order_data:
            payload = {
                "context": context,
                "error": {
                    "type": "DOMAIN-ERROR",
                    "code": "30004",
                    "message": f"Order {order_id} not found",
                },
            }
        else:
            # Update order state to Cancelled
            order_data["state"] = "Cancelled"
            order_data["cancellation"] = {
                "cancelled_by": cancel_request.get("context", {}).get("bap_id", ""),
                "reason": {"id": cancellation_reason_id},
            }
            order_data["updated_at"] = datetime.utcnow().strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )

            # Update fulfillment states
            for f in order_data.get("fulfillments", []):
                f["state"] = {"descriptor": {"code": "Cancelled"}}

            # Persist
            self._update_order_in_db(order_id, order_data)

            payload = {
                "context": context,
                "message": {"order": order_data},
            }

        return self.send_callback(
            cancel_request.get("context", {}).get("bap_uri"), "/on_cancel", payload
        )

    # -----------------------------------------------------------------------
    # Build Catalog
    # -----------------------------------------------------------------------
    def build_catalog(self, search_request=None):
        """Build ONDC-compliant catalog from active ONDC Products"""

        # Fetch active products
        products = frappe.get_all(
            "ONDC Product",
            filters={"is_active": 1},
            fields=["*"],
        )

        if not products:
            frappe.log_error("No active ONDC Products found", "ONDC Catalog")
            return {"bpp/descriptor": {"name": self.settings.store_name or "Store"}, "bpp/providers": []}

        items = []
        categories = {}

        for p in products:
            doc = frappe.get_doc("ONDC Product", p.name)
            item = doc.get_ondc_format()
            items.append(item)

            # Collect categories
            cat_id = item.get("category_id", "")
            if cat_id and cat_id not in categories:
                categories[cat_id] = {
                    "id": cat_id,
                    "descriptor": {"code": cat_id, "name": cat_id},
                    "tags": [
                        {
                            "code": "type",
                            "list": [{"code": "type", "value": "custom_menu"}],
                        },
                        {
                            "code": "timing",
                            "list": [
                                {"code": "day_from", "value": "1"},
                                {"code": "day_to", "value": "5"},
                                {"code": "time_from", "value": "0800"},
                                {"code": "time_to", "value": "2200"},
                            ],
                        },
                    ],
                }

        # Build fulfillment types
        fulfillment_types = []
        if self.settings.get("enable_delivery"):
            fulfillment_types.append(
                {"id": "F1", "type": "Delivery"}
            )
        if self.settings.get("enable_self_pickup"):
            fulfillment_types.append(
                {"id": "F2", "type": "Self-Pickup"}
            )
        if not fulfillment_types:
            fulfillment_types = [{"id": "F1", "type": "Delivery"}]

        # Build payment types
        payment_types = []
        if self.settings.get("accept_prepaid"):
            payment_types.append("ON-ORDER")
        if self.settings.get("accept_cod"):
            payment_types.append("ON-FULFILLMENT")
        if not payment_types:
            payment_types = ["ON-ORDER"]

        # Provider location
        location_id = f"LOC-{self.settings.city}"
        gps = self.settings.store_gps or "12.9716,77.5946"

        # Serviceability radius
        radius = self.settings.get("delivery_radius") or "10"

        # Provider tags (timing + serviceability)
        provider_tags = [
            {
                "code": "timing",
                "list": [
                    {"code": "type", "value": "Order"},
                    {"code": "location", "value": location_id},
                    {"code": "day_from", "value": "1"},
                    {"code": "day_to", "value": "7"},
                    {"code": "time_from", "value": "0000"},
                    {"code": "time_to", "value": "2359"},
                ],
            },
            {
                "code": "timing",
                "list": [
                    {"code": "type", "value": "Delivery"},
                    {"code": "location", "value": location_id},
                    {"code": "day_from", "value": "1"},
                    {"code": "day_to", "value": "7"},
                    {"code": "time_from", "value": "0000"},
                    {"code": "time_to", "value": "2359"},
                ],
            },
            {
                "code": "timing",
                "list": [
                    {"code": "type", "value": "All"},
                    {"code": "location", "value": location_id},
                    {"code": "day_from", "value": "1"},
                    {"code": "day_to", "value": "7"},
                    {"code": "time_from", "value": "0000"},
                    {"code": "time_to", "value": "2359"},
                ],
            },
            {
                "code": "serviceability",
                "list": [
                    {"code": "location", "value": location_id},
                    {"code": "category", "value": list(categories.keys())[0] if categories else "Foodgrains"},
                    {"code": "type", "value": "12"},
                    {"code": "val", "value": radius},
                    {"code": "unit", "value": "km"},
                ],
            },
        ]

        catalog = {
            "bpp/descriptor": {
                "name": self.settings.store_name or "Store",
                "symbol": "",
                "short_desc": self.settings.get("store_short_desc") or "Online Store",
                "long_desc": self.settings.get("store_long_desc") or "Online grocery store on ONDC",
                "images": [self.settings.get("store_image") or ""],
                "tags": [
                    {
                        "code": "bpp_terms",
                        "list": [
                            {"code": "np_type", "value": "MSN"},
                        ],
                    }
                ],
            },
            "bpp/fulfillments": fulfillment_types,
            "bpp/providers": [
                {
                    "id": self.settings.subscriber_id,
                    "descriptor": {
                        "name": self.settings.store_name or "Store",
                        "symbol": self.settings.get("store_image") or "",
                        "short_desc": self.settings.get("store_short_desc") or "Online Store",
                        "long_desc": self.settings.get("store_long_desc") or "Online grocery store on ONDC",
                        "images": [self.settings.get("store_image") or ""],
                    },
                    "locations": [
                        {
                            "id": location_id,
                            "gps": gps,
                            "address": {
                                "street": self.settings.get("store_street") or "",
                                "locality": self.settings.get("store_locality") or "",
                                "city": self.settings.get("store_city_name") or "Bangalore",
                                "state": self.settings.get("store_state") or "Karnataka",
                                "area_code": self.settings.get("store_area_code") or "560001",
                            },
                            "time": {
                                "label": "enable",
                                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                "days": "1,2,3,4,5,6,7",
                                "schedule": {"holidays": []},
                                "range": {
                                    "start": "0000",
                                    "end": "2359",
                                },
                            },
                            "circle": {
                                "gps": gps,
                                "radius": {"unit": "km", "value": radius},
                            },
                        }
                    ],
                    "categories": list(categories.values()),
                    "items": items,
                    "fulfillments": fulfillment_types,
                    "tags": provider_tags,
                    "ttl": "P1D",
                    "@ondc/org/fssai_license_no": self.settings.get("fssai_license_no") or "12345678901234",
                }
            ],
        }

        return catalog

    # -----------------------------------------------------------------------
    # Order helpers
    # -----------------------------------------------------------------------
    def _create_ondc_order(self, order, context):
        """Create an ONDC Order document from confirm request"""
        try:
            order_id = order.get("id") or frappe.generate_hash(length=16)
            items = order.get("items", [])
            billing = order.get("billing", {})
            fulfillments = order.get("fulfillments", [])
            payment = order.get("payment", {})
            quote = order.get("quote", {})

            ondc_order = frappe.new_doc("ONDC Order")
            ondc_order.order_id = order_id
            ondc_order.transaction_id = context.get("transaction_id", "")
            ondc_order.bap_id = context.get("bap_id", "")
            ondc_order.bap_uri = context.get("bap_uri", "")
            ondc_order.order_state = "Accepted"

            # Billing
            ondc_order.billing_name = billing.get("name", "")
            ondc_order.billing_phone = billing.get("phone", "")
            ondc_order.billing_email = billing.get("email", "")
            billing_address = billing.get("address", {})
            ondc_order.billing_address = json.dumps(billing_address) if billing_address else ""

            # Fulfillment
            if fulfillments:
                f = fulfillments[0]
                ondc_order.fulfillment_type = f.get("type", "Delivery")
                end = f.get("end", {})
                if end:
                    end_location = end.get("location", {})
                    ondc_order.delivery_address = json.dumps(end_location.get("address", {}))
                    ondc_order.delivery_gps = end_location.get("gps", "")
                    contact = end.get("contact", {})
                    ondc_order.delivery_phone = contact.get("phone", "")

            # Payment
            ondc_order.payment_type = payment.get("type", "ON-ORDER")
            ondc_order.payment_status = payment.get("status", "NOT-PAID")

            # Quote total
            ondc_order.order_total = float(quote.get("price", {}).get("value", 0))

            # Items
            for item in items:
                try:
                    product = frappe.get_doc("ONDC Product", {"ondc_product_id": item.get("id")})
                    qty = int(item.get("quantity", {}).get("count", 1))
                    ondc_order.append(
                        "items",
                        {
                            "product_id": product.ondc_product_id,
                            "product_name": product.product_name,
                            "quantity": qty,
                            "price": float(product.price or 0),
                            "total": float(product.price or 0) * qty,
                        },
                    )
                except Exception as e:
                    frappe.log_error(f"Item error: {str(e)}", "ONDC Order Item")

            # Store full request/response JSON
            ondc_order.raw_request = json.dumps(order, indent=2)

            ondc_order.insert(ignore_permissions=True)
            frappe.db.commit()

            return ondc_order

        except Exception as e:
            frappe.log_error(f"ONDC Order Creation Error: {str(e)}\n\n{json.dumps(order, indent=2)}", "ONDC Order")
            raise

    def _get_order_data(self, order_id):
        """Get order data for status/update responses"""
        try:
            orders = frappe.get_all(
                "ONDC Order",
                filters={"order_id": order_id},
                fields=["name"],
                limit=1,
            )
            if not orders:
                return None

            order_doc = frappe.get_doc("ONDC Order", orders[0].name)

            # Build order response from stored data
            items = []
            breakup = []
            total = 0

            for item in order_doc.get("items", []):
                qty = int(item.quantity or 1)
                price = float(item.price or 0)
                item_total = float(item.total or price * qty)
                total += item_total

                items.append(
                    {
                        "id": item.product_id,
                        "fulfillment_id": "F1",
                        "quantity": {"count": qty},
                    }
                )

                breakup.append(
                    {
                        "@ondc/org/item_id": item.product_id,
                        "@ondc/org/item_quantity": {"count": qty},
                        "@ondc/org/title_type": "item",
                        "title": item.product_name,
                        "price": {"currency": "INR", "value": str(item_total)},
                        "item": {"id": item.product_id},
                    }
                )

            delivery_charge = float(self.settings.get("delivery_charge") or 0)
            if delivery_charge > 0:
                total += delivery_charge
                breakup.append(
                    {
                        "@ondc/org/item_id": "F1",
                        "@ondc/org/title_type": "delivery",
                        "title": "Delivery charges",
                        "price": {"currency": "INR", "value": str(delivery_charge)},
                    }
                )

            # Build fulfillment from DB
            fulfillment_state = "Pending"
            order_state = order_doc.order_state or "Accepted"

            if order_state == "In-progress":
                fulfillment_state = "Packed"
            elif order_state == "Completed":
                fulfillment_state = "Order-delivered"
            elif order_state == "Cancelled":
                fulfillment_state = "Cancelled"

            location_id = f"LOC-{self.settings.city}"
            gps = self.settings.store_gps or "12.9716,77.5946"

            fulfillment = {
                "id": "F1",
                "type": order_doc.fulfillment_type or "Delivery",
                "tracking": False,
                "state": {"descriptor": {"code": fulfillment_state}},
                "@ondc/org/provider_name": self.settings.store_name or self.settings.subscriber_id,
                "@ondc/org/category": "Standard Delivery",
                "@ondc/org/TAT": self.settings.get("delivery_tat") or "PT60M",
                "start": {
                    "location": {
                        "id": location_id,
                        "descriptor": {"name": self.settings.store_name or "Store"},
                        "gps": gps,
                        "address": {
                            "locality": self.settings.get("store_locality") or "Store Area",
                            "city": self.settings.get("store_city_name") or "Bangalore",
                            "area_code": self.settings.get("store_area_code") or "560001",
                            "state": self.settings.get("store_state") or "Karnataka",
                        },
                    },
                    "contact": {
                        "phone": self.settings.get("store_phone") or "9999999999",
                        "email": self.settings.get("store_email") or "store@example.com",
                    },
                },
            }

            # Add delivery end location from order
            if order_doc.delivery_address:
                try:
                    end_address = json.loads(order_doc.delivery_address)
                except (json.JSONDecodeError, TypeError):
                    end_address = {}
                fulfillment["end"] = {
                    "location": {
                        "gps": order_doc.delivery_gps or "",
                        "address": end_address,
                    },
                    "contact": {
                        "phone": order_doc.delivery_phone or "",
                    },
                }

            # Parse billing
            billing = {}
            if order_doc.billing_name:
                billing["name"] = order_doc.billing_name
                billing["phone"] = order_doc.billing_phone or ""
                billing["email"] = order_doc.billing_email or ""
                if order_doc.billing_address:
                    try:
                        billing["address"] = json.loads(order_doc.billing_address)
                    except (json.JSONDecodeError, TypeError):
                        pass

            payment = {
                "type": order_doc.payment_type or "ON-ORDER",
                "collected_by": "BAP",
                "status": order_doc.payment_status or "NOT-PAID",
                "@ondc/org/buyer_app_finder_fee_type": "percent",
                "@ondc/org/buyer_app_finder_fee_amount": "3",
                "@ondc/org/settlement_details": [
                    {
                        "settlement_counterparty": "seller-app",
                        "settlement_phase": "sale-amount",
                        "settlement_type": "neft",
                        "settlement_bank_account_no": self.settings.get("bank_account_no") or "1234567890",
                        "settlement_ifsc_code": self.settings.get("ifsc_code") or "SBIN0000001",
                        "beneficiary_name": self.settings.get("beneficiary_name") or self.settings.store_name or "Seller",
                        "bank_name": self.settings.get("bank_name") or "State Bank of India",
                        "branch_name": self.settings.get("branch_name") or "Main Branch",
                    }
                ],
            }

            order_data = {
                "id": order_doc.order_id,
                "state": order_state,
                "provider": {"id": self.settings.subscriber_id},
                "items": items,
                "billing": billing,
                "fulfillments": [fulfillment],
                "quote": {
                    "price": {"currency": "INR", "value": str(total)},
                    "breakup": breakup,
                    "ttl": "P1D",
                },
                "payment": payment,
                "created_at": order_doc.creation.strftime("%Y-%m-%dT%H:%M:%S.000Z") if order_doc.creation else "",
                "updated_at": order_doc.modified.strftime("%Y-%m-%dT%H:%M:%S.000Z") if order_doc.modified else "",
            }

            return order_data

        except Exception as e:
            frappe.log_error(f"Error getting order data for {order_id}: {str(e)}", "ONDC Order Data")
            return None

    def _process_order_update(self, existing_order, update_order, update_target):
        """Process an order update (partial cancellation, etc.)"""
        # For now, merge the update into the existing order
        updated = existing_order.copy()

        if update_order.get("items"):
            updated["items"] = update_order["items"]

        if update_order.get("fulfillments"):
            updated["fulfillments"] = update_order["fulfillments"]

        if update_order.get("quote"):
            updated["quote"] = update_order["quote"]

        updated["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")

        return updated

    def _update_order_in_db(self, order_id, order_data):
        """Update order state in the database"""
        try:
            orders = frappe.get_all(
                "ONDC Order",
                filters={"order_id": order_id},
                fields=["name"],
                limit=1,
            )
            if orders:
                order_doc = frappe.get_doc("ONDC Order", orders[0].name)
                order_doc.order_state = order_data.get("state", order_doc.order_state)
                order_doc.raw_response = json.dumps(order_data, indent=2)
                order_doc.save(ignore_permissions=True)
                frappe.db.commit()
        except Exception as e:
            frappe.log_error(f"Error updating order {order_id}: {str(e)}", "ONDC Order Update")