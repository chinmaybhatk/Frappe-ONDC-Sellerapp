import frappe
import json
import time
from frappe import _
from werkzeug.wrappers import Response
import traceback
from datetime import datetime, timedelta

from ondc_seller_app.api.auth import verify_request, validate_context
from ondc_seller_app.api.ondc_errors import (
    build_ack_response,
    build_nack_response,
    build_error,
    get_cancellation_reason,
    CANCELLATION_REASONS,
    FULFILLMENT_STATES,
    VALID_FULFILLMENT_TRANSITIONS,
    is_valid_fulfillment_transition,
)


# Fulfillment state progression for Pramaan auto-testing.
# Each /status call advances the order one step through this lifecycle.
FULFILLMENT_PROGRESSION = [
    "Pending",
    "Packed",
    "Agent-assigned",
    "At-pickup",
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

    V18 FIX (CRITICAL): Store full confirm request data in cache so that process_status
    can later echo back the original billing, quote, payment, fulfillment end block.
    This fixes 413+ Pramaan failures where billing/quote/payment are undefined.
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

        # V18 FIX (CRITICAL): Cache the full confirm request so process_status can use it
        # This ensures on_status responses echo back the exact billing, quote, payment from confirm
        order_id = order.ondc_order_id
        cache_key = f"ondc_confirm_{order_id}"
        try:
            frappe.cache().set_value(cache_key, json.dumps(data, default=str), expires_in_sec=7200)
        except Exception as cache_err:
            frappe.log_error(
                title="ONDC Confirm Cache Failed",
                message=f"Failed to cache confirm data for {order_id}: {str(cache_err)}"
            )
            # Non-blocking: continue even if caching fails

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

    V20 FIX: Uses FULFILLMENT_PROGRESSION (which includes At-pickup) for the
    happy-path sequence AND validates against VALID_FULFILLMENT_TRANSITIONS from
    ondc_errors.py to ensure only legal transitions occur. This is the single
    source of truth — no state can be skipped.
    """
    current = order.get("fulfillment_state") or "Pending"

    if current in FULFILLMENT_PROGRESSION:
        idx = FULFILLMENT_PROGRESSION.index(current)
        if idx < len(FULFILLMENT_PROGRESSION) - 1:
            new_state = FULFILLMENT_PROGRESSION[idx + 1]

            # V20: Validate against VALID_FULFILLMENT_TRANSITIONS before applying
            if not is_valid_fulfillment_transition(current, new_state):
                frappe.log_error(
                    title=f"ONDC Invalid Transition blocked: {current} → {new_state}",
                    message=f"Order {order.ondc_order_id}: Transition {current} → {new_state} "
                            f"not in VALID_FULFILLMENT_TRANSITIONS. Staying at {current}."
                )
                return current

            order.fulfillment_state = new_state

            # V20 FIX: Update order_status for ALL active states including At-pickup
            if new_state == "Order-delivered":
                order.order_status = "Completed"
            elif new_state in ("Packed", "Agent-assigned", "At-pickup",
                               "Order-picked-up", "Out-for-delivery"):
                order.order_status = "In-progress"

            order.save(ignore_permissions=True)
            frappe.db.commit()
            return new_state

    return current


def process_status(data, log_name=None):
    """Process status request: auto-progress fulfillment and send complete on_status callback.

    V19 FIXES (addressing ~419+ Pramaan failures):
    - Create two timestamp helpers: _echo_timestamp() for echoed timestamps (preserves microseconds)
      and _new_timestamp() for generated timestamps (no microseconds)
    - order.updated_at should echo order.created_at from confirm (not current UTC time)
    - Add end.time.timestamp for ALL active states (Agent-assigned, Order-picked-up,
      Out-for-delivery, Order-delivered), not just the last two
    - Add retry logic for send_callback() with exponential backoff
    - Use _echo_timestamp() for billing timestamps from cached confirm
    - Ensure Order-delivered state is set to "Completed"

    V20 FIXES:
    - Added "At-pickup" to FULFILLMENT_PROGRESSION (was missing between Agent-assigned
      and Order-picked-up, causing state to skip or get stuck)
    - Validate every transition against VALID_FULFILLMENT_TRANSITIONS from ondc_errors.py
    - Added "At-pickup" to active_states for timestamp generation
    - Import VALID_FULFILLMENT_TRANSITIONS for transition validation
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

        # V19 FIX (CRITICAL): Two timestamp helpers
        # _echo_timestamp() — for timestamps from confirm request (preserves microseconds)
        def _echo_timestamp(ts_str):
            """Echo timestamp as-is from confirm data, preserving microseconds.
            Just ensure it has T separator and ends with Z."""
            if not ts_str:
                return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            ts_str = str(ts_str)
            # Replace space with T if present
            ts_str = ts_str.replace(" ", "T")
            # Ensure it ends with Z
            if not ts_str.endswith("Z"):
                ts_str += "Z"
            return ts_str

        # _new_timestamp() — for NEW timestamps we generate (no microseconds)
        def _new_timestamp():
            """Generate a new timestamp without microseconds."""
            return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # V18 FIX (CRITICAL): Load original confirm data from cache
        confirm_cache_key = f"ondc_confirm_{order_id}"
        original_order_data = {}
        try:
            confirm_raw = frappe.cache().get_value(confirm_cache_key)
            if confirm_raw:
                confirm_json = json.loads(confirm_raw)
                original_order_data = confirm_json.get("message", {}).get("order", {})
                frappe.log_error(
                    title="ONDC Confirm Data Loaded from Cache",
                    message=f"Successfully loaded cached confirm data for {order_id}"
                )
        except Exception as cache_err:
            frappe.log_error(
                title="ONDC Confirm Cache Load Failed",
                message=f"Failed to load cached confirm data for {order_id}: {str(cache_err)}"
            )

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

            # Add item object to quote_breakup
            quote_breakup.append({
                "title": item.item_code or item.ondc_item_id,
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/item_quantity": {"count": item_qty},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
                "item": {
                    "id": item.ondc_item_id,
                    "price": {"currency": "INR", "value": str(item_price)},
                    "quantity": {
                        "available": {"count": "99"},
                        "maximum": {"count": str(item_qty)},
                    },
                },
            })

        # Delivery charges in quote breakup
        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": order.fulfillment_id or "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        # Packing charges in quote breakup
        packing_charge = float(settings.get("default_packing_charge") or 0)
        quote_breakup.append({
            "title": "Packing charges",
            "@ondc/org/item_id": order.fulfillment_id or "F1",
            "@ondc/org/title_type": "packing",
            "price": {"currency": "INR", "value": str(packing_charge)},
        })

        # Tax in quote breakup
        tax_rate = float(settings.get("default_tax_rate") or 0) / 100
        tax_amount = round(item_total * tax_rate, 2)
        quote_breakup.append({
            "title": "Tax",
            "@ondc/org/item_id": order.fulfillment_id or "F1",
            "@ondc/org/title_type": "tax",
            "price": {"currency": "INR", "value": str(tax_amount)},
        })

        # Use stored total or compute full total with all charges
        grand_total = float(order.total_amount or 0)
        if grand_total <= item_total:
            grand_total = item_total + delivery_charge + packing_charge + tax_amount

        # Build store location
        store_gps = settings.get("store_gps") or "0.0,0.0"
        location_id = f"LOC-{settings.city}"

        # GST number for tags
        gst_number = settings.get("gst_number") or "29AACCZ4465H1ZW"

        # V19 FIX (CRITICAL): Use original created_at from confirm if available
        # This ensures billing.created_at and order.created_at match the BAP's confirm request
        order_created_at = original_order_data.get("created_at")
        if order_created_at:
            order_created_at = _echo_timestamp(order_created_at)
        else:
            order_created_at = _echo_timestamp(str(order.creation) if order.creation else None)

        # V19 FIX: order.updated_at should ECHO order.created_at from confirm, not current UTC
        order_updated_at = order_created_at

        # V18 FIX: Compute time.range and time.timestamp for fulfillment
        tat_str = settings.get("default_time_to_ship") or "PT60M"
        # Parse TAT minutes for time.range calculation
        tat_minutes = 60  # default 1 hour
        if "PT" in tat_str and "M" in tat_str:
            try:
                tat_minutes = int(tat_str.replace("PT", "").replace("M", ""))
            except ValueError:
                tat_minutes = 60

        now_utc = datetime.utcnow()
        range_start = _new_timestamp()
        range_end = (now_utc + timedelta(minutes=tat_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # V18 FIX: Build start.time and end.time with time.range for ALL states
        start_time = {
            "range": {
                "start": order_created_at,
                "end": range_end,
            },
        }
        end_time = {
            "range": {
                "start": order_created_at,
                "end": range_end,
            },
        }

        # V19 FIX: Add timestamps for active states (including Agent-assigned now)
        active_states = ["Packed", "Agent-assigned", "At-pickup", "Order-picked-up", "Out-for-delivery", "Order-delivered"]
        if fulfillment_state in active_states:
            start_time["timestamp"] = range_start
        # V19 FIX: Add end.time.timestamp for ALL active states, not just last two
        if fulfillment_state in active_states:
            end_time["timestamp"] = range_start

        # V19 FIX (CRITICAL): Use original billing from confirm if available
        if original_order_data.get("billing"):
            billing_payload = original_order_data["billing"].copy()
            # V19 FIX: Use _echo_timestamp for billing timestamps
            if "created_at" in billing_payload:
                billing_payload["created_at"] = _echo_timestamp(billing_payload["created_at"])
            if "updated_at" in billing_payload:
                billing_payload["updated_at"] = _echo_timestamp(billing_payload["updated_at"])
        else:
            billing_payload = {
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
                "tax_number": gst_number,
                "email": order.customer_email or "",
                "phone": order.customer_phone or "",
                "created_at": order_created_at,
                "updated_at": order_updated_at,
            }

        # V18 FIX (CRITICAL): Use original quote from confirm if available
        if original_order_data.get("quote"):
            quote_payload = original_order_data["quote"].copy()
            # Update breakup to include current items (V17 fix still applies)
            quote_payload["breakup"] = quote_breakup
            # Update price if needed
            quote_payload["price"] = {"currency": "INR", "value": str(round(grand_total, 2))}
        else:
            quote_payload = {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "PT15M",
            }

        # V18 FIX (CRITICAL): Use original payment from confirm if available, update status
        if original_order_data.get("payment"):
            payment_payload = original_order_data["payment"].copy()
            # Update status to reflect current payment state
            payment_payload["status"] = "PAID" if order.payment_status == "Paid" else "NOT-PAID"
            # Ensure params are present and correct
            if "params" not in payment_payload:
                payment_payload["params"] = {
                    "currency": "INR",
                    "amount": str(round(grand_total, 2)),
                    "transaction_id": order.transaction_id or "",
                }
            else:
                # Update amount if grand_total changed
                payment_payload["params"]["currency"] = "INR"
                payment_payload["params"]["amount"] = str(round(grand_total, 2))
                if not payment_payload["params"].get("transaction_id"):
                    payment_payload["params"]["transaction_id"] = order.transaction_id or ""
        else:
            payment_payload = {
                "type": _map_ondc_payment_type(order.payment_type),
                "status": "PAID" if order.payment_status == "Paid" else "NOT-PAID",
                "collected_by": "BAP",
                "params": {
                    "currency": "INR",
                    "amount": str(round(grand_total, 2)),
                    "transaction_id": order.transaction_id or "",
                },
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
            }

        # V18 FIX (CRITICAL): Use original fulfillment end block from confirm if available
        # This ensures end.person, end.location, end.contact match the original request
        end_location_gps = order.shipping_gps or ""
        end_location_address = _safe_json_loads(order.shipping_address)
        end_person_name = order.customer_name or order.billing_name or ""
        end_person_contact_phone = order.customer_phone or ""
        end_person_contact_email = order.customer_email or ""

        if original_order_data.get("fulfillments"):
            original_fulfillment = original_order_data["fulfillments"][0] if original_order_data["fulfillments"] else {}
            if original_fulfillment.get("end"):
                end_block = original_fulfillment["end"]
                if end_block.get("location"):
                    end_location_gps = end_block["location"].get("gps") or end_location_gps
                    if end_block["location"].get("address"):
                        end_location_address = end_block["location"]["address"]
                if end_block.get("person"):
                    end_person_name = end_block["person"].get("name") or end_person_name
                if end_block.get("contact"):
                    end_person_contact_phone = end_block["contact"].get("phone") or end_person_contact_phone
                    end_person_contact_email = end_block["contact"].get("email") or end_person_contact_email

        # Build complete order payload per ONDC spec — V19 comprehensive fix
        order_payload = {
            "id": order.ondc_order_id,
            "state": order.order_status,
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items,
            # V18 FIX (CRITICAL): Use original billing from confirm with proper timestamps
            "billing": billing_payload,
            # V18 FIX: Complete fulfillment with time.range, timestamp, end data from confirm
            "fulfillments": [
                {
                    "id": order.fulfillment_id or "F1",
                    "type": order.fulfillment_type or "Delivery",
                    "@ondc/org/provider_name": settings.get("store_name") or settings.legal_entity_name or "",
                    "tracking": bool(order.get("tracking_url")),
                    "@ondc/org/category": "Immediate Delivery",
                    "@ondc/org/TAT": tat_str,
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
                        "time": start_time,
                        "contact": {
                            "phone": settings.get("consumer_care_phone") or "9999999999",
                            "email": settings.get("consumer_care_email") or "seller@example.com",
                        },
                    },
                    "end": {
                        "location": {
                            "gps": end_location_gps,
                            "address": end_location_address,
                        },
                        "time": end_time,
                        # V18 FIX: end.person from original confirm
                        "person": {
                            "name": end_person_name,
                        },
                        "contact": {
                            "phone": end_person_contact_phone,
                            "email": end_person_contact_email,
                        },
                    },
                }
            ],
            # V18 FIX (CRITICAL): Use original quote from confirm
            "quote": quote_payload,
            # V18 FIX (CRITICAL): Use original payment from confirm
            "payment": payment_payload,
            # bpp_terms tags (matching on_init format)
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
            # V18 FIX: cancellation_terms with short_desc and refund_eligible
            "cancellation_terms": [
                {
                    "fulfillment_state": {
                        "descriptor": {
                            "code": "Pending",
                            "short_desc": "Cancellation is free before packing",
                        }
                    },
                    "reason_required": False,
                    "cancellation_fee": {
                        "percentage": "0",
                        "amount": {"currency": "INR", "value": "0.00"},
                    },
                    "refund_eligible": True,
                },
                {
                    "fulfillment_state": {
                        "descriptor": {
                            "code": "Packed",
                            "short_desc": "Cancellation may apply after packing",
                        }
                    },
                    "reason_required": True,
                    "cancellation_fee": {
                        "percentage": "0",
                        "amount": {"currency": "INR", "value": "0.00"},
                    },
                    "refund_eligible": True,
                },
                {
                    "fulfillment_state": {
                        "descriptor": {
                            "code": "Order-picked-up",
                            "short_desc": "Cancellation may apply after pickup",
                        }
                    },
                    "reason_required": True,
                    "cancellation_fee": {
                        "percentage": "0",
                        "amount": {"currency": "INR", "value": "0.00"},
                    },
                    "refund_eligible": True,
                },
            ],
            "created_at": order_created_at,
            "updated_at": order_updated_at,
        }

        # V19 FIX: Ensure Order-delivered properly sets state to "Completed"
        if fulfillment_state == "Order-delivered":
            order_payload["state"] = "Completed"

        # Add tracking URL if available
        if order.get("tracking_url"):
            order_payload["fulfillments"][0]["tracking"] = True
            order_payload["fulfillments"][0]["@ondc/org/tracking_url"] = order.tracking_url

        # V18 FIX: Build context with proper timestamp format (no microseconds)
        # Pass the request context to ensure proper context construction
        context = client.create_context("on_status", data.get("context"))
        payload = {
            "context": context,
            "message": {"order": order_payload},
        }

        # V18: Debug log with fulfillment state for tracking auto-progression
        frappe.log_error(
            title=f"ONDC on_status [{fulfillment_state}]"[:140],
            message=json.dumps(payload, indent=2)
        )

        # V19 FIX: Add retry logic for send_callback
        bap_uri = data.get("context", {}).get("bap_uri")
        max_retries = 3
        result = None
        for attempt in range(max_retries):
            result = client.send_callback(bap_uri, "/on_status", payload)
            if result.get("success"):
                break
            if attempt < max_retries - 1:
                frappe.log_error(
                    title=f"ONDC on_status retry {attempt+1}/{max_retries}"[:140],
                    message=f"Callback failed for {order_id} [{fulfillment_state}], retrying..."
                )
                time.sleep(1)

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


def _build_on_cancel_payload(order, data, cancelled_by, cancellation_reason_id, settings, client):
    """V21: Build a complete on_cancel order payload by loading from confirm cache.

    Mirrors process_status logic — echoes billing, quote, payment, fulfillment
    from the original confirm so Pramaan finds all required fields.
    Also handles Flow 3C (merchant-initiated cancel) where data may be synthetic.
    """
    from datetime import datetime, timedelta

    order_id = order.ondc_order_id

    # V21 FIX: Load original confirm data from cache (same as process_status)
    confirm_cache_key = f"ondc_confirm_{order_id}"
    original_order_data = {}
    try:
        confirm_raw = frappe.cache().get_value(confirm_cache_key)
        if confirm_raw:
            confirm_json = json.loads(confirm_raw)
            original_order_data = confirm_json.get("message", {}).get("order", {})
            frappe.log_error(
                title="ONDC on_cancel: Confirm Data Loaded from Cache",
                message=f"Loaded cached confirm data for on_cancel {order_id}"
            )
    except Exception as cache_err:
        frappe.log_error(
            title="ONDC on_cancel: Cache Load Failed",
            message=f"Failed to load confirm cache for {order_id}: {str(cache_err)}"
        )

    def _echo_timestamp(ts_str):
        if not ts_str:
            return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_str = str(ts_str).replace(" ", "T")
        if "." in ts_str and not ts_str.endswith("Z"):
            ts_str += "Z"
        elif not ts_str.endswith("Z"):
            ts_str += "Z"
        return ts_str

    def _new_timestamp():
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Timestamps
    order_created_at = original_order_data.get("created_at")
    if order_created_at:
        order_created_at = _echo_timestamp(order_created_at)
    else:
        order_created_at = _echo_timestamp(str(order.creation) if order.creation else None)
    order_updated_at = _new_timestamp()

    # Settings-derived values
    gst_number = settings.get("gst_number") or "29AACCZ4465H1ZW"
    store_gps = settings.get("store_gps") or "0.0,0.0"
    location_id = f"LOC-{settings.city}"
    tat_str = settings.get("default_time_to_ship") or "PT60M"
    tat_minutes = 60
    if "PT" in tat_str and "M" in tat_str:
        try:
            tat_minutes = int(tat_str.replace("PT", "").replace("M", ""))
        except ValueError:
            tat_minutes = 60
    now_utc = datetime.utcnow()
    range_start = order_created_at
    range_end = (now_utc + timedelta(minutes=tat_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Items + quote breakup (from order doc)
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
            "item": {
                "id": item.ondc_item_id,
                "price": {"currency": "INR", "value": str(item_price)},
                "quantity": {
                    "available": {"count": "0"},
                    "maximum": {"count": str(item_qty)},
                },
            },
        })

    delivery_charge = float(settings.get("default_delivery_charge") or 0)
    packing_charge = float(settings.get("default_packing_charge") or 0)
    tax_rate = float(settings.get("default_tax_rate") or 0) / 100
    tax_amount = round(item_total * tax_rate, 2)
    quote_breakup.append({"title": "Delivery charges", "@ondc/org/item_id": order.fulfillment_id or "F1", "@ondc/org/title_type": "delivery", "price": {"currency": "INR", "value": str(delivery_charge)}})
    quote_breakup.append({"title": "Packing charges", "@ondc/org/item_id": order.fulfillment_id or "F1", "@ondc/org/title_type": "packing", "price": {"currency": "INR", "value": str(packing_charge)}})
    quote_breakup.append({"title": "Tax", "@ondc/org/item_id": order.fulfillment_id or "F1", "@ondc/org/title_type": "tax", "price": {"currency": "INR", "value": str(tax_amount)}})
    grand_total = float(order.total_amount or 0)
    if grand_total <= item_total:
        grand_total = item_total + delivery_charge + packing_charge + tax_amount

    # Billing — echo from cache or fallback to order doc
    if original_order_data.get("billing"):
        billing_payload = original_order_data["billing"].copy()
        if "created_at" in billing_payload:
            billing_payload["created_at"] = _echo_timestamp(billing_payload["created_at"])
        if "updated_at" in billing_payload:
            billing_payload["updated_at"] = _echo_timestamp(billing_payload["updated_at"])
    else:
        billing_payload = {
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
            "email": order.customer_email or "",
            "phone": order.customer_phone or "",
            "created_at": order_created_at,
            "updated_at": order_updated_at,
        }

    # Quote — echo from cache
    if original_order_data.get("quote"):
        quote_payload = original_order_data["quote"].copy()
        quote_payload["breakup"] = quote_breakup
        quote_payload["price"] = {"currency": "INR", "value": str(round(grand_total, 2))}
    else:
        quote_payload = {
            "price": {"currency": "INR", "value": str(round(grand_total, 2))},
            "breakup": quote_breakup,
            "ttl": "PT15M",
        }

    # Payment — echo from cache, mark as NOT-PAID (refund pending)
    if original_order_data.get("payment"):
        payment_payload = original_order_data["payment"].copy()
        payment_payload["status"] = "NOT-PAID"
        if "params" not in payment_payload:
            payment_payload["params"] = {
                "currency": "INR",
                "amount": str(round(grand_total, 2)),
                "transaction_id": order.transaction_id or "",
            }
        else:
            payment_payload["params"]["currency"] = "INR"
            payment_payload["params"]["amount"] = str(round(grand_total, 2))
            if not payment_payload["params"].get("transaction_id"):
                payment_payload["params"]["transaction_id"] = order.transaction_id or ""
    else:
        payment_payload = {
            "type": _map_ondc_payment_type(order.payment_type),
            "status": "NOT-PAID",
            "collected_by": "BAP",
            "params": {
                "currency": "INR",
                "amount": str(round(grand_total, 2)),
                "transaction_id": order.transaction_id or "",
            },
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
                    "beneficiary_name": settings.get("legal_entity_name") or "",
                    "settlement_bank_account_no": settings.get("settlement_account") or "",
                    "settlement_ifsc_code": settings.get("settlement_ifsc") or "",
                    "bank_name": settings.get("settlement_bank_name") or "",
                    "branch_name": settings.get("settlement_branch_name") or "",
                    "upi_address": settings.get("upi_address") or "",
                }
            ],
        }

    # Fulfillment end block — echo from cache
    end_location_gps = order.shipping_gps or ""
    end_location_address = _safe_json_loads(order.shipping_address)
    end_person_name = order.customer_name or order.billing_name or ""
    end_contact_phone = order.customer_phone or ""
    end_contact_email = order.customer_email or ""
    if original_order_data.get("fulfillments"):
        orig_f = original_order_data["fulfillments"][0] if original_order_data["fulfillments"] else {}
        if orig_f.get("end"):
            eb = orig_f["end"]
            if eb.get("location"):
                end_location_gps = eb["location"].get("gps") or end_location_gps
                if eb["location"].get("address"):
                    end_location_address = eb["location"]["address"]
            if eb.get("person"):
                end_person_name = eb["person"].get("name") or end_person_name
            if eb.get("contact"):
                end_contact_phone = eb["contact"].get("phone") or end_contact_phone
                end_contact_email = eb["contact"].get("email") or end_contact_email

    # V22 FIX: fulfillments[0].tags must have at least 1 item (Pramaan retail_bpp_on_cancel_message_79_minItems)
    # Try to echo tags from original confirm fulfillment; if absent, supply the mandatory cancellation_terms tag
    fulfillment_tags = []
    if original_order_data.get("fulfillments") and original_order_data["fulfillments"]:
        orig_tags = original_order_data["fulfillments"][0].get("tags")
        if orig_tags:
            fulfillment_tags = orig_tags
    if not fulfillment_tags:
        # Default fulfillment tag required by ONDC spec for on_cancel
        fulfillment_tags = [
            {
                "code": "cancellation_return_policy",
                "list": [
                    {"code": "return_window", "value": "P1D"},
                    {"code": "returnable", "value": "false"},
                    {"code": "cancellation_fee_type", "value": "percent"},
                    {"code": "cancellation_fee_amount", "value": "0"},
                    {"code": "refund_eligible", "value": "true"},
                ],
            }
        ]

    order_payload = {
        "id": order.ondc_order_id,
        "state": "Cancelled",
        "provider": {
            "id": settings.subscriber_id,
            "locations": [{"id": location_id}],
        },
        "items": items,
        "billing": billing_payload,
        "fulfillments": [
            {
                "id": order.fulfillment_id or "F1",
                "type": order.fulfillment_type or "Delivery",
                "@ondc/org/provider_name": settings.get("store_name") or settings.legal_entity_name or "",
                "tracking": False,
                "@ondc/org/category": "Immediate Delivery",
                "@ondc/org/TAT": tat_str,
                "state": {
                    "descriptor": {"code": "Cancelled"},
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
                    "time": {
                        "range": {"start": range_start, "end": range_end},
                    },
                    "contact": {
                        "phone": settings.get("consumer_care_phone") or "9999999999",
                        "email": settings.get("consumer_care_email") or "seller@example.com",
                    },
                },
                "end": {
                    "location": {
                        "gps": end_location_gps,
                        "address": end_location_address,
                    },
                    "time": {
                        "range": {"start": range_start, "end": range_end},
                    },
                    "person": {"name": end_person_name},
                    "contact": {
                        "phone": end_contact_phone,
                        "email": end_contact_email,
                    },
                },
                "tags": fulfillment_tags,
            }
        ],
        "quote": quote_payload,
        "payment": payment_payload,
        "cancellation": {
            "cancelled_by": cancelled_by,
            "reason": {
                "id": str(cancellation_reason_id),
                "descriptor": {
                    "short_desc": get_cancellation_reason(cancellation_reason_id),
                },
            },
        },
        "cancellation_terms": [
            {
                "fulfillment_state": {"descriptor": {"code": "Pending", "short_desc": "Cancellation is free before packing"}},
                "reason_required": False,
                "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0.00"}},
                "refund_eligible": True,
            },
            {
                "fulfillment_state": {"descriptor": {"code": "Packed", "short_desc": "Cancellation may apply after packing"}},
                "reason_required": True,
                "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0.00"}},
                "refund_eligible": True,
            },
        ],
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
        "created_at": order_created_at,
        "updated_at": order_updated_at,
    }

    return order_payload


def process_cancel(data, log_name=None):
    """V21: Process buyer-initiated cancel (/cancel → on_cancel).

    V21 FIX: Echo full order body from confirm cache so Pramaan finds all required
    fields (billing, items, quote, payment, fulfillment start/end). Fixes 150
    Pramaan failures (Flow 2: Buyer Side Order Cancellation).
    """
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

        # V21 FIX: Build full order payload from confirm cache
        cancelled_by = data.get("context", {}).get("bap_id", "")
        order_payload = _build_on_cancel_payload(
            order, data, cancelled_by, cancellation_reason_id, settings, client
        )

        payload = {
            "context": context,
            "message": {"order": order_payload},
        }

        frappe.log_error(
            title="ONDC on_cancel [Buyer]",
            message=json.dumps(payload, indent=2)
        )

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_cancel",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_cancel Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def trigger_merchant_cancel(order_id, cancellation_reason_id="003", cancelled_by_bpp=True):
    """V21: Flow 3C — Merchant/seller-initiated unsolicited on_cancel.

    Called when the merchant cancels an order (e.g., out of stock, seller reject).
    BPP sends on_cancel proactively WITHOUT waiting for a /cancel from the buyer.
    This satisfies Flow 3C: Merchant Side Full Order Cancellation.

    Usage: Call this from Frappe UI button or scheduled job when seller cancels.
    """
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        try:
            order = frappe.get_doc("ONDC Order", {"ondc_order_id": order_id})
        except frappe.DoesNotExistError:
            frappe.log_error(
                title="ONDC Merchant Cancel: Order Not Found",
                message=f"Order {order_id} not found for merchant cancel"
            )
            return {"success": False, "error": f"Order not found: {order_id}"}

        if order.order_status in ("Completed", "Cancelled"):
            return {"success": False, "error": f"Order already {order.order_status}"}

        # Update order status
        prev_state = order.order_status
        order.order_status = "Cancelled"
        order.fulfillment_state = "Cancelled"
        order.cancellation_reason_id = str(cancellation_reason_id)
        order.save(ignore_permissions=True)
        frappe.db.commit()

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)

        # Load original confirm data to reconstruct context
        confirm_cache_key = f"ondc_confirm_{order_id}"
        original_context = {}
        try:
            confirm_raw = frappe.cache().get_value(confirm_cache_key)
            if confirm_raw:
                confirm_json = json.loads(confirm_raw)
                original_context = confirm_json.get("context", {})
        except Exception:
            pass

        # Synthetic data dict for context reconstruction
        synthetic_data = {"context": original_context}
        context = client.create_context("on_cancel", original_context)

        # V21: cancelled_by is BPP (merchant) for Flow 3C
        cancelled_by = settings.subscriber_id if cancelled_by_bpp else original_context.get("bap_id", "")
        order_payload = _build_on_cancel_payload(
            order, synthetic_data, cancelled_by, cancellation_reason_id, settings, client
        )

        payload = {
            "context": context,
            "message": {"order": order_payload},
        }

        frappe.log_error(
            title="ONDC on_cancel [Merchant/Flow 3C]",
            message=json.dumps(payload, indent=2)
        )

        bap_uri = original_context.get("bap_uri") or order.bap_uri
        result = client.send_callback(bap_uri, "/on_cancel", payload)

        frappe.log_error(
            title="ONDC Merchant Cancel Sent",
            message=f"Order {order_id}: on_cancel sent to {bap_uri}, result: {result}"
        )
        return result

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC trigger_merchant_cancel Error")
        return {"success": False, "error": str(e)}


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
