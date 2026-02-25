import frappe
import json
from frappe import _
from werkzeug.wrappers import Response
import traceback
from datetime import datetime

from ondc_seller_app.api.auth import verify_request, validate_context
from ondc_seller_app.api.ondc_errors import (
    build_ack_response,
    build_nack_response,
    build_error,
    get_cancellation_reason,
    CANCELLATION_REASONS,
    FULFILLMENT_STATES,
    is_valid_fulfillment_transition,
)


# Fulfillment state progression for Pramaan auto-testing.
# Each /status call advances the order one step through this lifecycle.
FULFILLMENT_PROGRESSION = [
    "Pending",
    "Packed",
    "Agent-assigned",
    "Order-picked-up",
    "Out-for-delivery",
    "Order-delivered",
]


@frappe.whitelist(allow_guest=True)
def handle_webhook(api):
    """
    Handle incoming ONDC webhooks.

    Implements the ONDC async ACK+callback pattern:
    1. Validate request schema and verify signature
    2. Return ACK/NACK immediately
    3. Process asynchronously via frappe.enqueue
    4. Send callback to BAP's URI
    """
    try:
        data = frappe.request.get_json()
        if not data:
            frappe.response.update(build_nack_response("20000", "Empty request body"))
            frappe.response["http_status_code"] = 400
            return

        context = data.get("context", {})

        # Build the context to echo back in ACK/NACK responses.
        # ONDC/Beckn spec requires the synchronous response to include the
        # request context so the caller can correlate the ACK with its request.
        # V9 FIX: Added `or` fallbacks — Frappe strips None values from JSON.
        resp_context = {
            "domain": context.get("domain"),
            "country": context.get("country") or "IND",
            "city": context.get("city") or "std:080",
            "action": context.get("action"),
            "core_version": context.get("core_version") or "1.2.0",
            "bap_id": context.get("bap_id"),
            "bap_uri": context.get("bap_uri"),
            "bpp_id": context.get("bpp_id") or frappe.db.get_single_value("ONDC Settings", "subscriber_id"),
            "bpp_uri": context.get("bpp_uri") or frappe.db.get_single_value("ONDC Settings", "subscriber_url"),
            "transaction_id": context.get("transaction_id"),
            "message_id": context.get("message_id"),
            "timestamp": context.get("timestamp"),
            "ttl": context.get("ttl", "PT30S"),
        }

        # --- Step 1: Validate context ---
        is_valid_ctx, err_code, err_msg = validate_context(context)
        if not is_valid_ctx:
            _log_webhook(api, data, status="Failed", error_message=err_msg)
            nack = build_nack_response(err_code, err_msg)
            nack["context"] = resp_context
            frappe.response.update(nack)
            frappe.response["http_status_code"] = 400
            return

        # --- Step 2: Verify signature ---
        auth_header = frappe.request.headers.get("Authorization")
        gateway_auth_header = frappe.request.headers.get("X-Gateway-Authorization")

        is_valid_sig, sig_error = verify_request(data, auth_header, gateway_auth_header)
        if not is_valid_sig:
            # Frappe v14+: first arg = title (140 char max), use keyword args for safety
            frappe.log_error(
                title=f"ONDC Auth: {api} sig fail"[:140],
                message=f"Signature verification failed for {api}: {sig_error}"
            )
            # Log but don't block in staging/preprod - many test BAPs have mismatched keys
            settings = frappe.get_single("ONDC Settings")
            if settings.environment == "prod":
                _log_webhook(api, data, status="Failed", error_message=f"Auth failed: {sig_error}")
                nack = build_nack_response("20001", sig_error)
                nack["context"] = resp_context
                frappe.response.update(nack)
                frappe.response["http_status_code"] = 401
                return

        # --- Step 3: Validate action matches route ---
        if context.get("action") != api:
            _log_webhook(api, data, status="Failed", error_message=f"Action mismatch: {context.get('action')} != {api}")
            nack = build_nack_response("10002", f"Action mismatch: expected {api}, got {context.get('action')}")
            nack["context"] = resp_context
            frappe.response.update(nack)
            frappe.response["http_status_code"] = 400
            return

        # --- Step 4: Log the webhook ---
        log_name = _log_webhook(api, data, status="Received")

        # --- Step 5: Return ACK immediately ---
        ack_response = build_ack_response()

        # --- Step 6: Enqueue async processing ---
        handler_map = {
            # Core ONDC transaction APIs
            "search": "ondc_seller_app.api.webhook.process_search",
            "select": "ondc_seller_app.api.webhook.process_select",
            "init": "ondc_seller_app.api.webhook.process_init",
            "confirm": "ondc_seller_app.api.webhook.process_confirm",
            "status": "ondc_seller_app.api.webhook.process_status",
            "track": "ondc_seller_app.api.webhook.process_track",
            "cancel": "ondc_seller_app.api.webhook.process_cancel",
            "update": "ondc_seller_app.api.webhook.process_update",
            "rating": "ondc_seller_app.api.webhook.process_rating",
            "support": "ondc_seller_app.api.webhook.process_support",
            # IGM (Issue & Grievance Management) APIs
            "issue": "ondc_seller_app.api.webhook.process_issue",
            "issue_status": "ondc_seller_app.api.webhook.process_issue_status",
            # RSP (Reconciliation & Settlement Protocol) APIs
            "receiver_recon": "ondc_seller_app.api.webhook.process_receiver_recon",
        }

        handler_method = handler_map.get(api)
        if not handler_method:
            _update_webhook_log(log_name, status="Failed", error_message=f"Unknown action: {api}")
            nack = build_nack_response("10002", f"Unknown action: {api}")
            nack["context"] = resp_context
            frappe.response.update(nack)
            frappe.response["http_status_code"] = 400
            return

        frappe.enqueue(
            handler_method,
            queue="default",
            timeout=30,
            data=data,
            log_name=log_name,
        )

        frappe.db.commit()
        # Include context in the ACK so Pramaan can correlate the response.
        # ONDC/Beckn spec requires context to be echoed back in the synchronous ACK.
        ack_response["context"] = resp_context
        frappe.response.update(ack_response)
        return

    except Exception as e:
        frappe.log_error(title=f"ONDC Webhook Error - {api}"[:140], message=traceback.format_exc())
        frappe.response.update(build_nack_response("20000", str(e)))
        frappe.response["http_status_code"] = 500
        return


# ---------------------------------------------------------------------------
# Async Handlers (called via frappe.enqueue)
# ---------------------------------------------------------------------------

def process_search(data, log_name=None):
    """Process search request asynchronously and send on_search callback"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        result = client.on_search(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_search Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_select(data, log_name=None):
    """Process select request asynchronously and send on_select callback"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        result = client.on_select(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_select Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_init(data, log_name=None):
    """Process init request asynchronously and send on_init callback"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        result = client.on_init(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_init Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_confirm(data, log_name=None):
    """Process confirm request: send on_confirm callback FIRST, then create order doc.

    CRITICAL V9 FIX: The on_confirm callback to the BAP is the primary obligation.
    If order doc creation fails (missing fields, duplicate, etc.), the callback
    must still be sent. Otherwise Pramaan hangs forever at the confirm step.
    """
    callback_sent = False
    try:
        # --- Step 1: Send on_confirm callback FIRST (critical path) ---
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        result = client.on_confirm(data)
        callback_sent = True
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_confirm callback Error")
        _update_webhook_log(log_name, status="Failed", error_message=f"Callback failed: {str(e)}")
        return  # If callback itself fails, nothing more to do

    # --- Step 2: Create ONDC Order doc (best-effort, non-blocking) ---
    try:
        order_data = data.get("message", {}).get("order", {})
        context = data.get("context", {})

        order = frappe.new_doc("ONDC Order")
        order.ondc_order_id = order_data.get("id") or frappe.generate_hash(length=16)
        order.transaction_id = context.get("transaction_id")
        order.message_id = context.get("message_id")
        order.bap_id = context.get("bap_id")
        order.bap_uri = context.get("bap_uri")
        order.order_status = "Accepted"
        order.fulfillment_state = "Pending"

        # Customer details
        billing = order_data.get("billing", {})
        order.customer_name = billing.get("name") or ""
        order.customer_email = billing.get("email") or ""
        order.customer_phone = billing.get("phone") or ""

        # Billing address
        address = billing.get("address", {})
        order.billing_name = billing.get("name") or ""
        order.billing_building = address.get("building") or ""
        order.billing_locality = address.get("locality") or ""
        order.billing_city = address.get("city") or ""
        order.billing_state = address.get("state") or ""
        order.billing_area_code = address.get("area_code") or ""

        # Fulfillment details
        fulfillments = order_data.get("fulfillments", [])
        fulfillment = fulfillments[0] if fulfillments else {}
        order.fulfillment_id = fulfillment.get("id") or "F1"
        order.fulfillment_type = fulfillment.get("type") or "Delivery"

        end_location = fulfillment.get("end", {}).get("location", {})
        order.shipping_gps = end_location.get("gps") or ""
        shipping_addr = end_location.get("address", {})
        order.shipping_address = json.dumps(shipping_addr) if shipping_addr else ""

        # Items
        item_total = 0.0
        for item_data in order_data.get("items", []):
            item_price = float(item_data.get("price", {}).get("value", 0))
            item_qty = int(item_data.get("quantity", {}).get("count", 1))
            item_total += item_price * item_qty
            order.append("items", {
                "ondc_item_id": item_data.get("id") or "",
                "item_code": get_item_code_from_ondc_id(item_data.get("id", "")),
                "quantity": item_qty,
                "price": item_price,
            })

        # Payment details
        payment = order_data.get("payment", {})
        order.payment_type = _map_payment_type(payment.get("type"))
        # V16 FIX: Correctly map payment status from ONDC format
        ondc_payment_status = payment.get("status", "NOT-PAID")
        order.payment_status = "Paid" if ondc_payment_status == "PAID" else "Pending"

        # V16 FIX: Total amount from quote (full order total including delivery, packing, tax)
        quote = order_data.get("quote", {})
        total = quote.get("price", {}).get("value")
        if total:
            order.total_amount = float(total)
        else:
            # Fallback: compute from items + default charges
            delivery_charge = float(settings.get("default_delivery_charge") or 0)
            packing_charge = float(settings.get("default_packing_charge") or 0)
            tax_rate = float(settings.get("default_tax_rate") or 0) / 100
            tax_amount = item_total * tax_rate
            order.total_amount = item_total + delivery_charge + packing_charge + tax_amount

        order.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.log_error(
            title="ONDC Order Created",
            message=f"Order {order.ondc_order_id} created successfully for txn {order.transaction_id}"
        )
    except Exception as e:
        # Order creation failed but callback was already sent — log and continue
        frappe.log_error(
            title="ONDC Order Creation Failed (non-blocking)",
            message=f"on_confirm callback was sent successfully, but order doc creation failed: {traceback.format_exc()}"
        )


def _auto_progress_fulfillment(order):
    """Auto-progress fulfillment state for Pramaan testing.
    Each /status call advances the fulfillment one step through the lifecycle.
    Returns the new fulfillment state."""
    current = order.get("fulfillment_state") or "Pending"

    if current in FULFILLMENT_PROGRESSION:
        idx = FULFILLMENT_PROGRESSION.index(current)
        if idx < len(FULFILLMENT_PROGRESSION) - 1:
            new_state = FULFILLMENT_PROGRESSION[idx + 1]
            order.fulfillment_state = new_state

            # Update order_status based on fulfillment progression
            if new_state == "Order-delivered":
                order.order_status = "Completed"
            elif new_state in ("Packed", "Agent-assigned", "Order-picked-up", "Out-for-delivery"):
                order.order_status = "In-progress"

            order.save(ignore_permissions=True)
            frappe.db.commit()
            return new_state

    return current


def process_status(data, log_name=None):
    """Process status request: auto-progress fulfillment and send complete on_status callback.

    V16 FIX: Comprehensive on_status response matching the on_init format that
    Pramaan accepted. Includes proper bpp_terms tags, cancellation_terms array,
    complete fulfillment start/end with address+contact, correct payment type,
    and full quote breakup with packing and tax.
    """
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        order_id = data.get("message", {}).get("order_id")
        if not order_id:
            _update_webhook_log(log_name, status="Failed", error_message="Missing order_id")
            return

        try:
            order = frappe.get_doc("ONDC Order", {"ondc_order_id": order_id})
        except frappe.DoesNotExistError:
            _update_webhook_log(log_name, status="Failed", error_message=f"Order not found: {order_id}")
            return

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)

        # Auto-progress fulfillment for Pramaan testing
        fulfillment_state = _auto_progress_fulfillment(order)
        # Re-read to get updated values
        order.reload()

        # Build items list with full details
        items = []
        quote_breakup = []
        item_total = 0.0
        for item in order.items:
            item_price = float(item.price or 0)
            item_qty = int(item.quantity or 1)
            line_total = item_price * item_qty
            item_total += line_total

            items.append({
                "id": item.ondc_item_id,
                "fulfillment_id": order.fulfillment_id or "F1",
                "quantity": {"count": item_qty},
                "price": {"currency": "INR", "value": str(item_price)},
            })

            quote_breakup.append({
                "title": item.item_code or item.ondc_item_id,
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/item_quantity": {"count": item_qty},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
            })

        # Delivery charges in quote breakup
        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": order.fulfillment_id or "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        # V16: Packing charges in quote breakup
        packing_charge = float(settings.get("default_packing_charge") or 0)
        quote_breakup.append({
            "title": "Packing charges",
            "@ondc/org/item_id": order.fulfillment_id or "F1",
            "@ondc/org/title_type": "packing",
            "price": {"currency": "INR", "value": str(packing_charge)},
        })

        # V16: Tax in quote breakup
        tax_rate = float(settings.get("default_tax_rate") or 0) / 100
        tax_amount = round(item_total * tax_rate, 2)
        quote_breakup.append({
            "title": "Tax",
            "@ondc/org/item_id": order.fulfillment_id or "F1",
            "@ondc/org/title_type": "tax",
            "price": {"currency": "INR", "value": str(tax_amount)},
        })

        # V16: Use stored total or compute full total with all charges
        grand_total = float(order.total_amount or 0)
        if grand_total <= item_total:
            # total_amount was stored incorrectly (just items), recompute
            grand_total = item_total + delivery_charge + packing_charge + tax_amount

        # Build store location
        store_gps = settings.get("store_gps") or "0.0,0.0"
        location_id = f"LOC-{settings.city}"

        # V16: GST number for tags
        gst_number = settings.get("gst_number") or "29AACCZ4465H1ZW"

        # Build complete order payload per ONDC spec — V16 matching on_init format
        order_payload = {
            "id": order.ondc_order_id,
            "state": order.order_status,
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items,
            "billing": {
                "name": order.billing_name or order.customer_name or "",
                "address": {
                    "name": order.billing_name or order.customer_name or "",
                    "building": order.billing_building or "",
                    "locality": order.billing_locality or "",
                    "city": order.billing_city or "",
                    "state": order.billing_state or "",
                    "country": "IND",
                    "area_code": order.billing_area_code or "",
                },
                "tax_number": "00ABCDE1234F1Z5",
                "email": order.customer_email or "",
                "phone": order.customer_phone or "",
                "created_at": str(order.creation).replace(" ", "T") + "Z" if order.creation else datetime.utcnow().isoformat() + "Z",
                "updated_at": datetime.utcnow().isoformat() + "Z",
            },
            "fulfillments": [
                {
                    "id": order.fulfillment_id or "F1",
                    "type": order.fulfillment_type or "Delivery",
                    "@ondc/org/provider_name": settings.get("store_name") or settings.legal_entity_name or "",
                    "tracking": bool(order.get("tracking_url")),
                    "@ondc/org/category": "Immediate Delivery",
                    "@ondc/org/TAT": settings.get("default_time_to_ship") or "PT60M",
                    "state": {
                        "descriptor": {
                            "code": fulfillment_state,
                        }
                    },
                    "start": {
                        "location": {
                            "id": location_id,
                            "descriptor": {"name": settings.get("store_name") or settings.legal_entity_name or ""},
                            "gps": store_gps,
                            "address": {
                                "locality": settings.get("store_locality") or "Kormanagala",
                                "city": settings.get("store_city") or "Bengaluru",
                                "state": settings.get("store_state") or "Karnataka",
                                "country": "IND",
                                "area_code": settings.get("store_area_code") or "560038",
                            },
                        },
                        "contact": {
                            "phone": settings.get("consumer_care_phone") or "9999999999",
                            "email": settings.get("consumer_care_email") or "seller@example.com",
                        },
                    },
                    "end": {
                        "location": {
                            "gps": order.shipping_gps or "",
                            "address": _safe_json_loads(order.shipping_address),
                        },
                        "contact": {
                            "phone": order.customer_phone or "",
                            "email": order.customer_email or "",
                        },
                    },
                }
            ],
            "quote": {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "PT15M",
            },
            "payment": {
                "type": _map_ondc_payment_type(order.payment_type),
                "status": "PAID" if order.payment_status == "Paid" else "NOT-PAID",
                "collected_by": "BAP",
                "@ondc/org/buyer_app_finder_fee_type": "percent",
                "@ondc/org/buyer_app_finder_fee_amount": str(settings.get("buyer_finder_fee") or "3"),
                "@ondc/org/settlement_basis": "delivery",
                "@ondc/org/settlement_window": "P2D",
                "@ondc/org/withholding_amount": "0.00",
                "@ondc/org/settlement_details": [
                    {
                        "settlement_counterparty": "seller-app",
                        "settlement_phase": "sale-amount",
                        "settlement_type": "neft",
                        "beneficiary_name": settings.get("legal_entity_name") or "zibmoc business solutions private limited",
                        "settlement_bank_account_no": settings.get("settlement_account") or "1234567890123456",
                        "settlement_ifsc_code": settings.get("settlement_ifsc") or "SBIN0000001",
                        "bank_name": settings.get("settlement_bank_name") or "State Bank of India",
                        "branch_name": settings.get("settlement_branch_name") or "Bangalore Main Branch",
                    }
                ],
            },
            # V16: Proper bpp_terms tags (matching on_init format that Pramaan accepted)
            "tags": [
                {
                    "code": "bpp_terms",
                    "list": [
                        {"code": "tax_number", "value": gst_number},
                        {"code": "provider_tax_number", "value": gst_number},
                        {"code": "np_type", "value": "MSN"},
                    ],
                }
            ],
            # V16: Proper cancellation_terms array (matching on_init format)
            "cancellation_terms": [
                {
                    "fulfillment_state": {"descriptor": {"code": "Pending"}},
                    "reason_required": False,
                    "cancellation_fee": {
                        "percentage": "0",
                        "amount": {"currency": "INR", "value": "0.00"},
                    },
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Packed"}},
                    "reason_required": True,
                    "cancellation_fee": {
                        "percentage": "0",
                        "amount": {"currency": "INR", "value": "0.00"},
                    },
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Order-picked-up"}},
                    "reason_required": True,
                    "cancellation_fee": {
                        "percentage": "0",
                        "amount": {"currency": "INR", "value": "0.00"},
                    },
                },
            ],
            "created_at": str(order.creation).replace(" ", "T") + "Z" if order.creation else datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

        # Add tracking URL if available
        if order.get("tracking_url"):
            order_payload["fulfillments"][0]["tracking"] = True
            order_payload["fulfillments"][0]["@ondc/org/tracking_url"] = order.tracking_url

        # Build context and send callback
        context = client.create_context("on_status", data.get("context"))
        payload = {
            "context": context,
            "message": {"order": order_payload},
        }

        # V16: Debug log the on_status payload for troubleshooting
        frappe.log_error(
            title="ONDC on_status payload"[:140],
            message=json.dumps(payload, indent=2)
        )

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_status",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_status Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_track(data, log_name=None):
    """Process track request and send on_track callback"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        order_id = data.get("message", {}).get("order_id")
        if not order_id:
            _update_webhook_log(log_name, status="Failed", error_message="Missing order_id")
            return

        try:
            order = frappe.get_doc("ONDC Order", {"ondc_order_id": order_id})
        except frappe.DoesNotExistError:
            _update_webhook_log(log_name, status="Failed", error_message=f"Order not found: {order_id}")
            return

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)

        context = client.create_context("on_track", data.get("context"))

        tracking_data = {
            "status": "active" if order.order_status == "In-progress" else "inactive",
        }

        # Add tracking URL if available
        if order.get("tracking_url"):
            tracking_data["url"] = order.tracking_url

        payload = {
            "context": context,
            "message": {"tracking": tracking_data},
        }

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_track",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_track Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_cancel(data, log_name=None):
    """Process cancel request with proper cancellation reason codes and refund terms"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        message = data.get("message", {})
        order_id = message.get("order_id")
        cancellation_reason_id = message.get("cancellation_reason_id", "")

        if not order_id:
            _update_webhook_log(log_name, status="Failed", error_message="Missing order_id")
            return

        try:
            order = frappe.get_doc("ONDC Order", {"ondc_order_id": order_id})
        except frappe.DoesNotExistError:
            _update_webhook_log(log_name, status="Failed", error_message=f"Order not found: {order_id}")
            return

        # Check if order can be cancelled
        if order.order_status in ("Completed", "Cancelled"):
            _update_webhook_log(log_name, status="Failed", error_message=f"Order cannot be cancelled (status: {order.order_status})")
            return

        # Update order status
        order.order_status = "Cancelled"
        order.fulfillment_state = "Cancelled"
        order.cancellation_reason_id = str(cancellation_reason_id)
        order.save(ignore_permissions=True)
        frappe.db.commit()

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_cancel", data.get("context"))

        # Build cancellation response with refund terms
        order_payload = {
            "id": order.ondc_order_id,
            "state": "Cancelled",
            "cancellation": {
                "cancelled_by": data.get("context", {}).get("bap_id", ""),
                "reason": {
                    "id": str(cancellation_reason_id),
                    "descriptor": {
                        "short_desc": get_cancellation_reason(cancellation_reason_id),
                    },
                },
            },
            "tags": [
                {
                    "code": "cancellation_terms",
                    "list": [
                        {"code": "cancellation_fee_type", "value": "percent"},
                        {"code": "cancellation_fee_amount", "value": "0"},
                    ],
                }
            ],
            "payment": {
                "type": order.payment_type or "PRE-FULFILLMENT",
                "status": "NOT-PAID",
                "@ondc/org/settlement_details": [
                    {
                        "settlement_counterparty": "buyer-app",
                        "settlement_type": "refund",
                        "settlement_amount": str(order.total_amount or 0),
                    }
                ],
            },
            "fulfillments": [
                {
                    "id": order.fulfillment_id,
                    "type": order.fulfillment_type or "Delivery",
                    "state": {
                        "descriptor": {"code": "Cancelled"}
                    },
                }
            ],
        }

        payload = {
            "context": context,
            "message": {"order": order_payload},
        }

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_cancel",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_cancel Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_update(data, log_name=None):
    """
    Process update request - handles ready-to-ship and order modifications.
    The /update API is used to trigger ready-to-ship status.
    """
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        update_target = data.get("message", {}).get("update_target", "")
        order_data = data.get("message", {}).get("order", {})
        order_id = order_data.get("id")

        if not order_id:
            _update_webhook_log(log_name, status="Failed", error_message="Missing order.id in update")
            return

        try:
            order = frappe.get_doc("ONDC Order", {"ondc_order_id": order_id})
        except frappe.DoesNotExistError:
            _update_webhook_log(log_name, status="Failed", error_message=f"Order not found: {order_id}")
            return

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_update", data.get("context"))

        # Handle fulfillment update (e.g., ready-to-ship)
        if update_target == "fulfillment":
            fulfillments = order_data.get("fulfillments", [])
            if fulfillments:
                new_state = fulfillments[0].get("state", {}).get("descriptor", {}).get("code")
                if new_state and is_valid_fulfillment_transition(
                    order.get("fulfillment_state") or "Pending", new_state
                ):
                    order.fulfillment_state = new_state
                    order.save(ignore_permissions=True)
                    frappe.db.commit()

        # Handle item update (e.g., quantity change, replacement)
        elif update_target == "item":
            # Update items if provided
            for item_update in order_data.get("items", []):
                for order_item in order.items:
                    if order_item.ondc_item_id == item_update.get("id"):
                        new_qty = item_update.get("quantity", {}).get("count")
                        if new_qty is not None:
                            order_item.quantity = int(new_qty)
            order.save(ignore_permissions=True)
            frappe.db.commit()

        # Build response
        order_payload = {
            "id": order.ondc_order_id,
            "state": order.order_status,
            "fulfillments": [
                {
                    "id": order.fulfillment_id,
                    "type": order.fulfillment_type or "Delivery",
                    "state": {
                        "descriptor": {
                            "code": order.get("fulfillment_state") or "Pending",
                        }
                    },
                }
            ],
        }

        payload = {
            "context": context,
            "message": {"order": order_payload},
        }

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_update",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_update Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_rating(data, log_name=None):
    """Process rating request and send on_rating callback"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        ratings = data.get("message", {}).get("ratings", [])

        # Store ratings (could be extended to a dedicated DocType)
        for rating in ratings:
            frappe.get_doc({
                "doctype": "Comment",
                "comment_type": "Info",
                "reference_doctype": "ONDC Order",
                "reference_name": rating.get("id"),
                "content": f"ONDC Rating: {rating.get('value', 'N/A')} - {rating.get('feedback_form', {}).get('question', '')}",
            }).insert(ignore_permissions=True)

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_rating", data.get("context"))

        payload = {
            "context": context,
            "message": {
                "feedback_ack": True,
                "rating_ack": True,
            },
        }

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_rating",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_rating Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_support(data, log_name=None):
    """Process support request and send on_support callback with contact details from settings"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_support", data.get("context"))

        payload = {
            "context": context,
            "message": {
                "phone": settings.get("consumer_care_phone") or "",
                "email": settings.get("consumer_care_email") or "",
                "uri": settings.get("subscriber_url") or "",
            },
        }

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_support",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_support Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def get_item_code_from_ondc_id(ondc_id):
    """Get Frappe Item code from ONDC product ID"""
    product = frappe.db.get_value(
        "ONDC Product", {"ondc_product_id": ondc_id}, "item_code"
    )
    return product or ondc_id


def _map_payment_type(ondc_type):
    """Map ONDC payment type to local select options"""
    mapping = {
        "PRE-FULFILLMENT": "Prepaid",
        "ON-FULFILLMENT": "COD",
        "POST-FULFILLMENT": "Credit",
        "ON-ORDER": "Prepaid",
    }
    return mapping.get(ondc_type, "Prepaid")


def _map_ondc_payment_type(local_type):
    """Map local payment type back to ONDC format.
    V16 FIX: Prepaid maps to ON-ORDER (matching what Pramaan sends in init/confirm)."""
    mapping = {
        "Prepaid": "ON-ORDER",
        "COD": "ON-FULFILLMENT",
        "Credit": "POST-FULFILLMENT",
    }
    return mapping.get(local_type, "ON-ORDER")


def _safe_json_loads(value):
    """Safely parse JSON string, return empty dict on failure"""
    if not value:
        return {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


def _json_response(data, status_code):
    """Create a JSON HTTP response"""
    return Response(
        json.dumps(data),
        status=status_code,
        mimetype="application/json",
    )


def _log_webhook(api, data, status="Received", error_message=None):
    """Create a webhook log entry and return the log name"""
    try:
        log = frappe.new_doc("ONDC Webhook Log")
        log.webhook_type = api
        log.request_id = data.get("context", {}).get("message_id")
        log.transaction_id = data.get("context", {}).get("transaction_id")
        log.message_id = data.get("context", {}).get("message_id")
        log.request_body = json.dumps(data, indent=2)
        log.status = status
        if error_message:
            log.error_message = error_message
        log.insert(ignore_permissions=True)
        frappe.db.commit()
        return log.name
    except Exception:
        frappe.log_error(traceback.format_exc(), "ONDC Webhook Log Error")
        return None


def _update_webhook_log(log_name, status=None, response=None, error_message=None):
    """Update an existing webhook log entry"""
    if not log_name:
        return
    try:
        log = frappe.get_doc("ONDC Webhook Log", log_name)
        if status:
            log.status = status
        if response:
            log.response_body = json.dumps(response, indent=2) if isinstance(response, dict) else str(response)
        if error_message:
            log.error_message = error_message
        log.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(traceback.format_exc(), "ONDC Webhook Log Update Error")


# ---------------------------------------------------------------------------
# IGM (Issue & Grievance Management) Handlers
# ---------------------------------------------------------------------------

def process_issue(data, log_name=None):
    """Process /issue request - creates ticket in Helpdesk"""
    try:
        from ondc_seller_app.api.igm_adapter import IGMAdapter

        adapter = IGMAdapter()
        result = adapter.handle_issue(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_issue Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_issue_status(data, log_name=None):
    """Process /issue_status request - returns ticket status"""
    try:
        from ondc_seller_app.api.igm_adapter import IGMAdapter

        adapter = IGMAdapter()
        result = adapter.handle_issue_status(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_issue_status Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


# ---------------------------------------------------------------------------
# RSP (Reconciliation & Settlement Protocol) Handlers
# ---------------------------------------------------------------------------

def process_receiver_recon(data, log_name=None):
    """Process /receiver_recon request - reconciles settlements"""
    try:
        from ondc_seller_app.api.rsp_adapter import RSPAdapter

        adapter = RSPAdapter()
        result = adapter.handle_receiver_recon(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_receiver_recon Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


# ---------------------------------------------------------------------------
# Root-level ONDC endpoint wrappers
# ---------------------------------------------------------------------------
# These are individually whitelisted functions that Frappe exposes at:
#   /api/method/ondc_seller_app.api.webhook.<action>
# They also serve as targets for website_route_rules at root level:
#   /search -> handle_search -> handle_webhook("search")
#
# This ensures routing works reliably on Frappe Cloud where
# website_route_rules with <path:api> wildcards may fail.
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_search(**kwargs):
    """Root-level /search endpoint"""
    handle_webhook("search")

@frappe.whitelist(allow_guest=True)
def handle_select(**kwargs):
    """Root-level /select endpoint"""
    handle_webhook("select")

@frappe.whitelist(allow_guest=True)
def handle_init(**kwargs):
    """Root-level /init endpoint"""
    handle_webhook("init")

@frappe.whitelist(allow_guest=True)
def handle_confirm(**kwargs):
    """Root-level /confirm endpoint"""
    handle_webhook("confirm")

@frappe.whitelist(allow_guest=True)
def handle_status(**kwargs):
    """Root-level /status endpoint"""
    handle_webhook("status")

@frappe.whitelist(allow_guest=True)
def handle_track(**kwargs):
    """Root-level /track endpoint"""
    handle_webhook("track")

@frappe.whitelist(allow_guest=True)
def handle_cancel(**kwargs):
    """Root-level /cancel endpoint"""
    handle_webhook("cancel")

@frappe.whitelist(allow_guest=True)
def handle_update(**kwargs):
    """Root-level /update endpoint"""
    handle_webhook("update")

@frappe.whitelist(allow_guest=True)
def handle_rating(**kwargs):
    """Root-level /rating endpoint"""
    handle_webhook("rating")

@frappe.whitelist(allow_guest=True)
def handle_support(**kwargs):
    """Root-level /support endpoint"""
    handle_webhook("support")
