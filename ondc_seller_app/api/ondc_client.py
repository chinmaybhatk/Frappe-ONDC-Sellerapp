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
        unique_key_id = self.settings.unique_key_id

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
        if isinstance(request_body, dict):
            body_str = json.dumps(request_body, separators=(',', ':'), sort_keys=True)
        else:
            body_str = request_body if isinstance(request_body, str) else str(request_body)
        return base64.b64encode(hashlib.blake2b(body_str.encode()).digest()).decode()

    # -----------------------------------------------------------------------
    # HTTP helpers
    # -----------------------------------------------------------------------
    def make_request(self, endpoint, method, data=None, use_gateway=False):
        """Make HTTP request to ONDC network"""
        env = self.settings.environment
        if use_gateway:
            base_url = self.base_urls[env]["gateway"]
        else:
            base_url = self.base_urls[env]["registry"]

        url = f"{base_url}{endpoint}"
        headers = {"Content-Type": "application/json"}

        if data:
            headers["Authorization"] = self.get_auth_header(data)

        response = requests.request(
            method,
            url,
            headers=headers,
            json=data,
            timeout=30
        )
        return response

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------
    def send_search(self, search_data):
        """Send search request to ONDC gateway"""
        return self.make_request("/search", "POST", search_data, use_gateway=True)

    # -----------------------------------------------------------------------
    # Catalog / on_search callback
    # -----------------------------------------------------------------------
    def send_on_search(self, bap_uri, on_search_payload):
        """Send on_search callback to BAP"""
        url = f"{bap_uri}/on_search"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_search_payload),
        }
        response = requests.post(url, headers=headers, json=on_search_payload, timeout=30)
        return response

    # -----------------------------------------------------------------------
    # Order / select / init / confirm
    # -----------------------------------------------------------------------
    def send_on_select(self, bap_uri, on_select_payload):
        """Send on_select callback to BAP"""
        url = f"{bap_uri}/on_select"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_select_payload),
        }
        response = requests.post(url, headers=headers, json=on_select_payload, timeout=30)
        return response

    def send_on_init(self, bap_uri, on_init_payload):
        """Send on_init callback to BAP"""
        url = f"{bap_uri}/on_init"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_init_payload),
        }
        response = requests.post(url, headers=headers, json=on_init_payload, timeout=30)
        return response

    def send_on_confirm(self, bap_uri, on_confirm_payload):
        """Send on_confirm callback to BAP"""
        url = f"{bap_uri}/on_confirm"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_confirm_payload),
        }
        response = requests.post(url, headers=headers, json=on_confirm_payload, timeout=30)
        return response

    # -----------------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------------
    def send_on_status(self, bap_uri, on_status_payload):
        """Send on_status callback to BAP"""
        url = f"{bap_uri}/on_status"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_status_payload),
        }
        response = requests.post(url, headers=headers, json=on_status_payload, timeout=30)
        return response

    # -----------------------------------------------------------------------
    # Cancel
    # -----------------------------------------------------------------------
    def send_on_cancel(self, bap_uri, on_cancel_payload):
        """Send on_cancel callback to BAP"""
        url = f"{bap_uri}/on_cancel"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_cancel_payload),
        }
        response = requests.post(url, headers=headers, json=on_cancel_payload, timeout=30)
        return response

    # -----------------------------------------------------------------------
    # Track
    # -----------------------------------------------------------------------
    def send_on_track(self, bap_uri, on_track_payload):
        """Send on_track callback to BAP"""
        url = f"{bap_uri}/on_track"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_track_payload),
        }
        response = requests.post(url, headers=headers, json=on_track_payload, timeout=30)
        return response

    # -----------------------------------------------------------------------
    # Support
    # -----------------------------------------------------------------------
    def send_on_support(self, bap_uri, on_support_payload):
        """Send on_support callback to BAP"""
        url = f"{bap_uri}/on_support"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_support_payload),
        }
        response = requests.post(url, headers=headers, json=on_support_payload, timeout=30)
        return response

    # -----------------------------------------------------------------------
    # Rating
    # -----------------------------------------------------------------------
    def send_on_rating(self, bap_uri, on_rating_payload):
        """Send on_rating callback to BAP"""
        url = f"{bap_uri}/on_rating"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_rating_payload),
        }
        response = requests.post(url, headers=headers, json=on_rating_payload, timeout=30)
        return response

    # -----------------------------------------------------------------------
    # Update
    # -----------------------------------------------------------------------
    def send_on_update(self, bap_uri, on_update_payload):
        """Send on_update callback to BAP"""
        url = f"{bap_uri}/on_update"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.get_auth_header(on_update_payload),
        }
        response = requests.post(url, headers=headers, json=on_update_payload, timeout=30)
        return response

    # -----------------------------------------------------------------------
    # Registry lookup
    # -----------------------------------------------------------------------
    def lookup_subscriber(self, subscriber_id, ukid=None):
        """Look up a subscriber in the ONDC registry"""
        lookup_data = {"subscriber_id": subscriber_id}
        if ukid:
            lookup_data["ukid"] = ukid
        return self.make_request("/lookup", "POST", lookup_data)

    def verify_auth_header(self, auth_header, request_body, subscriber_id=None):
        """Verify an incoming Authorization header from ONDC network"""
        try:
            # Parse the Authorization header
            header_dict = {}
            # Remove 'Signature ' prefix
            header_str = auth_header.replace("Signature ", "")
            # Parse key=value pairs
            import re
            matches = re.findall(r'(\w+)="([^"]*)"', header_str)
            for key, value in matches:
                header_dict[key] = value

            key_id = header_dict.get("keyId", "")
            parts = key_id.split("|")
            if len(parts) != 3:
                return False, "Invalid keyId format"

            sender_subscriber_id = parts[0]
            unique_key_id = parts[1]

            # Get created and expires
            created = header_dict.get("created")
            expires = header_dict.get("expires")
            signature_b64 = header_dict.get("signature")

            if not all([created, expires, signature_b64]):
                return False, "Missing required header fields"

            # Check expiry
            current_time = int(datetime.utcnow().timestamp())
            if current_time > int(expires):
                return False, "Authorization header has expired"

            # Look up the subscriber's public key from registry
            lookup_response = self.lookup_subscriber(sender_subscriber_id, unique_key_id)
            if lookup_response.status_code != 200:
                return False, f"Registry lookup failed: {lookup_response.status_code}"

            registry_data = lookup_response.json()
            if not registry_data:
                return False, "Subscriber not found in registry"

            # Find the matching signing public key
            signing_public_key = None
            for subscriber in registry_data:
                for key_payload in subscriber.get("key_payload", []):
                    if key_payload.get("ukid") == unique_key_id:
                        signing_public_key = key_payload.get("signing_public_key")
                        break

            if not signing_public_key:
                # Try alternative field name
                for subscriber in registry_data:
                    if subscriber.get("signing_public_key"):
                        signing_public_key = subscriber.get("signing_public_key")
                        break

            if not signing_public_key:
                return False, "Could not find signing public key for subscriber"

            # Reconstruct the signing string
            digest = self._calculate_digest(request_body)
            signing_string = (
                f"(created): {created}\n"
                f"(expires): {expires}\n"
                f"digest: BLAKE-512={digest}"
            )

            # Verify the signature
            public_key_bytes = base64.b64decode(signing_public_key)
            verify_key = nacl.signing.VerifyKey(public_key_bytes)
            signature_bytes = base64.b64decode(signature_b64)

            verify_key.verify(signing_string.encode(), signature_bytes)
            return True, "Signature verified successfully"

        except nacl.exceptions.BadSignatureError:
            return False, "Invalid signature"
        except Exception as e:
            return False, f"Verification error: {str(e)}"

    # -----------------------------------------------------------------------
    # Context builder
    # -----------------------------------------------------------------------
    def create_context(self, action, domain, bap_id, bap_uri, transaction_id=None, message_id=None):
        """Create ONDC context object"""
        import uuid
        return {
            "domain": domain,
            "action": action,
            "country": "IND",
            "city": "*",
            "core_version": "1.2.0",
            "bap_id": bap_id,
            "bap_uri": bap_uri,
            "bpp_id": self.settings.subscriber_id,
            "bpp_uri": self.settings.subscriber_url,
            "transaction_id": transaction_id or str(uuid.uuid4()),
            "message_id": message_id or str(uuid.uuid4()),
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "ttl": "PT30S",
        }

    # -----------------------------------------------------------------------
    # Callback sender (generic)
    # -----------------------------------------------------------------------
    def send_callback(self, bap_uri, action, payload):
        """Send a generic callback to BAP"""
        callback_methods = {
            "on_search": self.send_on_search,
            "on_select": self.send_on_select,
            "on_init": self.send_on_init,
            "on_confirm": self.send_on_confirm,
            "on_status": self.send_on_status,
            "on_cancel": self.send_on_cancel,
            "on_track": self.send_on_track,
            "on_support": self.send_on_support,
            "on_rating": self.send_on_rating,
            "on_update": self.send_on_update,
        }

        method = callback_methods.get(action)
        if not method:
            raise ValueError(f"Unknown callback action: {action}")

        return method(bap_uri, payload)


# -----------------------------------------------------------------------
# Standalone helpers (module-level)
# -----------------------------------------------------------------------

def get_ondc_settings():
    """Get ONDC Settings document"""
    return frappe.get_doc("ONDC Settings", "ONDC Settings")


def get_ondc_client():
    """Get an initialized ONDCClient"""
    settings = get_ondc_settings()
    return ONDCClient(settings)


# -----------------------------------------------------------------------
# Search handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_search(body=None):
    """Handle incoming search request from ONDC gateway"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        message = body.get("message", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")
        domain = context.get("domain")

        # Store the search request
        search_doc = frappe.get_doc({
            "doctype": "ONDC Search Request",
            "transaction_id": transaction_id,
            "message_id": message_id,
            "bap_id": bap_id,
            "bap_uri": bap_uri,
            "domain": domain,
            "search_intent": json.dumps(message),
            "status": "Received",
        })
        search_doc.insert(ignore_permissions=True)
        frappe.db.commit()

        # Send ACK immediately
        ack_response = {
            "context": context,
            "message": {"ack": {"status": "ACK"}},
        }

        # Trigger async catalog building
        frappe.enqueue(
            "ondc_seller_app.api.ondc_client.build_and_send_catalog",
            queue="short",
            timeout=120,
            search_request_id=search_doc.name,
        )

        return ack_response

    except Exception as e:
        frappe.log_error(f"Search handler error: {str(e)}", "ONDC Search Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


@frappe.whitelist()
def build_and_send_catalog(search_request_id):
    """Build catalog and send on_search callback"""
    try:
        search_doc = frappe.get_doc("ONDC Search Request", search_request_id)
        client = get_ondc_client()

        # Build catalog from active listings
        items = []
        listings = frappe.get_all(
            "ONDC Product Listing",
            filters={"status": "Active"},
            fields=["*"],
        )

        for listing in listings:
            item = {
                "id": listing.name,
                "descriptor": {
                    "name": listing.item_name,
                    "short_desc": listing.get("short_description", ""),
                    "long_desc": listing.get("long_description", ""),
                },
                "price": {
                    "currency": "INR",
                    "value": str(listing.price),
                },
                "quantity": {
                    "available": {"count": str(listing.get("available_quantity", 0))},
                },
                "category_id": listing.get("category", ""),
                "fulfillment_id": "F1",
                "@ondc/org/returnable": listing.get("returnable", False),
                "@ondc/org/cancellable": listing.get("cancellable", True),
                "@ondc/org/return_window": listing.get("return_window", "P7D"),
                "@ondc/org/seller_pickup_return": listing.get("seller_pickup_return", False),
                "@ondc/org/time_to_ship": listing.get("time_to_ship", "PT24H"),
                "@ondc/org/available_on_cod": listing.get("available_on_cod", False),
                "@ondc/org/contact_details_consumer_care": listing.get("consumer_care_contact", ""),
            }
            items.append(item)

        settings = get_ondc_settings()
        on_search_payload = {
            "context": client.create_context(
                "on_search",
                search_doc.domain or "nic2004:52110",
                search_doc.bap_id,
                search_doc.bap_uri,
                search_doc.transaction_id,
                search_doc.message_id,
            ),
            "message": {
                "catalog": {
                    "bpp/descriptor": {
                        "name": settings.company_name or "Seller",
                        "short_desc": settings.get("company_description", ""),
                    },
                    "bpp/providers": [
                        {
                            "id": settings.subscriber_id,
                            "descriptor": {
                                "name": settings.company_name or "Seller",
                            },
                            "items": items,
                            "fulfillments": [
                                {
                                    "id": "F1",
                                    "type": "Delivery",
                                    "contact": {
                                        "phone": settings.get("support_phone", ""),
                                        "email": settings.get("support_email", ""),
                                    },
                                }
                            ],
                        }
                    ],
                }
            },
        }

        response = client.send_on_search(search_doc.bap_uri, on_search_payload)

        search_doc.status = "Catalog Sent" if response.status_code == 200 else "Failed"
        search_doc.response_payload = json.dumps(on_search_payload)
        search_doc.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(f"Catalog build error: {str(e)}", "ONDC Catalog Builder")
        try:
            search_doc.status = "Failed"
            search_doc.error_message = str(e)
            search_doc.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            pass


# -----------------------------------------------------------------------
# Select handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_select(body=None):
    """Handle incoming select request"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        message = body.get("message", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")

        # Calculate quote for selected items
        order_items = message.get("order", {}).get("items", [])
        fulfillments = message.get("order", {}).get("fulfillments", [])

        quote_items = []
        total_price = 0

        for item in order_items:
            item_id = item.get("id")
            quantity = item.get("quantity", {}).get("count", 1)

            listing = frappe.get_doc("ONDC Product Listing", item_id)
            item_total = float(listing.price) * int(quantity)
            total_price += item_total

            quote_items.append({
                "id": item_id,
                "title": listing.item_name,
                "price": {"currency": "INR", "value": str(item_total)},
                "quantity": {"count": quantity},
                "item": {
                    "price": {"currency": "INR", "value": str(listing.price)},
                    "quantity": {"available": {"count": str(listing.available_quantity)}},
                },
            })

        client = get_ondc_client()
        on_select_payload = {
            "context": client.create_context(
                "on_select", context.get("domain"), bap_id, bap_uri,
                transaction_id, message_id
            ),
            "message": {
                "order": {
                    "provider": {"id": get_ondc_settings().subscriber_id},
                    "items": order_items,
                    "fulfillments": fulfillments,
                    "quote": {
                        "price": {"currency": "INR", "value": str(total_price)},
                        "breakup": quote_items,
                        "ttl": "PT15M",
                    },
                }
            },
        }

        response = client.send_on_select(bap_uri, on_select_payload)

        return {"context": context, "message": {"ack": {"status": "ACK"}}}

    except Exception as e:
        frappe.log_error(f"Select handler error: {str(e)}", "ONDC Select Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


# -----------------------------------------------------------------------
# Init handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_init(body=None):
    """Handle incoming init request"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        message = body.get("message", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")

        order = message.get("order", {})
        billing = order.get("billing", {})
        fulfillments = order.get("fulfillments", [])

        client = get_ondc_client()
        settings = get_ondc_settings()

        on_init_payload = {
            "context": client.create_context(
                "on_init", context.get("domain"), bap_id, bap_uri,
                transaction_id, message_id
            ),
            "message": {
                "order": {
                    "provider": {"id": settings.subscriber_id},
                    "items": order.get("items", []),
                    "billing": billing,
                    "fulfillments": fulfillments,
                    "quote": order.get("quote", {}),
                    "payment": {
                        "type": "ON-ORDER",
                        "@ondc/org/settlement_details": [],
                    },
                }
            },
        }

        response = client.send_on_init(bap_uri, on_init_payload)

        return {"context": context, "message": {"ack": {"status": "ACK"}}}

    except Exception as e:
        frappe.log_error(f"Init handler error: {str(e)}", "ONDC Init Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


# -----------------------------------------------------------------------
# Confirm handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_confirm(body=None):
    """Handle incoming confirm request - create Sales Order"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        message = body.get("message", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")

        order = message.get("order", {})

        # Create ONDC Order in Frappe
        ondc_order = frappe.get_doc({
            "doctype": "ONDC Order",
            "transaction_id": transaction_id,
            "message_id": message_id,
            "bap_id": bap_id,
            "bap_uri": bap_uri,
            "domain": context.get("domain"),
            "order_payload": json.dumps(order),
            "status": "Confirmed",
        })
        ondc_order.insert(ignore_permissions=True)
        frappe.db.commit()

        client = get_ondc_client()
        settings = get_ondc_settings()

        on_confirm_payload = {
            "context": client.create_context(
                "on_confirm", context.get("domain"), bap_id, bap_uri,
                transaction_id, message_id
            ),
            "message": {
                "order": {
                    "id": ondc_order.name,
                    "state": "Accepted",
                    "provider": {"id": settings.subscriber_id},
                    "items": order.get("items", []),
                    "billing": order.get("billing", {}),
                    "fulfillments": order.get("fulfillments", []),
                    "quote": order.get("quote", {}),
                    "payment": order.get("payment", {}),
                    "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                }
            },
        }

        response = client.send_on_confirm(bap_uri, on_confirm_payload)

        return {"context": context, "message": {"ack": {"status": "ACK"}}}

    except Exception as e:
        frappe.log_error(f"Confirm handler error: {str(e)}", "ONDC Confirm Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


# -----------------------------------------------------------------------
# Status handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_status(body=None):
    """Handle incoming status request"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        message = body.get("message", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")
        order_id = message.get("order_id")

        # Get the order
        try:
            ondc_order = frappe.get_doc("ONDC Order", {"transaction_id": transaction_id})
            order_state = ondc_order.status
            fulfillment_state = "Pending"
            if order_state == "Confirmed":
                fulfillment_state = "Pending"
            elif order_state == "Processing":
                fulfillment_state = "Order-picked-up"
            elif order_state == "Shipped":
                fulfillment_state = "Out-for-delivery"
            elif order_state == "Delivered":
                fulfillment_state = "Order-delivered"
            elif order_state == "Cancelled":
                fulfillment_state = "Cancelled"
        except frappe.DoesNotExistError:
            order_state = "Unknown"
            fulfillment_state = "Pending"

        client = get_ondc_client()
        settings = get_ondc_settings()

        on_status_payload = {
            "context": client.create_context(
                "on_status", context.get("domain"), bap_id, bap_uri,
                transaction_id, message_id
            ),
            "message": {
                "order": {
                    "id": order_id,
                    "state": order_state,
                    "provider": {"id": settings.subscriber_id},
                    "fulfillments": [
                        {
                            "id": "F1",
                            "state": {
                                "descriptor": {"code": fulfillment_state}
                            },
                        }
                    ],
                    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                }
            },
        }

        response = client.send_on_status(bap_uri, on_status_payload)

        return {"context": context, "message": {"ack": {"status": "ACK"}}}

    except Exception as e:
        frappe.log_error(f"Status handler error: {str(e)}", "ONDC Status Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


# -----------------------------------------------------------------------
# Cancel handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_cancel(body=None):
    """Handle incoming cancel request"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        message = body.get("message", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")

        order_id = message.get("order_id")
        cancellation_reason_id = message.get("cancellation_reason_id", "001")

        # Update order status
        try:
            ondc_order = frappe.get_doc("ONDC Order", {"transaction_id": transaction_id})
            ondc_order.status = "Cancelled"
            ondc_order.cancellation_reason = cancellation_reason_id
            ondc_order.save(ignore_permissions=True)
            frappe.db.commit()
            order_items = json.loads(ondc_order.order_payload).get("items", [])
        except frappe.DoesNotExistError:
            order_items = message.get("order", {}).get("items", [])

        client = get_ondc_client()
        settings = get_ondc_settings()

        on_cancel_payload = {
            "context": client.create_context(
                "on_cancel", context.get("domain"), bap_id, bap_uri,
                transaction_id, message_id
            ),
            "message": {
                "order": {
                    "id": order_id,
                    "state": "Cancelled",
                    "provider": {"id": settings.subscriber_id},
                    "items": order_items,
                    "fulfillments": [
                        {
                            "id": "F1",
                            "state": {
                                "descriptor": {"code": "Cancelled"}
                            },
                        }
                    ],
                    "cancellation": {
                        "cancelled_by": "CONSUMER",
                        "reason": {"id": cancellation_reason_id},
                    },
                    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                }
            },
        }

        response = client.send_on_cancel(bap_uri, on_cancel_payload)

        return {"context": context, "message": {"ack": {"status": "ACK"}}}

    except Exception as e:
        frappe.log_error(f"Cancel handler error: {str(e)}", "ONDC Cancel Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


# -----------------------------------------------------------------------
# Track handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_track(body=None):
    """Handle incoming track request"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        message = body.get("message", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")

        client = get_ondc_client()
        settings = get_ondc_settings()

        on_track_payload = {
            "context": client.create_context(
                "on_track", context.get("domain"), bap_id, bap_uri,
                transaction_id, message_id
            ),
            "message": {
                "tracking": {
                    "status": "active",
                    "url": f"{settings.subscriber_url}/tracking/{transaction_id}",
                }
            },
        }

        response = client.send_on_track(bap_uri, on_track_payload)

        return {"context": context, "message": {"ack": {"status": "ACK"}}}

    except Exception as e:
        frappe.log_error(f"Track handler error: {str(e)}", "ONDC Track Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


# -----------------------------------------------------------------------
# Support handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_support(body=None):
    """Handle incoming support request"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")

        client = get_ondc_client()
        settings = get_ondc_settings()

        on_support_payload = {
            "context": client.create_context(
                "on_support", context.get("domain"), bap_id, bap_uri,
                transaction_id, message_id
            ),
            "message": {
                "support": {
                    "ref_id": transaction_id,
                    "callback_phone": settings.get("support_phone", "+91-0000000000"),
                    "email": settings.get("support_email", "support@example.com"),
                }
            },
        }

        response = client.send_on_support(bap_uri, on_support_payload)

        return {"context": context, "message": {"ack": {"status": "ACK"}}}

    except Exception as e:
        frappe.log_error(f"Support handler error: {str(e)}", "ONDC Support Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


# -----------------------------------------------------------------------
# Rating handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_rating(body=None):
    """Handle incoming rating request"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        message = body.get("message", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")

        client = get_ondc_client()
        settings = get_ondc_settings()

        on_rating_payload = {
            "context": client.create_context(
                "on_rating", context.get("domain"), bap_id, bap_uri,
                transaction_id, message_id
            ),
            "message": {
                "feedback_form": {
                    "form": {
                        "url": f"{settings.subscriber_url}/feedback",
                        "mime_type": "text/html",
                    },
                    "required": False,
                }
            },
        }

        response = client.send_on_rating(bap_uri, on_rating_payload)

        return {"context": context, "message": {"ack": {"status": "ACK"}}}

    except Exception as e:
        frappe.log_error(f"Rating handler error: {str(e)}", "ONDC Rating Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


# -----------------------------------------------------------------------
# Update handler
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_update(body=None):
    """Handle incoming update request"""
    try:
        if body is None:
            body = frappe.request.get_data(as_text=True)
            if isinstance(body, str):
                body = json.loads(body)

        context = body.get("context", {})
        message = body.get("message", {})
        transaction_id = context.get("transaction_id")
        message_id = context.get("message_id")
        bap_id = context.get("bap_id")
        bap_uri = context.get("bap_uri")

        order = message.get("order", {})
        update_target = message.get("update_target", "")

        # Get existing order
        try:
            ondc_order = frappe.get_doc("ONDC Order", {"transaction_id": transaction_id})
            existing_order = json.loads(ondc_order.order_payload)
        except frappe.DoesNotExistError:
            existing_order = order

        client = get_ondc_client()
        settings = get_ondc_settings()

        on_update_payload = {
            "context": client.create_context(
                "on_update", context.get("domain"), bap_id, bap_uri,
                transaction_id, message_id
            ),
            "message": {
                "order": {
                    "id": existing_order.get("id", transaction_id),
                    "state": "Accepted",
                    "provider": {"id": settings.subscriber_id},
                    "items": order.get("items", existing_order.get("items", [])),
                    "billing": order.get("billing", existing_order.get("billing", {})),
                    "fulfillments": order.get("fulfillments", existing_order.get("fulfillments", [])),
                    "quote": order.get("quote", existing_order.get("quote", {})),
                    "payment": order.get("payment", existing_order.get("payment", {})),
                    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                }
            },
        }

        response = client.send_on_update(bap_uri, on_update_payload)

        return {"context": context, "message": {"ack": {"status": "ACK"}}}

    except Exception as e:
        frappe.log_error(f"Update handler error: {str(e)}", "ONDC Update Handler")
        return {
            "message": {
                "ack": {"status": "NACK"},
                "error": {"type": "DOMAIN-ERROR", "code": "30000", "message": str(e)},
            }
        }


# -----------------------------------------------------------------------
# Partial cancellation handler (merchant-initiated)
# -----------------------------------------------------------------------

@frappe.whitelist()
def process_cancel(order_name, cancelled_item_ids=None, reason_code="003"):
    """
    Process merchant-initiated (partial) cancellation and send on_cancel to BAP.

    :param order_name: ONDC Order docname
    :param cancelled_item_ids: list of item IDs being cancelled; None = full cancel
    :param reason_code: ONDC cancellation reason code
    """
    try:
        ondc_order = frappe.get_doc("ONDC Order", order_name)
        order_payload = json.loads(ondc_order.order_payload)
        all_items = order_payload.get("items", [])

        if cancelled_item_ids:
            cancelled_items = [i for i in all_items if i.get("id") in cancelled_item_ids]
        else:
            cancelled_items = all_items

        client = get_ondc_client()
        settings = get_ondc_settings()

        on_cancel_payload = {
            "context": client.create_context(
                "on_cancel",
                ondc_order.domain or "nic2004:52110",
                ondc_order.bap_id,
                ondc_order.bap_uri,
                ondc_order.transaction_id,
                frappe.utils.generate_hash(length=16),
            ),
            "message": {
                "order": {
                    "id": ondc_order.name,
                    "state": "Cancelled",
                    "provider": {"id": settings.subscriber_id},
                    "items": cancelled_items,
                    "fulfillments": [
                        {
                            "id": "F1",
                            "state": {"descriptor": {"code": "Cancelled"}},
                        }
                    ],
                    "cancellation": {
                        "cancelled_by": "SELLER",
                        "reason": {"id": reason_code},
                    },
                    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                    + "Z",
                }
            },
        }

        response = client.send_on_cancel(ondc_order.bap_uri, on_cancel_payload)

        if response.status_code == 200:
            ondc_order.status = "Cancelled"
            ondc_order.save(ignore_permissions=True)
            frappe.db.commit()
            return {"status": "success", "message": "Cancellation sent successfully"}
        else:
            return {
                "status": "error",
                "message": f"Failed to send cancellation: {response.text}",
            }

    except Exception as e:
        frappe.log_error(f"Cancel processing error: {str(e)}", "ONDC Cancel Processor")
        return {"status": "error", "message": str(e)}
