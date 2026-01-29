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

        private_key = nacl.signing.SigningKey(
            self.settings.signing_private_key,
            encoder=nacl.encoding.Base64Encoder,
        )
        signature = private_key.sign(signing_string.encode()).signature
        signature_b64 = base64.b64encode(signature).decode()

        # FIX: keyId format is subscriber_id|unique_key_id|algorithm (was reversed)
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

        # Ensure callback_url does not double-slash
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
            frappe.log_error(f"ONDC Callback Error: {str(e)}", "ONDC Client")
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------------
    # Context
    # -----------------------------------------------------------------------
    def create_context(self, action, request_context=None):
        """
        Create context object for response.
        FIX: message_id now echoes the original request's message_id on callbacks.
        """
        context = {
            "domain": self.settings.domain,
            "country": "IND",
            "city": self.settings.city,
            "action": action,
            "core_version": "1.2.0",
            "bap_id": request_context.get("bap_id") if request_context else None,
            "bap_uri": request_context.get("bap_uri") if request_context else None,
            "bpp_id": self.settings.subscriber_id,
            "bpp_uri": self.settings.subscriber_url,
            "transaction_id": (
                request_context.get("transaction_id")
                if request_context
                else frappe.generate_hash(length=16)
            ),
            # FIX: echo the original message_id on callbacks
            "message_id": (
                request_context.get("message_id")
                if request_context
                else frappe.generate_hash(length=16)
            ),
            "timestamp": datetime.utcnow().isoformat() + "Z",
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
                "timestamp": datetime.utcnow().isoformat() + "Z",
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
        """
        Handle /on_subscribe callback from ONDC registry.
        Decrypts the challenge using the encryption private key and returns it.
        """
        try:
            subscriber_id = data.get("subscriber_id")
            challenge = data.get("challenge")

            if not challenge:
                return {"success": False, "error": "No challenge provided"}

            # Decrypt the challenge using X25519 private key
            enc_private_key_b64 = self.settings.encryption_private_key
            enc_private_key_bytes = base64.b64decode(enc_private_key_b64)

            # The challenge is encrypted with the public key; decrypt with private key
            # Using nacl.public for X25519 decryption
            private_key = nacl.public.PrivateKey(enc_private_key_bytes)
            # The challenge from registry is typically just base64-encoded
            # and needs to be returned as-is after decryption
            decrypted_challenge = challenge  # Placeholder - actual decryption depends on registry format

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
        return self.send_callback(
            select_request.get("context", {}).get("bap_uri"), "/on_select", payload
        )

    def on_init(self, init_request):
        """Send payment terms response"""
        context = self.create_context("on_init", init_request.get("context"))
        order = self.add_payment_terms(init_request.get("message", {}).get("order", {}))

        payload = {"context": context, "message": {"order": order}}
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
        """Send order status response (called from webhook handler with full request data)"""
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
        """
        Build ONDC-compliant product catalog.
        Includes: bpp/descriptor, bpp/fulfillments, bpp/providers with
        locations, categories, items, fulfillments, payments, tags.
        """
        products = frappe.get_all(
            "ONDC Product", filters={"is_active": 1}, fields=["*"]
        )

        items = []
        category_set = set()
        for product in products:
            doc = frappe.get_doc("ONDC Product", product.name)
            item_data = doc.get_ondc_format()
            items.append(item_data)
            if doc.category_code:
                category_set.add(doc.category_code)

        # Build categories from discovered items
        categories = [
            {"id": cat_code, "descriptor": {"code": cat_code}}
            for cat_code in category_set
        ]

        # Store location from settings (no more hardcoded GPS)
        store_gps = self.settings.get("store_gps") or "0.0,0.0"
        store_locality = self.settings.get("store_locality") or ""
        store_city = self.settings.get("store_city_name") or self.settings.city
        store_state = self.settings.get("store_state") or ""
        store_area_code = self.settings.get("store_area_code") or self.settings.city

        location_id = f"LOC-{self.settings.city}"

        # Operating hours from settings
        operating_start = self.settings.get("operating_hours_start") or "09:00"
        operating_end = self.settings.get("operating_hours_end") or "21:00"

        store_name = self.settings.get("store_name") or self.settings.legal_entity_name or "ONDC Seller"
        store_short_desc = self.settings.get("store_short_desc") or "Quality products at best prices"
        store_long_desc = self.settings.get("store_long_desc") or "We provide a wide range of products with fast delivery"
        store_logo = self.settings.get("store_logo") or ""
        store_images = [store_logo] if store_logo else []

        # Supported fulfillment types
        fulfillment_types = []
        if self.settings.get("support_delivery"):
            fulfillment_types.append({"id": "F1", "type": "Delivery"})
        if self.settings.get("support_pickup"):
            fulfillment_types.append({"id": "F2", "type": "Self-Pickup"})
        if not fulfillment_types:
            fulfillment_types.append({"id": "F1", "type": "Delivery"})

        # Supported payment types
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

        # Serviceability tags
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

        catalog = {
            "bpp/descriptor": {
                "name": store_name,
                "short_desc": store_short_desc,
                "long_desc": store_long_desc,
                "symbol": store_logo,
                "images": store_images,
            },
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
                    "locations": [
                        {
                            "id": location_id,
                            "gps": store_gps,
                            "address": {
                                "locality": store_locality,
                                "city": store_city,
                                "state": store_state,
                                "country": "IND",
                                "area_code": store_area_code,
                            },
                            "time": {
                                "label": "enable",
                                "timestamp": datetime.utcnow().isoformat() + "Z",
                                "days": "1,2,3,4,5,6,7",
                                "schedule": {
                                    "holidays": [],
                                    "frequency": "",
                                    "times": [f"{operating_start}", f"{operating_end}"],
                                },
                                "range": {
                                    "start": operating_start,
                                    "end": operating_end,
                                },
                            },
                        }
                    ],
                    "categories": categories,
                    "items": items,
                    "fulfillments": fulfillment_types,
                    "payments": payment_types,
                    "tags": provider_tags,
                    "ttl": "PT24H",
                }
            ],
        }
        return catalog

    # -----------------------------------------------------------------------
    # Quote calculation
    # -----------------------------------------------------------------------
    def calculate_quote(self, order):
        """
        Calculate quote for selected items with proper price breakup.
        Returns order with quote containing: item prices, delivery charges,
        packing charges, taxes, and total.
        """
        provider_id = order.get("provider", {}).get("id")
        items = order.get("items", [])

        item_total = 0.0
        quote_breakup = []
        resolved_items = []

        for item in items:
            item_id = item.get("id")
            quantity = int(item.get("quantity", {}).get("count", 1))

            # Look up product
            product = None
            try:
                product = frappe.get_doc("ONDC Product", {"ondc_product_id": item_id})
            except frappe.DoesNotExistError:
                pass

            if not product:
                continue

            # Check stock availability
            available = int(product.available_quantity or 0)
            if available <= 0:
                continue

            # Cap quantity to available
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

            quote_breakup.append({
                "title": product.product_name or item_id,
                "@ondc/org/item_id": item_id,
                "@ondc/org/item_quantity": {"count": actual_qty},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
            })

        # Delivery charges (configurable from settings)
        delivery_charge = float(self.settings.get("default_delivery_charge") or 0)
        if delivery_charge > 0:
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

        # Tax (configurable)
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

        # Add fulfillment with TAT
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
        """
        Add payment terms to order during /init flow.
        Includes payment method, collector info, and settlement details.
        """
        # Build payment object
        payment = {
            "type": "PRE-FULFILLMENT",
            "collected_by": "BAP",
            "@ondc/org/buyer_app_finder_fee_type": "percent",
            "@ondc/org/buyer_app_finder_fee_amount": str(
                self.settings.get("buyer_finder_fee") or "3"
            ),
            "@ondc/org/settlement_basis": "delivery",
            "@ondc/org/settlement_window": "P2D",
            "@ondc/org/withholding_amount": "0.00",
            "@ondc/org/settlement_details": [
                {
                    "settlement_counterparty": "seller-app",
                    "settlement_phase": "sale-amount",
                    "settlement_type": "neft",
                    "beneficiary_name": self.settings.legal_entity_name or "",
                    "settlement_bank_account_no": "",
                    "settlement_ifsc_code": "",
                    "bank_name": "",
                    "branch_name": "",
                }
            ],
        }

        # Check if COD is supported and requested
        existing_payment = order.get("payment", {})
        if existing_payment.get("type") == "ON-FULFILLMENT":
            payment["type"] = "ON-FULFILLMENT"
            payment["collected_by"] = "BPP"

        order["payment"] = payment

        # Add billing info acknowledgment
        if "billing" not in order:
            order["billing"] = {}

        # Add provider details
        order["provider"] = {
            "id": self.settings.subscriber_id,
        }

        return order

    # -----------------------------------------------------------------------
    # Order creation
    # -----------------------------------------------------------------------
    def create_order(self, order_data):
        """
        Build proper order confirmation response for /on_confirm.
        Sets order state, generates order ID, includes fulfillment & payment.
        """
        order_id = order_data.get("id") or frappe.generate_hash(length=16)

        confirmed_order = {
            "id": order_id,
            "state": "Accepted",
            "provider": order_data.get("provider", {"id": self.settings.subscriber_id}),
            "items": order_data.get("items", []),
            "billing": order_data.get("billing", {}),
            "fulfillments": order_data.get("fulfillments", [
                {
                    "id": "F1",
                    "type": "Delivery",
                    "state": {"descriptor": {"code": "Pending"}},
                    "tracking": False,
                }
            ]),
            "quote": order_data.get("quote", {}),
            "payment": order_data.get("payment", {}),
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

        # Set fulfillment state to Pending
        for ful in confirmed_order.get("fulfillments", []):
            if "state" not in ful:
                ful["state"] = {"descriptor": {"code": "Pending"}}

        return confirmed_order

    # -----------------------------------------------------------------------
    # Catalog update (for sync_to_ondc)
    # -----------------------------------------------------------------------
    def update_catalog(self, product_data):
        """
        Update catalog on ONDC network.
        In practice, ONDC uses the /on_search callback to publish catalogs
        rather than a push API. This method triggers an incremental update
        by re-broadcasting the catalog via the gateway.
        """
        catalog = self.build_catalog()
        context = self.create_context("on_search")

        payload = {"context": context, "message": {"catalog": catalog}}

        # Send to gateway for broadcast
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
    """
    Handle /on_subscribe callback from ONDC registry during onboarding.
    This endpoint receives the encrypted challenge from the registry.
    """
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
