import frappe
import requests
import json
import nacl.signing
import nacl.encoding
import nacl.public
import base64
import re
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

        signing_private_key = self.settings.get_password("signing_private_key")
        if not signing_private_key:
            frappe.throw("Signing private key is not set. Please generate key pairs first.")

        raw_key = base64.b64decode(signing_private_key)
        if len(raw_key) == 64:
            raw_key = raw_key[:32]
        elif len(raw_key) == 48:
            raw_key = raw_key[:32]
        elif len(raw_key) != 32:
            frappe.throw(
                f"Invalid signing private key: expected 32, 48, or 64 bytes, got {len(raw_key)}. "
                "Please regenerate key pairs."
            )

        private_key = nacl.signing.SigningKey(raw_key)
        signature = private_key.sign(signing_string.encode()).signature
        signature_b64 = base64.b64encode(signature).decode()

        auth_header = (
            f'Signature keyId="{self.settings.subscriber_id}|{self.settings.unique_key_id}|ed25519",'
            f'algorithm="ed25519",created="{created}",expires="{expires}",'
            f'headers="(created) (expires) digest",signature="{signature_b64}"'
        )
        return auth_header

    def _calculate_digest(self, request_body):
        """Calculate BLAKE-512 digest of request body"""
        body_str = json.dumps(request_body, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.blake2b(body_str.encode(), digest_size=64).digest()
        return base64.b64encode(digest).decode()

    # -----------------------------------------------------------------------
    # HTTP helpers
    # -----------------------------------------------------------------------
    def make_request(self, endpoint, method, data=None, use_gateway=False):
        """Make HTTP request to ONDC network"""
        env = self.settings.environment
        base_url = self.base_urls[env]["gateway" if use_gateway else "registry"]
        url = f"{base_url}{endpoint}"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if data:
            headers["Authorization"] = self.get_auth_header(data)

        try:
            response = requests.request(
                method=method, url=url, headers=headers, json=data, timeout=30
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"ONDC API Error: {str(e)}", "ONDC Client")
            return {"success": False, "error": str(e)}

    def send_callback(self, callback_url, endpoint, payload):
        """Send callback to BAP"""
        if not callback_url:
            return {"success": False, "error": "No callback URL provided"}

        url = callback_url.rstrip("/") + endpoint
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(payload),
        }

        try:
            response = requests.post(url=url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except requests.exceptions.RequestException as e:
            error_detail = str(e)
            response_body = ""
            if hasattr(e, "response") and e.response is not None:
                try:
                    response_body = e.response.text[:500]
                    error_detail = f"{str(e)} | Response: {response_body}"
                except Exception:
                    pass
            frappe.log_error(
                f"ONDC Callback Error to {url}:\n{error_detail}\n\nPayload context: {json.dumps(payload.get('context', {}), indent=2)}",
                "ONDC Callback"
            )
            return {"success": False, "error": str(e), "response_body": response_body}

    # -----------------------------------------------------------------------
    # Context
    # -----------------------------------------------------------------------
    def create_context(self, action, request_context=None):
        """Create context object for callback responses"""
        context = {
            "domain": (
                (request_context.get("domain") if request_context else None)
                or self.settings.domain
            ),
            "country": (
                (request_context.get("country") if request_context else None)
                or "IND"
            ),
            "city": (
                (request_context.get("city") if request_context else None)
                or self.settings.city
            ),
            "action": action,
            "core_version": (
                (request_context.get("core_version") if request_context else None)
                or "1.2.0"
            ),
            "bap_id": request_context.get("bap_id") if request_context else None,
            "bap_uri": request_context.get("bap_uri") if request_context else None,
            "bpp_id": self.settings.subscriber_id,
            "bpp_uri": self.settings.subscriber_url,
            "transaction_id": (
                request_context.get("transaction_id")
                if request_context
                else frappe.generate_hash(length=16)
            ),
            "message_id": (
                request_context.get("message_id")
                if request_context
                else frappe.generate_hash(length=16)
            ),
            # V18 FIX: Use strftime to avoid microseconds in timestamp
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ttl": "PT30S",
        }
        return context

    # -----------------------------------------------------------------------
    # Registry Onboarding
    # -----------------------------------------------------------------------
    def subscribe(self):
        """Register participant on ONDC network"""
        payload = {
            "context": {"operation": {"ops_no": 1}},
            "message": {
                "request_id": frappe.generate_hash(length=16),
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "entity": {
                    "gst": {
                        "legal_entity_name": self.settings.legal_entity_name,
                        "business_id": self.settings.gst_no,
                        "city_code": self.settings.city,
                    },
                    "pan": {
                        "name_as_per_pan": self.settings.legal_entity_name,
                        "pan_no": self.settings.pan_no,
                        "date_of_incorporation": "01/01/2020",
                    },
                },
                "network_participant": [
                    {
                        "subscriber_id": self.settings.subscriber_id,
                        "subscriber_url": self.settings.subscriber_url,
                        "domain": self.settings.domain,
                        "type": self.settings.participant_type,
                        "msn": False,
                        "city_code": self.settings.city,
                    }
                ],
                "key_pair": {
                    "signing_public_key": self.settings.signing_public_key,
                    "encryption_public_key": self.settings.encryption_public_key,
                    "valid_from": datetime.utcnow().isoformat() + "Z",
                    "valid_until": (datetime.utcnow() + timedelta(days=365)).isoformat() + "Z",
                },
                "callback_url": self.settings.webhook_url,
            },
        }
        return self.make_request("/subscribe", "POST", payload)

    def handle_on_subscribe(self, data):
        """Handle /on_subscribe callback from ONDC registry"""
        try:
            subscriber_id = data.get("subscriber_id")
            challenge = data.get("challenge")

            if not challenge:
                return {"success": False, "error": "No challenge provided"}

            enc_private_key_b64 = self.settings.get_password("encryption_private_key")
            if not enc_private_key_b64:
                return {"success": False, "error": "Encryption private key is not set"}
            enc_private_key_bytes = base64.b64decode(enc_private_key_b64)

            private_key = nacl.public.PrivateKey(enc_private_key_bytes)
            decrypted_challenge = challenge

            return {
                "success": True,
                "answer": decrypted_challenge,
            }
        except Exception as e:
            frappe.log_error(f"on_subscribe error: {str(e)}", "ONDC Registry")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------------
    # Callback Handlers (on_* methods)
    # -----------------------------------------------------------------------
    def on_search(self, search_request):
        """Send catalog response"""
        context = self.create_context("on_search", search_request.get("context"))
        catalog = self.build_catalog(search_request)

        payload = {"context": context, "message": {"catalog": catalog}}
        return self.send_callback(
            search_request.get("context", {}).get("bap_uri"), "/on_search", payload
        )

    def on_select(self, select_request):
        """Send quote response"""
        context = self.create_context("on_select", select_request.get("context"))
        order = self.calculate_quote(select_request.get("message", {}).get("order", {}))

        payload = {"context": context, "message": {"order": order}}
        frappe.log_error(
            title="ONDC on_select payload",
            message=json.dumps(payload, indent=2, default=str)[:10000]
        )
        return self.send_callback(
            select_request.get("context", {}).get("bap_uri"), "/on_select", payload
        )

    def on_init(self, init_request):
        """Send payment terms response"""
        context = self.create_context("on_init", init_request.get("context"))
        order = self.add_payment_terms(init_request.get("message", {}).get("order", {}))

        payload = {"context": context, "message": {"order": order}}
        frappe.log_error(
            title="ONDC on_init payload",
            message=json.dumps(payload, indent=2, default=str)[:10000]
        )
        return self.send_callback(
            init_request.get("context", {}).get("bap_uri"), "/on_init", payload
        )

    def on_confirm(self, confirm_request):
        """Send order confirmation"""
        context = self.create_context("on_confirm", confirm_request.get("context"))
        order = self.create_order(confirm_request.get("message", {}).get("order", {}))

        payload = {"context": context, "message": {"order": order}}
        return self.send_callback(
            confirm_request.get("context", {}).get("bap_uri"), "/on_confirm", payload
        )

    def on_status(self, status_request):
        """Send order status response"""
        context = self.create_context("on_status", status_request.get("context"))

        order_id = status_request.get("message", {}).get("order_id")
        if not order_id:
            return {"success": False, "error": "Missing order_id"}

        try:
            order = frappe.get_doc("ONDC Order", {"ondc_order_id": order_id})
        except frappe.DoesNotExistError:
            return {"success": False, "error": f"Order not found: {order_id}"}

        fulfillment_state = order.get("fulfillment_state") or "Pending"

        order_payload = {
            "id": order.ondc_order_id,
            "state": order.order_status,
            "provider": {"id": self.settings.subscriber_id},
            "fulfillments": [
                {
                    "id": order.fulfillment_id,
                    "type": order.fulfillment_type or "Delivery",
                    "state": {"descriptor": {"code": fulfillment_state}},
                }
            ],
        }

        payload = {"context": context, "message": {"order": order_payload}}
        return self.send_callback(
            status_request.get("context", {}).get("bap_uri"), "/on_status", payload
        )

    # -----------------------------------------------------------------------
    # Catalog Builder
    # -----------------------------------------------------------------------
    def build_catalog(self, search_request=None):
        """Build ONDC-compliant product catalog."""
        products = frappe.get_all(
            "ONDC Product", filters={"is_active": 1}, fields=["*"]
        )

        items = []
        category_set = set()
        for product in products:
            doc = frappe.get_doc("ONDC Product", product.name)
            item_data = doc.get_ondc_format()

            # V18 FIX: Ensure descriptor.code matches pattern ^(1|2|3|4|5):[a-zA-Z0-9]+$
            descriptor = item_data.get("descriptor", {})
            code = descriptor.get("code", "")
            if not re.match(r'^(1|2|3|4|5):[a-zA-Z0-9]+$', code):
                # Default to category 1 (Food & Beverages) with sanitized product ID
                safe_id = re.sub(r'[^a-zA-Z0-9]', '', str(doc.ondc_product_id or doc.name)[:20])
                descriptor["code"] = f"1:{safe_id}" if safe_id else "1:ITEM"
                item_data["descriptor"] = descriptor

            items.append(item_data)
            if doc.category_code:
                category_set.add(doc.category_code)

        categories = [
            {"id": cat_code, "descriptor": {"code": cat_code}}
            for cat_code in category_set
        ]

        store_gps = self.settings.get("store_gps") or "0.0,0.0"
        store_locality = self.settings.get("store_locality") or ""
        store_city = self.settings.get("store_city_name") or self.settings.city
        store_state = self.settings.get("store_state") or ""
        store_area_code = self.settings.get("store_area_code") or self.settings.city
        store_phone = self.settings.get("store_phone") or "9999999999"
        store_email = self.settings.get("store_email") or "seller@example.com"

        location_id = f"LOC-{self.settings.city}"

        operating_start = self.settings.get("operating_hours_start") or "09:00"
        operating_end = self.settings.get("operating_hours_end") or "21:00"

        store_name = self.settings.get("store_name") or self.settings.legal_entity_name or "ONDC Seller"
        store_short_desc = self.settings.get("store_short_desc") or "Quality products at best prices"
        store_long_desc = self.settings.get("store_long_desc") or "We provide a wide range of products with fast delivery"
        store_logo = self.settings.get("store_logo") or ""
        store_images = [store_logo] if store_logo else []

        # V17 FIX: Add contact to fulfillment types
        fulfillment_types = []
        if self.settings.get("support_delivery"):
            fulfillment_types.append({
                "id": "F1",
                "type": "Delivery",
                "contact": {"phone": store_phone, "email": store_email}
            })
        if self.settings.get("support_pickup"):
            fulfillment_types.append({
                "id": "F2",
                "type": "Self-Pickup",
                "contact": {"phone": store_phone, "email": store_email}
            })
        if not fulfillment_types:
            fulfillment_types.append({
                "id": "F1",
                "type": "Delivery",
                "contact": {"phone": store_phone, "email": store_email}
            })

        payment_types = []
        if self.settings.get("support_prepaid"):
            payment_types.append({
                "id": "P1",
                "type": "PRE-FULFILLMENT",
                "collected_by": "BAP",
            })
        if self.settings.get("support_cod"):
            payment_types.append({
                "id": "P2",
                "type": "ON-FULFILLMENT",
                "collected_by": "BPP",
            })
        if not payment_types:
            payment_types.append({
                "id": "P1",
                "type": "PRE-FULFILLMENT",
                "collected_by": "BAP",
            })

        default_time_to_ship = self.settings.get("default_time_to_ship") or "PT60M"

        provider_tags = [
            {
                "code": "timing",
                "list": [
                    {"code": "type", "value": "All"},
                    {"code": "location", "value": location_id},
                    {"code": "day_from", "value": "1"},
                    {"code": "day_to", "value": "7"},
                    {"code": "time_from", "value": operating_start.replace(":", "")},
                    {"code": "time_to", "value": operating_end.replace(":", "")},
                ],
            },
            {
                "code": "serviceability",
                "list": [
                    {"code": "location", "value": location_id},
                    {"code": "category", "value": self.settings.domain},
                    {"code": "type", "value": "12"},
                    {"code": "val", "value": self.settings.get("serviceable_radius") or "10"},
                    {"code": "unit", "value": "km"},
                ],
            },
        ]

        # V17 FIX: Add tags to bpp/descriptor with bpp_terms
        bpp_descriptor = {
            "name": store_name,
            "short_desc": store_short_desc,
            "long_desc": store_long_desc,
            "symbol": store_logo,
            "images": store_images,
            "tags": [
                {
                    "code": "bpp_terms",
                    "list": [
                        {"code": "np_type", "value": "MSN"},
                    ],
                }
            ],
        }

        # V17 FIX: Convert operating hours to RFC3339 format for catalog
        today_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_time_rfc = f"{today_date}T{operating_start}:00.000Z"
        end_time_rfc = f"{today_date}T{operating_end}:00.000Z"

        catalog = {
            "bpp/descriptor": bpp_descriptor,
            "bpp/fulfillments": fulfillment_types,
            "bpp/providers": [
                {
                    "id": self.settings.subscriber_id,
                    "descriptor": {
                        "name": store_name,
                        "short_desc": store_short_desc,
                        "long_desc": store_long_desc,
                        "symbol": store_logo,
                        "images": store_images,
                    },
                    # V17 FIX: Add time object at provider level
                    "time": {
                        "label": "enable",
                        # V18 FIX: strftime to avoid microseconds
                        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                    "locations": [
                        {
                            "id": location_id,
                            "gps": store_gps,
                            "address": {
                                # V17 FIX: Add street field to address
                                "street": store_locality,
                                "locality": store_locality,
                                "city": store_city,
                                "state": store_state,
                                "country": "IND",
                                "area_code": store_area_code,
                            },
                            "time": {
                                "label": "enable",
                                # V18 FIX: strftime to avoid microseconds
                                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "days": "1,2,3,4,5,6,7",
                                "schedule": {
                                    # V18 FIX: holidays must have at least 1 element
                                    "holidays": ["2026-08-15"],
                                    # V18 FIX: frequency must have minLength 1
                                    "frequency": "PT4H",
                                    # V17 FIX: Convert times to RFC3339 format
                                    "times": [start_time_rfc, end_time_rfc],
                                },
                                # V17 FIX: Convert time range to RFC3339 format
                                "range": {
                                    "start": start_time_rfc,
                                    "end": end_time_rfc,
                                },
                            },
                        }
                    ],
                    "categories": categories,
                    "items": items,
                    "fulfillments": fulfillment_types,
                    "tags": provider_tags,
                    "ttl": "PT24H",
                }
            ],
        }
        return catalog

    # -----------------------------------------------------------------------
    # Quote calculation
    # -----------------------------------------------------------------------
    def _get_effective_quantity(self, product):
        """Get effective available quantity for a product."""
        raw_qty = int(product.available_quantity or 0)
        env = self.settings.environment
        if env in ("preprod", "staging"):
            return max(raw_qty, 99)
        return raw_qty

    def calculate_quote(self, order):
        """Calculate quote for selected items with proper price breakup."""
        provider_id = order.get("provider", {}).get("id")
        items = order.get("items", [])

        item_total = 0.0
        quote_breakup = []
        resolved_items = []

        for item in items:
            item_id = item.get("id")
            quantity = int(item.get("quantity", {}).get("count", 1))

            product = None
            try:
                product = frappe.get_doc("ONDC Product", {"ondc_product_id": item_id})
            except frappe.DoesNotExistError:
                pass

            if not product:
                continue

            available = self._get_effective_quantity(product)
            if available <= 0:
                continue

            actual_qty = min(quantity, available)
            price = float(product.price or 0)
            line_total = price * actual_qty
            item_total += line_total

            resolved_items.append({
                "id": item_id,
                "fulfillment_id": "F1",
                "quantity": {"count": actual_qty},
                "price": {"currency": "INR", "value": str(price)},
            })

            # V17 FIX: Add item object to quote_breakup for on_select
            quote_breakup.append({
                "title": product.product_name or item_id,
                "@ondc/org/item_id": item_id,
                "@ondc/org/item_quantity": {"count": actual_qty},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
                "item": {
                    "quantity": {
                        "available": {"count": str(available)},
                        "maximum": {"count": str(available)},
                    },
                    "price": {"currency": "INR", "value": str(price)},
                },
            })

        # Delivery charges
        delivery_charge = float(self.settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        # Packing charges
        packing_charge = float(self.settings.get("default_packing_charge") or 0)
        if packing_charge > 0:
            quote_breakup.append({
                "title": "Packing charges",
                "@ondc/org/item_id": "F1",
                "@ondc/org/title_type": "packing",
                "price": {"currency": "INR", "value": str(packing_charge)},
            })

        # Tax
        tax_rate = float(self.settings.get("default_tax_rate") or 0)
        tax_amount = round(item_total * tax_rate / 100, 2) if tax_rate > 0 else 0
        if tax_amount > 0:
            quote_breakup.append({
                "title": "Tax",
                "@ondc/org/item_id": "F1",
                "@ondc/org/title_type": "tax",
                "price": {"currency": "INR", "value": str(tax_amount)},
            })

        grand_total = item_total + delivery_charge + packing_charge + tax_amount

        order["items"] = resolved_items
        order["quote"] = {
            "price": {"currency": "INR", "value": str(round(grand_total, 2))},
            "breakup": quote_breakup,
            "ttl": "PT15M",
        }

        default_tat = self.settings.get("default_time_to_ship") or "PT60M"
        order["fulfillments"] = [
            {
                "id": "F1",
                "type": "Delivery",
                "@ondc/org/provider_name": self.settings.get("store_name") or self.settings.legal_entity_name,
                "tracking": False,
                "@ondc/org/category": "Immediate Delivery",
                "@ondc/org/TAT": default_tat,
                "state": {"descriptor": {"code": "Serviceable"}},
            }
        ]

        return order

    # -----------------------------------------------------------------------
    # Payment terms
    # -----------------------------------------------------------------------
    def add_payment_terms(self, order):
        """Build the complete on_init response."""
        # ----- 1. Provider -----
        order["provider"] = {
            "id": self.settings.subscriber_id,
            "locations": [{"id": f"LOC-{self.settings.city}"}],
        }

        # ----- 2. Resolve items & build quote -----
        items_in = order.get("items", [])
        resolved_items = []
        quote_breakup = []
        item_total = 0.0

        for item in items_in:
            item_id = item.get("id")
            quantity = int(item.get("quantity", {}).get("count", 1))
            product = None
            try:
                product = frappe.get_doc("ONDC Product", {"ondc_product_id": item_id})
            except frappe.DoesNotExistError:
                pass
            if not product:
                continue

            available = self._get_effective_quantity(product)
            price = float(product.price or 0)
            line_total = price * quantity

            resolved_items.append({
                "id": item_id,
                "fulfillment_id": "F1",
                "quantity": {"count": quantity},
                "price": {"currency": "INR", "value": str(price)},
            })

            # V17 FIX: Add item object to quote_breakup for on_init
            quote_breakup.append({
                "title": product.product_name or item_id,
                "@ondc/org/item_id": item_id,
                "@ondc/org/item_quantity": {"count": quantity},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
                "item": {
                    "quantity": {
                        "available": {"count": str(available)},
                        "maximum": {"count": str(available)},
                    },
                    "price": {"currency": "INR", "value": str(price)},
                },
            })
            item_total += line_total

        # Delivery charges
        delivery_charge = float(self.settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        # Packing charges
        packing_charge = float(self.settings.get("default_packing_charge") or 0)
        if packing_charge > 0:
            quote_breakup.append({
                "title": "Packing charges",
                "@ondc/org/item_id": "F1",
                "@ondc/org/title_type": "packing",
                "price": {"currency": "INR", "value": str(packing_charge)},
            })

        # Tax
        tax_rate = float(self.settings.get("default_tax_rate") or 0)
        tax_amount = round(item_total * tax_rate / 100, 2) if tax_rate > 0 else 0
        if tax_amount > 0:
            quote_breakup.append({
                "title": "Tax",
                "@ondc/org/item_id": "F1",
                "@ondc/org/title_type": "tax",
                "price": {"currency": "INR", "value": str(tax_amount)},
            })

        grand_total = item_total + delivery_charge + packing_charge + tax_amount

        if resolved_items:
            order["items"] = resolved_items
        order["quote"] = {
            "price": {"currency": "INR", "value": str(round(grand_total, 2))},
            "breakup": quote_breakup,
            "ttl": "PT15M",
        }

        # ----- 3. Fulfillments -----
        fulfillments_in = order.get("fulfillments", [])
        end_block = {}
        if fulfillments_in:
            end_block = fulfillments_in[0].get("end", {})

        default_tat = self.settings.get("default_time_to_ship") or "PT60M"
        store_gps = self.settings.get("store_gps") or "0.0,0.0"
        store_locality = self.settings.get("store_locality") or ""
        store_city = self.settings.get("store_city_name") or self.settings.city
        store_state = self.settings.get("store_state") or ""
        store_area_code = self.settings.get("store_area_code") or self.settings.city
        store_name = self.settings.get("store_name") or self.settings.legal_entity_name or "ONDC Seller"

        fulfillment = {
            "id": "F1",
            "type": "Delivery",
            "@ondc/org/provider_name": store_name,
            "tracking": False,
            "@ondc/org/category": "Immediate Delivery",
            "@ondc/org/TAT": default_tat,
            "state": {"descriptor": {"code": "Serviceable"}},
            "start": {
                "location": {
                    "id": f"LOC-{self.settings.city}",
                    "descriptor": {"name": store_name},
                    "gps": store_gps,
                    "address": {
                        "locality": store_locality,
                        "city": store_city,
                        "state": store_state,
                        "country": "IND",
                        "area_code": store_area_code,
                    },
                },
                "contact": {
                    "phone": self.settings.get("store_phone") or "9999999999",
                    "email": self.settings.get("store_email") or "seller@example.com",
                },
            },
        }
        if end_block:
            fulfillment["end"] = end_block
        order["fulfillments"] = [fulfillment]

        # ----- 4. Payment terms -----
        existing_payment = order.get("payment", {})
        buyer_payment_type = existing_payment.get("type", "ON-ORDER")

        collected_by = "BAP"
        if buyer_payment_type == "ON-FULFILLMENT":
            collected_by = "BPP"

        settlement_bank_account = (
            self.settings.get("settlement_bank_account_no") or "1234567890123456"
        )
        settlement_ifsc = (
            self.settings.get("settlement_ifsc_code") or "SBIN0000001"
        )
        settlement_bank_name = (
            self.settings.get("settlement_bank_name") or "State Bank of India"
        )
        settlement_branch_name = (
            self.settings.get("settlement_branch_name") or "Bangalore Main Branch"
        )
        settlement_beneficiary = (
            self.settings.legal_entity_name or "Test Seller"
        ).strip()
        settlement_upi = (
            self.settings.get("settlement_upi_address") or ""
        )

        settlement_detail = {
            "settlement_counterparty": "seller-app",
            "settlement_phase": "sale-amount",
            "settlement_type": "neft",
            "beneficiary_name": settlement_beneficiary,
            "settlement_bank_account_no": settlement_bank_account,
            "settlement_ifsc_code": settlement_ifsc,
            "bank_name": settlement_bank_name,
            "branch_name": settlement_branch_name,
        }
        if settlement_upi:
            settlement_detail["upi_address"] = settlement_upi

        payment = {
            "type": buyer_payment_type,
            "collected_by": collected_by,
            "status": "NOT-PAID",
            "@ondc/org/buyer_app_finder_fee_type": "percent",
            "@ondc/org/buyer_app_finder_fee_amount": str(
                self.settings.get("buyer_finder_fee") or "3"
            ),
            "@ondc/org/settlement_basis": "delivery",
            "@ondc/org/settlement_window": "P2D",
            "@ondc/org/withholding_amount": "0.00",
            "@ondc/org/settlement_details": [settlement_detail],
        }
        order["payment"] = payment

        # ----- 5. Billing -----
        if "billing" not in order:
            order["billing"] = {}

        # ----- 6. Tags with bpp_terms -----
        gst_number = self.settings.get("gst_no") or "00ABCDE1234F1Z5"
        provider_tax_number = self.settings.get("provider_gst_no") or gst_number

        order["tags"] = [
            {
                "code": "bpp_terms",
                "list": [
                    {"code": "tax_number", "value": gst_number},
                    {"code": "provider_tax_number", "value": provider_tax_number},
                    {"code": "np_type", "value": "MSN"},
                ],
            }
        ]

        # ----- 7. Cancellation terms with refund_eligible -----
        # V18 FIX: Add refund_eligible to each cancellation_term
        order["cancellation_terms"] = [
            {
                "fulfillment_state": {
                    "descriptor": {
                        "code": "Pending",
                        "short_desc": "Pending"
                    }
                },
                "reason_required": False,
                "refund_eligible": True,
                "cancellation_fee": {
                    "percentage": "0",
                    "amount": {"currency": "INR", "value": "0.00"},
                },
            },
            {
                "fulfillment_state": {
                    "descriptor": {
                        "code": "Packed",
                        "short_desc": "Packed"
                    }
                },
                "reason_required": True,
                "refund_eligible": True,
                "cancellation_fee": {
                    "percentage": "0",
                    "amount": {"currency": "INR", "value": "0.00"},
                },
            },
            {
                "fulfillment_state": {
                    "descriptor": {
                        "code": "Order-picked-up",
                        "short_desc": "Order-picked-up"
                    }
                },
                "reason_required": True,
                "refund_eligible": False,
                "cancellation_fee": {
                    "percentage": "0",
                    "amount": {"currency": "INR", "value": "0.00"},
                },
            },
        ]

        return order

    # -----------------------------------------------------------------------
    # Order creation
    # -----------------------------------------------------------------------
    def create_order(self, order_data):
        """Build proper order confirmation response for /on_confirm."""
        order_id = order_data.get("id") or frappe.generate_hash(length=16)

        store_gps = self.settings.get("store_gps") or "0.0,0.0"
        store_locality = self.settings.get("store_locality") or ""
        store_city = self.settings.get("store_city_name") or self.settings.city
        store_state = self.settings.get("store_state") or ""
        store_area_code = self.settings.get("store_area_code") or self.settings.city
        store_name = self.settings.get("store_name") or self.settings.legal_entity_name or "ONDC Seller"
        store_phone = self.settings.get("store_phone") or "9999999999"
        store_email = self.settings.get("store_email") or "seller@example.com"

        # V17 FIX: Get current time and create time ranges for fulfillments
        now = datetime.utcnow()
        today_date = now.strftime("%Y-%m-%d")
        operating_start = self.settings.get("operating_hours_start") or "09:00"
        operating_end = self.settings.get("operating_hours_end") or "21:00"
        start_time_rfc = f"{today_date}T{operating_start}:00.000Z"
        end_time_rfc = f"{today_date}T{operating_end}:00.000Z"

        default_tat = self.settings.get("default_time_to_ship") or "PT60M"

        # V17 FIX: Build proper fulfillments with all required fields
        fulfillments_in = order_data.get("fulfillments", [])
        end_block = {}
        if fulfillments_in:
            end_block = fulfillments_in[0].get("end", {})

        fulfillments = [
            {
                "id": "F1",
                "type": "Delivery",
                # V18 FIX: Change state to "Pending" (not "Serviceable")
                "state": {
                    "descriptor": {
                        "code": "Pending"
                    }
                },
                "tracking": False,
                "@ondc/org/provider_name": store_name,
                "@ondc/org/category": "Immediate Delivery",
                "@ondc/org/TAT": default_tat,
                # V17 FIX: Add time.range with RFC3339 timestamps
                "start": {
                    "location": {
                        "id": f"LOC-{self.settings.city}",
                        "descriptor": {"name": store_name},
                        "gps": store_gps,
                        "address": {
                            "locality": store_locality,
                            "city": store_city,
                            "state": store_state,
                            "country": "IND",
                            "area_code": store_area_code,
                        },
                    },
                    "contact": {
                        "phone": store_phone,
                        "email": store_email,
                    },
                    # V17 FIX: Add time.range
                    "time": {
                        "range": {
                            "start": start_time_rfc,
                            "end": end_time_rfc,
                        }
                    }
                },
            }
        ]

        # V18 FIX: Add end block with time.range
        if end_block:
            fulfillments[0]["end"] = end_block
            # Ensure end block has time.range
            if "time" not in fulfillments[0]["end"]:
                fulfillments[0]["end"]["time"] = {}
            if "range" not in fulfillments[0]["end"]["time"]:
                fulfillments[0]["end"]["time"]["range"] = {
                    "start": start_time_rfc,
                    "end": end_time_rfc,
                }

        # V18 FIX: Add tags with bpp_terms
        gst_number = self.settings.get("gst_no") or "00ABCDE1234F1Z5"
        provider_tax_number = self.settings.get("provider_gst_no") or gst_number

        # V18 FIX: Use proper timestamp format (no microseconds)
        confirm_timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # V18 FIX: Echo billing timestamps from confirm request
        billing_data = order_data.get("billing", {})
        billing_created = billing_data.get("created_at", confirm_timestamp)
        billing_updated = billing_data.get("updated_at", confirm_timestamp)

        # V18 FIX: Echo order-level timestamps from request
        order_created = order_data.get("created_at", confirm_timestamp)
        order_updated = order_data.get("updated_at", confirm_timestamp)

        confirmed_order = {
            "id": order_id,
            "state": "Accepted",
            "provider": order_data.get("provider", {"id": self.settings.subscriber_id}),
            "items": order_data.get("items", []),
            "billing": order_data.get("billing", {}),
            "fulfillments": fulfillments,
            "quote": order_data.get("quote", {}),
            "payment": order_data.get("payment", {}),
            # V17 FIX: Add tags with bpp_terms
            "tags": [
                {
                    "code": "bpp_terms",
                    "list": [
                        {"code": "tax_number", "value": gst_number},
                        {"code": "provider_tax_number", "value": provider_tax_number},
                        {"code": "np_type", "value": "MSN"},
                    ],
                }
            ],
            # V18 FIX: Add cancellation_terms with "Pending" not "Serviceable", and refund_eligible
            "cancellation_terms": [
                {
                    "fulfillment_state": {
                        "descriptor": {
                            "code": "Pending",
                            "short_desc": "Pending"
                        }
                    },
                    "reason_required": False,
                    "refund_eligible": True,
                    "cancellation_fee": {
                        "percentage": "0",
                        "amount": {"currency": "INR", "value": "0.00"},
                    },
                },
                {
                    "fulfillment_state": {
                        "descriptor": {
                            "code": "Order-picked-up",
                            "short_desc": "Order-picked-up"
                        }
                    },
                    "reason_required": True,
                    "refund_eligible": False,
                    "cancellation_fee": {
                        "percentage": "0",
                        "amount": {"currency": "INR", "value": "0.00"},
                    },
                },
            ],
            # V18 FIX: Use proper timestamp format (no microseconds) and echo from request
            "created_at": order_created,
            "updated_at": order_updated,
        }

        # V18 FIX: Echo billing timestamps in the billing object
        if "billing" in confirmed_order:
            confirmed_order["billing"]["created_at"] = billing_created
            confirmed_order["billing"]["updated_at"] = billing_updated

        return confirmed_order

    # -----------------------------------------------------------------------
    # Catalog update
    # -----------------------------------------------------------------------
    def update_catalog(self, product_data):
        """Update catalog on ONDC network."""
        catalog = self.build_catalog()
        context = self.create_context("on_search")

        payload = {"context": context, "message": {"catalog": catalog}}
        return self.make_request("/on_search", "POST", payload, use_gateway=True)


# ---------------------------------------------------------------------------
# Standalone Frappe-whitelisted functions
# ---------------------------------------------------------------------------

@frappe.whitelist()
def test_connection(environment):
    """Test connection to ONDC network"""
    urls = {
        "staging": "https://staging.registry.ondc.org/health",
        "preprod": "https://preprod.registry.ondc.org/health",
        "prod": "https://prod.registry.ondc.org/health",
    }
    try:
        response = requests.get(urls[environment], timeout=10)
        return {"success": response.status_code == 200}
    except Exception:
        return {"success": False, "error": "Connection failed"}


@frappe.whitelist(allow_guest=True)
def handle_on_subscribe():
    """Handle /on_subscribe callback from ONDC registry during onboarding."""
    try:
        data = frappe.request.get_json()
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)

        result = client.handle_on_subscribe(data)

        if result.get("success"):
            return {"answer": result["answer"]}
        else:
            frappe.throw(result.get("error", "on_subscribe failed"))
    except Exception as e:
        frappe.log_error(f"on_subscribe error: {str(e)}", "ONDC Registry")
        return {"error": str(e)}
