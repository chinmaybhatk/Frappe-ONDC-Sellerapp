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


def to_rfc3339(frappe_dt):
    """Convert Frappe datetime to RFC3339 format with Z suffix"""
    if not frappe_dt:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if isinstance(frappe_dt, str):
        # Try to parse Frappe format and convert to RFC3339
        try:
            from frappe.utils import get_datetime
            dt = get_datetime(frappe_dt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except:
            # Fallback: if already has T and Z, use as-is
            if "T" in frappe_dt and ("Z" in frappe_dt or "+" in frappe_dt):
                return frappe_dt
            # Otherwise convert space to T and add .000Z
            return str(frappe_dt).replace(" ", "T") + ".000Z" if " " in str(frappe_dt) else str(frappe_dt)
    else:
        # datetime object
        return frappe_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


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
        resp_context = {
            "domain": context.get("domain"),
            "country": context.get("country"),
            "city": context.get("city"),
            "action": context.get("action"),
            "core_version": context.get("core_version"),
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
    """Process confirm request: create ONDC Order, then send on_confirm callback"""
    try:
        order_data = data.get("message", {}).get("order", {})
        context = data.get("context", {})
        
        # Create ONDC Order
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
        order.customer_name = billing.get("name")
        order.customer_email = billing.get("email")
        order.customer_phone = billing.get("phone")
        
        # Billing address
        address = billing.get("address", {})
        order.billing_name = billing.get("name")
        order.billing_building = address.get("building")
        order.billing_locality = address.get("locality")
        order.billing_city = address.get("city")
        order.billing_state = address.get("state")
        order.billing_area_code = address.get("area_code")
        
        # Fulfillment details
        fulfillment = order_data.get("fulfillments", [{}])[0]
        order.fulfillment_id = fulfillment.get("id")
        order.fulfillment_type = fulfillment.get("type", "Delivery")
        
        end_location = fulfillment.get("end", {}).get("location", {})
        order.shipping_gps = end_location.get("gps")
        order.shipping_address = json.dumps(end_location.get("address", {}))
        
        # Items  (FIX: was using self.get_item_code_from_ondc_id - self is undefined)
        for item_data in order_data.get("items", []):
            order.append("items", {
                "ondc_item_id": item_data.get("id"),
                "item_code": get_item_code_from_ondc_id(item_data.get("id")),
                "quantity": item_data.get("quantity", {}).get("count", 1),
                "price": float(item_data.get("price", {}).get("value", 0)),
            })
        
        # Payment details
        payment = order_data.get("payment", {})
        order.payment_type = _map_payment_type(payment.get("type"))
        order.payment_status = "Paid" if payment.get("status") == "PAID" else "Pending"
        
        # Cancellation fields
        cancellation = order_data.get("cancellation", {})
        if cancellation:
            order.cancellation_reason_id = cancellation.get("reason", {}).get("id")
        
        order.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Send on_confirm callback
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        result = client.on_confirm(data)
        _update_webhook_log(log_name, status="Processed", response=result)

        # --- Unsolicited on_update for partial cancellation (Flow 3A) ---
        # After on_confirm, the seller NP must proactively send on_update
        # with partial cancellation details (reduced items).
        # Enqueue with a small delay so on_confirm is received first.
        frappe.enqueue(
            "ondc_seller_app.api.webhook.send_unsolicited_on_update",
            queue="default",
            timeout=30,
            enqueue_after_commit=True,
            data=data,
            order_name=order.name,
        )

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_confirm Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def send_unsolicited_on_update(data, order_name):
    """
    Send unsolicited on_update for merchant-side partial cancellation (Flow 3A).

    After on_confirm, the seller NP proactively sends on_update to the BAP
    indicating partial cancellation: one item's quantity is reduced (cancelled),
    while remaining items stay. Fulfillment State = "Pending", Order State = "Accepted".
    """
    import time
    time.sleep(2)  # Brief delay to ensure on_confirm is processed first

    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        from datetime import datetime

        order = frappe.get_doc("ONDC Order", order_name)
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)

        # Build context with NEW message_id (unsolicited = new message)
        req_context = data.get("context", {})
        context = client.create_context("on_update", req_context)
        # Override message_id for unsolicited call
        context["message_id"] = frappe.generate_hash(length=32)

        store_gps = settings.get("store_gps") or "0.0,0.0"
        store_name = settings.get("store_name") or settings.legal_entity_name or "ONDC Seller"
        location_id = f"LOC-{settings.city}"
        tax_rate = float(settings.get("default_tax_rate") or 0)

        # --- Partial cancellation logic ---
        # For Pramaan Flow 3A: reduce the first item's quantity by 1
        # (simulates merchant-side partial cancellation)
        items = []
        quote_breakup = []
        item_total = 0.0
        total_tax = 0.0
        cancelled_items = []

        order_items = list(order.items)
        for idx, item in enumerate(order_items):
            price = float(item.price or 0)
            original_qty = int(item.quantity or 1)

            if idx == 0 and original_qty > 1:
                # Partially cancel: reduce qty by 1
                new_qty = original_qty - 1
                cancelled_qty = 1
            elif idx == 0 and original_qty == 1 and len(order_items) > 1:
                # Fully cancel this item if there are other items
                new_qty = 0
                cancelled_qty = original_qty
            else:
                new_qty = original_qty
                cancelled_qty = 0

            # Active items
            if new_qty > 0:
                line_total = price * new_qty
                item_total += line_total
                items.append({
                    "id": item.ondc_item_id,
                    "fulfillment_id": order.fulfillment_id or "F1",
                    "quantity": {"count": new_qty},
                })
                quote_breakup.append({
                    "title": item.ondc_item_id,
                    "@ondc/org/item_id": item.ondc_item_id,
                    "@ondc/org/item_quantity": {"count": new_qty},
                    "@ondc/org/title_type": "item",
                    "price": {"currency": "INR", "value": str(line_total)},
                    "item": {"price": {"currency": "INR", "value": str(price)}},
                })
                item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
                total_tax += item_tax
                quote_breakup.append({
                    "title": "Tax",
                    "@ondc/org/item_id": item.ondc_item_id,
                    "@ondc/org/title_type": "tax",
                    "price": {"currency": "INR", "value": str(item_tax)},
                })

            # Cancelled portion
            if cancelled_qty > 0:
                cancelled_items.append({
                    "id": item.ondc_item_id,
                    "fulfillment_id": "C1",  # Cancellation fulfillment
                    "quantity": {"count": cancelled_qty},
                    "tags": [
                        {
                            "code": "update_details",
                            "list": [
                                {"code": "update_type", "value": "cancel"},
                                {"code": "reason_code", "value": "009"},
                            ],
                        },
                    ],
                })

        # Add cancelled items to the items list
        items.extend(cancelled_items)

        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        packing_charge = float(settings.get("default_packing_charge") or 0)
        quote_breakup.append({
            "title": "Packing charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "packing",
            "price": {"currency": "INR", "value": str(packing_charge)},
        })

        grand_total = item_total + total_tax + delivery_charge + packing_charge

        # Build fulfillments: active + cancellation
        fulfillment_obj = {
            "id": order.fulfillment_id or "F1",
            "type": order.fulfillment_type or "Delivery",
            "@ondc/org/provider_name": store_name,
            "@ondc/org/TAT": settings.get("default_time_to_ship") or "PT60M",
            "tracking": False,
            "state": {"descriptor": {"code": "Pending"}},
            "start": {
                "location": {
                    "id": location_id,
                    "descriptor": {"name": store_name},
                    "gps": store_gps,
                    "address": {
                        "locality": settings.get("store_locality") or "",
                        "city": settings.get("store_city_name") or settings.city,
                        "state": settings.get("store_state") or "",
                        "country": "IND",
                        "area_code": settings.get("store_area_code") or settings.city,
                    },
                },
                "contact": {
                    "phone": settings.get("consumer_care_phone") or "",
                    "email": settings.get("consumer_care_email") or "",
                },
                "time": {
                    "range": {
                        "start": datetime.utcnow().isoformat() + "Z",
                        "end": (datetime.utcnow() + __import__('datetime').timedelta(hours=1)).isoformat() + "Z",
                    },
                },
            },
            "tags": [
                {"code": "routing", "list": [{"code": "type", "value": "P2P"}]},
            ],
        }

        # End location from order
        if order.get("shipping_gps") or order.get("shipping_address"):
            end_address = {}
            if order.get("shipping_address"):
                try:
                    end_address = json.loads(order.shipping_address) if isinstance(order.shipping_address, str) else order.shipping_address
                except (json.JSONDecodeError, TypeError):
                    end_address = {}
            fulfillment_obj["end"] = {
                "location": {
                    "gps": order.get("shipping_gps") or "",
                    "address": end_address,
                },
                "person": {
                    "name": order.customer_name or "",
                },
                "contact": {
                    "phone": order.customer_phone or "",
                    "email": order.customer_email or "",
                },
            }

        fulfillments = [fulfillment_obj]

        # Cancellation fulfillment for cancelled items
        if cancelled_items:
            fulfillments.append({
                "id": "C1",
                "type": "Cancel",
                "state": {"descriptor": {"code": "Cancelled"}},
                "tags": [
                    {
                        "code": "cancel_request",
                        "list": [
                            {"code": "reason_id", "value": "009"},
                            {"code": "initiated_by", "value": settings.subscriber_id},
                        ],
                    },
                ],
            })

        # Payment
        payment_obj = {
            "type": order.payment_type or "ON-ORDER",
            "collected_by": "BAP" if (order.payment_type or "ON-ORDER") != "ON-FULFILLMENT" else "BPP",
            "status": "PAID" if order.payment_status == "Paid" else "NOT-PAID",
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
                    "beneficiary_name": settings.legal_entity_name or "",
                    "settlement_bank_account_no": settings.get("settlement_bank_account") or "",
                    "settlement_ifsc_code": settings.get("settlement_ifsc_code") or "",
                    "bank_name": settings.get("settlement_bank_name") or "",
                    "branch_name": settings.get("settlement_branch_name") or "",
                }
            ],
        }

        order_payload = {
            "id": order.ondc_order_id,
            "state": "Accepted",
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items,
            "billing": {
                "name": order.billing_name or order.customer_name or "",
                "address": {
                    "building": order.billing_building or "",
                    "locality": order.billing_locality or "",
                    "city": order.billing_city or "",
                    "state": order.billing_state or "",
                    "country": "IND",
                    "area_code": order.billing_area_code or "",
                },
                "email": order.customer_email or "",
                "phone": order.customer_phone or "",
                "tax_number": order.get("billing_tax_number") or "",
                "created_at": to_rfc3339(order.creation),
                "updated_at": to_rfc3339(order.modified),
            },
            "fulfillments": fulfillments,
            "quote": {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "P1D",
            },
            "payment": payment_obj,
            "created_at": to_rfc3339(order.creation),
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }

        payload = {"context": context, "message": {"order": order_payload}}

        result = client.send_callback(
            req_context.get("bap_uri"),
            "/on_update",
            payload,
        )

        frappe.log_error(
            title="ONDC unsolicited on_update sent",
            message=f"Order: {order.ondc_order_id}, Result: {json.dumps(result, default=str)[:2000]}"
        )

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC unsolicited on_update Error")


def process_status(data, log_name=None):
    """Process status request and send on_status callback with ONDC-compliant structure"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        from datetime import datetime

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

        # Auto-progress fulfillment state on each status call
        # Pramaan expects fulfillment to advance: Pending→Packed→Agent-assigned→Order-picked-up→Out-for-delivery→Order-delivered
        state_progression = [
            "Pending",
            "Packed",
            "Agent-assigned",
            "Order-picked-up",
            "Out-for-delivery",
            "Order-delivered",
        ]
        current_state = order.get("fulfillment_state") or "Pending"
        if current_state in state_progression:
            current_idx = state_progression.index(current_state)
            if current_idx < len(state_progression) - 1:
                next_state = state_progression[current_idx + 1]
                order.fulfillment_state = next_state
                order.flags.ignore_validate = True
                order.save(ignore_permissions=True)
                frappe.db.commit()
                frappe.log_error(
                    f"Auto-progressed fulfillment state: {current_state} → {next_state}",
                    "ONDC Status Auto-Progress"
                )

        fulfillment_state = order.get("fulfillment_state") or "Pending"

        # Map fulfillment state to order state
        state_map = {
            "Pending": "Accepted",
            "Packed": "In-progress",
            "Agent-assigned": "In-progress",
            "At-pickup": "In-progress",
            "Order-picked-up": "In-progress",
            "Out-for-delivery": "In-progress",
            "Order-delivered": "Completed",
            "Cancelled": "Cancelled",
        }
        order_state = state_map.get(fulfillment_state, order.order_status or "Accepted")

        # Build items with per-item tax
        items = []
        quote_breakup = []
        item_total = 0.0
        tax_rate = float(settings.get("default_tax_rate") or 0)
        total_tax = 0.0

        for item in order.items:
            price = float(item.price or 0)
            qty = int(item.quantity or 1)
            line_total = price * qty
            item_total += line_total

            items.append({
                "id": item.ondc_item_id,
                "fulfillment_id": order.fulfillment_id or "F1",
                "quantity": {"count": qty},
            })

            quote_breakup.append({
                "title": item.ondc_item_id,
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/item_quantity": {"count": qty},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
                "item": {"price": {"currency": "INR", "value": str(price)}},
            })

            # Per-item tax
            item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
            total_tax += item_tax
            quote_breakup.append({
                "title": "Tax",
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/title_type": "tax",
                "price": {"currency": "INR", "value": str(item_tax)},
            })

        # Delivery charges
        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        # Packing charges (always include)
        packing_charge = float(settings.get("default_packing_charge") or 0)
        quote_breakup.append({
            "title": "Packing charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "packing",
            "price": {"currency": "INR", "value": str(packing_charge)},
        })

        # Convenience fee
        convenience_fee = float(settings.get("convenience_fee") or 0)
        quote_breakup.append({
            "title": "Convenience Fee",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "misc",
            "price": {"currency": "INR", "value": str(convenience_fee)},
        })

        grand_total = item_total + total_tax + delivery_charge + packing_charge + convenience_fee

        # Store location
        store_gps = settings.get("store_gps") or "0.0,0.0"
        store_name = settings.get("store_name") or settings.legal_entity_name or "ONDC Seller"
        location_id = f"LOC-{settings.city}"

        # Build fulfillment
        fulfillment_obj = {
            "id": order.fulfillment_id or "F1",
            "type": order.fulfillment_type or "Delivery",
            "@ondc/org/provider_name": store_name,
            "@ondc/org/TAT": settings.get("default_time_to_ship") or "PT60M",
            "tracking": bool(order.get("tracking_url")),
            "state": {"descriptor": {"code": fulfillment_state}},
            "start": {
                "location": {
                    "id": location_id,
                    "descriptor": {"name": store_name},
                    "gps": store_gps,
                    "address": {
                        "locality": settings.get("store_locality") or "",
                        "city": settings.get("store_city_name") or settings.city,
                        "state": settings.get("store_state") or "",
                        "country": "IND",
                        "area_code": settings.get("store_area_code") or settings.city,
                    },
                },
                "contact": {
                    "phone": settings.get("consumer_care_phone") or "",
                    "email": settings.get("consumer_care_email") or "",
                },
                "time": {
                    "range": {
                        "start": datetime.utcnow().isoformat() + "Z",
                        "end": (datetime.utcnow() + __import__('datetime').timedelta(hours=1)).isoformat() + "Z",
                    },
                },
            },
            "tags": [
                {
                    "code": "routing",
                    "list": [{"code": "type", "value": "P2P"}],
                },
            ],
        }

        # End location from order
        if order.get("shipping_gps") or order.get("shipping_address"):
            end_address = {}
            if order.get("shipping_address"):
                try:
                    end_address = json.loads(order.shipping_address) if isinstance(order.shipping_address, str) else order.shipping_address
                except (json.JSONDecodeError, TypeError):
                    end_address = {}
            fulfillment_obj["end"] = {
                "location": {
                    "gps": order.get("shipping_gps") or "",
                    "address": end_address,
                },
                "person": {
                    "name": order.customer_name or "",
                },
                "contact": {
                    "phone": order.customer_phone or "",
                    "email": order.customer_email or "",
                },
                "time": {
                    "range": {
                        "start": datetime.utcnow().isoformat() + "Z",
                        "end": (datetime.utcnow() + __import__('datetime').timedelta(hours=2)).isoformat() + "Z",
                    },
                },
            }

        # Agent details - required for Agent-assigned and later states
        agent_states = ["Agent-assigned", "At-pickup", "Order-picked-up", "Out-for-delivery", "Order-delivered"]
        if order.get("delivery_agent_name"):
            fulfillment_obj["agent"] = {
                "name": order.delivery_agent_name,
                "phone": order.get("delivery_agent_phone") or "",
            }
        elif fulfillment_state in agent_states:
            # Provide default agent for Pramaan compliance
            fulfillment_obj["agent"] = {
                "name": settings.get("store_name") or "Delivery Agent",
                "phone": settings.get("consumer_care_phone") or "9876543210",
            }

        # Documents (invoice for post-pickup states)
        post_pickup_states = ["Order-picked-up", "Out-for-delivery", "Order-delivered"]
        if fulfillment_state in post_pickup_states:
            fulfillment_obj["documents"] = [
                {
                    "url": order.get("invoice_url") or f"https://{settings.subscriber_id}/invoice/{order.ondc_order_id}",
                    "label": "Invoice",
                },
            ]

        # Tracking URL
        if order.get("tracking_url"):
            fulfillment_obj["tracking"] = True
            fulfillment_obj["@ondc/org/tracking_url"] = order.tracking_url

        # Payment with params and settlement details
        payment_obj = {
            "type": order.payment_type or "ON-ORDER",
            "collected_by": "BAP" if (order.payment_type or "ON-ORDER") != "ON-FULFILLMENT" else "BPP",
            "status": "PAID" if order.payment_status == "Paid" else "NOT-PAID",
            "params": {
                "currency": "INR",
                "amount": str(round(grand_total, 2)),
                "transaction_id": order.get("payment_transaction_id") or order.ondc_order_id,
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
                    "beneficiary_name": settings.legal_entity_name or "",
                    "settlement_bank_account_no": settings.get("settlement_bank_account") or "",
                    "settlement_ifsc_code": settings.get("settlement_ifsc_code") or "",
                    "bank_name": settings.get("settlement_bank_name") or "",
                    "branch_name": settings.get("settlement_branch_name") or "",
                }
            ],
        }

        order_payload = {
            "id": order.ondc_order_id,
            "state": order_state,
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items,
            "billing": {
                "name": order.billing_name or order.customer_name or "",
                "address": {
                    "building": order.billing_building or "",
                    "locality": order.billing_locality or "",
                    "city": order.billing_city or "",
                    "state": order.billing_state or "",
                    "country": "IND",
                    "area_code": order.billing_area_code or "",
                },
                "email": order.customer_email or "",
                "phone": order.customer_phone or "",
                "tax_number": order.get("billing_tax_number") or "",
                "created_at": to_rfc3339(order.creation),
                "updated_at": to_rfc3339(order.modified),
            },
            "fulfillments": [fulfillment_obj],
            "quote": {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "P1D",
            },
            "payment": payment_obj,
            "created_at": to_rfc3339(order.creation),
            "updated_at": to_rfc3339(order.modified),
        }

        context = client.create_context("on_status", data.get("context"))
        payload = {
            "context": context,
            "message": {"order": order_payload},
        }

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
    """Process track request with proper trackable states and location"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        from datetime import datetime

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

        fulfillment_state = order.get("fulfillment_state") or "Pending"
        trackable_states = ["Agent-assigned", "At-pickup", "Order-picked-up", "Out-for-delivery"]

        is_active = fulfillment_state in trackable_states

        tracking_data = {
            "status": "active" if is_active else "inactive",
        }

        if order.get("tracking_url"):
            tracking_data["url"] = order.tracking_url

        # Add location for active tracking
        if is_active:
            tracking_data["location"] = {
                "gps": order.get("shipping_gps") or settings.get("store_gps") or "0.0,0.0",
                "time": {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            }

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
    """Process cancel request with ONDC-compliant cancellation structure"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        from datetime import datetime

        message = data.get("message", {})
        order_id = message.get("order_id")
        cancellation_reason_id = str(message.get("cancellation_reason_id", ""))

        if not order_id:
            _update_webhook_log(log_name, status="Failed", error_message="Missing order_id")
            return

        try:
            order = frappe.get_doc("ONDC Order", {"ondc_order_id": order_id})
        except frappe.DoesNotExistError:
            _update_webhook_log(log_name, status="Failed", error_message=f"Order not found: {order_id}")
            return

        if order.order_status in ("Completed", "Cancelled"):
            _update_webhook_log(log_name, status="Failed", error_message=f"Order cannot be cancelled (status: {order.order_status})")
            return

        # Store pre-cancel state
        precancel_state = order.get("fulfillment_state") or "Pending"

        # Determine who cancelled
        # Reason codes 001-005 are buyer-initiated, 006+ are seller-initiated
        bap_id = data.get("context", {}).get("bap_id", "")
        try:
            reason_num = int(cancellation_reason_id)
            cancelled_by = bap_id if reason_num <= 5 else (order.get("bpp_id") or frappe.db.get_single_value("ONDC Settings", "subscriber_id"))
        except (ValueError, TypeError):
            cancelled_by = bap_id

        # Update order
        order.order_status = "Cancelled"
        order.fulfillment_state = "Cancelled"
        order.cancellation_reason_id = cancellation_reason_id
        order.save(ignore_permissions=True)
        frappe.db.commit()

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_cancel", data.get("context"))

        store_gps = settings.get("store_gps") or "0.0,0.0"
        store_name = settings.get("store_name") or settings.legal_entity_name or "ONDC Seller"
        location_id = f"LOC-{settings.city}"

        # Build items
        items = []
        quote_breakup = []
        item_total = 0.0
        tax_rate = float(settings.get("default_tax_rate") or 0)
        total_tax = 0.0

        for item in order.items:
            price = float(item.price or 0)
            qty = int(item.quantity or 1)
            line_total = price * qty
            item_total += line_total

            items.append({
                "id": item.ondc_item_id,
                "fulfillment_id": order.fulfillment_id or "F1",
                "quantity": {"count": qty},
            })

            quote_breakup.append({
                "title": item.ondc_item_id,
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/item_quantity": {"count": qty},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
                "item": {"price": {"currency": "INR", "value": str(price)}},
            })

            item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
            total_tax += item_tax
            quote_breakup.append({
                "title": "Tax",
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/title_type": "tax",
                "price": {"currency": "INR", "value": str(item_tax)},
            })

        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        packing_charge = float(settings.get("default_packing_charge") or 0)
        quote_breakup.append({
            "title": "Packing charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "packing",
            "price": {"currency": "INR", "value": str(packing_charge)},
        })

        grand_total = item_total + total_tax + delivery_charge + packing_charge

        # Build cancellation response
        order_payload = {
            "id": order.ondc_order_id,
            "state": "Cancelled",
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items,
            "billing": {
                "name": order.billing_name or order.customer_name or "",
                "address": {
                    "building": order.billing_building or "",
                    "locality": order.billing_locality or "",
                    "city": order.billing_city or "",
                    "state": order.billing_state or "",
                    "country": "IND",
                    "area_code": order.billing_area_code or "",
                },
                "email": order.customer_email or "",
                "phone": order.customer_phone or "",
            },
            "cancellation": {
                "cancelled_by": cancelled_by,
                "reason": {
                    "id": cancellation_reason_id,
                },
            },
            "quote": {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "P1D",
            },
            "fulfillments": [
                {
                    "id": order.fulfillment_id or "F1",
                    "type": order.fulfillment_type or "Delivery",
                    "state": {"descriptor": {"code": "Cancelled"}},
                    "start": {
                        "location": {
                            "id": location_id,
                            "descriptor": {"name": store_name},
                            "gps": store_gps,
                        },
                    },
                    "tags": [
                        {
                            "code": "cancel_request",
                            "list": [
                                {"code": "reason_id", "value": cancellation_reason_id},
                                {"code": "initiated_by", "value": cancelled_by},
                            ],
                        },
                        {
                            "code": "precancel_state",
                            "list": [
                                {"code": "fulfillment_state", "value": precancel_state},
                                {"code": "updated_at", "value": datetime.utcnow().isoformat() + "Z"},
                            ],
                        },
                    ],
                }
            ],
            "payment": {
                "type": order.payment_type or "ON-ORDER",
                "collected_by": "BAP" if (order.payment_type or "ON-ORDER") != "ON-FULFILLMENT" else "BPP",
                "status": "NOT-PAID",
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
                        "beneficiary_name": settings.legal_entity_name or "",
                        "settlement_bank_account_no": settings.get("settlement_bank_account") or "",
                        "settlement_ifsc_code": settings.get("settlement_ifsc_code") or "",
                        "bank_name": settings.get("settlement_bank_name") or "",
                        "branch_name": settings.get("settlement_branch_name") or "",
                    },
                    {
                        "settlement_counterparty": "buyer-app",
                        "settlement_phase": "refund",
                        "settlement_type": "neft",
                        "settlement_amount": str(round(grand_total, 2)),
                    },
                ],
            },
            "cancellation_terms": [
                {
                    "fulfillment_state": {"descriptor": {"code": "Pending"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0"}},
                    "reason_required": False,
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Packed"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0"}},
                    "reason_required": True,
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Order-picked-up"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0"}},
                    "reason_required": True,
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Out-for-delivery"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0"}},
                    "reason_required": True,
                },
            ],
            "tags": [
                {
                    "code": "bpp_terms",
                    "list": [
                        {"code": "np_type", "value": "MSN"},
                        {"code": "accept_bap_terms", "value": "Y"},
                        {"code": "collect_payment", "value": "Y"},
                        {"code": "max_liability", "value": "2"},
                        {"code": "max_liability_cap", "value": "10000"},
                        {"code": "mandatory_arbitration", "value": "false"},
                        {"code": "court_jurisdiction", "value": "Bengaluru"},
                        {"code": "delay_interest", "value": "1000"},
                    ],
                },
            ],
            "created_at": str(order.creation) if order.creation else "",
            "updated_at": datetime.utcnow().isoformat() + "Z",
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
    """Process update request with ONDC-compliant response structure"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        from datetime import datetime

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

        # Handle fulfillment update
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

        elif update_target == "item":
            for item_update in order_data.get("items", []):
                for order_item in order.items:
                    if order_item.ondc_item_id == item_update.get("id"):
                        new_qty = item_update.get("quantity", {}).get("count")
                        if new_qty is not None:
                            order_item.quantity = int(new_qty)
            order.save(ignore_permissions=True)
            frappe.db.commit()

        fulfillment_state = order.get("fulfillment_state") or "Pending"
        state_map = {
            "Pending": "Accepted",
            "Packed": "In-progress",
            "Agent-assigned": "In-progress",
            "At-pickup": "In-progress",
            "Order-picked-up": "In-progress",
            "Out-for-delivery": "In-progress",
            "Order-delivered": "Completed",
            "Cancelled": "Cancelled",
        }
        order_state = state_map.get(fulfillment_state, order.order_status or "Accepted")

        # Build items with per-item tax
        items = []
        quote_breakup = []
        item_total = 0.0
        tax_rate = float(settings.get("default_tax_rate") or 0)
        total_tax = 0.0

        for item in order.items:
            price = float(item.price or 0)
            qty = int(item.quantity or 1)
            line_total = price * qty
            item_total += line_total

            items.append({
                "id": item.ondc_item_id,
                "fulfillment_id": order.fulfillment_id or "F1",
                "quantity": {"count": qty},
            })

            quote_breakup.append({
                "title": item.ondc_item_id,
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/item_quantity": {"count": qty},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
                "item": {"price": {"currency": "INR", "value": str(price)}},
            })

            item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
            total_tax += item_tax
            quote_breakup.append({
                "title": "Tax",
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/title_type": "tax",
                "price": {"currency": "INR", "value": str(item_tax)},
            })

        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        packing_charge = float(settings.get("default_packing_charge") or 0)
        quote_breakup.append({
            "title": "Packing charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "packing",
            "price": {"currency": "INR", "value": str(packing_charge)},
        })

        grand_total = item_total + total_tax + delivery_charge + packing_charge

        store_gps = settings.get("store_gps") or "0.0,0.0"
        store_name = settings.get("store_name") or settings.legal_entity_name or "ONDC Seller"
        location_id = f"LOC-{settings.city}"

        # Build fulfillment
        fulfillment_obj = {
            "id": order.fulfillment_id or "F1",
            "type": order.fulfillment_type or "Delivery",
            "@ondc/org/provider_name": store_name,
            "@ondc/org/TAT": settings.get("default_time_to_ship") or "PT60M",
            "tracking": bool(order.get("tracking_url")),
            "state": {"descriptor": {"code": fulfillment_state}},
            "start": {
                "location": {
                    "id": location_id,
                    "descriptor": {"name": store_name},
                    "gps": store_gps,
                    "address": {
                        "locality": settings.get("store_locality") or "",
                        "city": settings.get("store_city_name") or settings.city,
                        "state": settings.get("store_state") or "",
                        "country": "IND",
                        "area_code": settings.get("store_area_code") or settings.city,
                    },
                },
                "contact": {
                    "phone": settings.get("consumer_care_phone") or "",
                    "email": settings.get("consumer_care_email") or "",
                },
            },
            "tags": [
                {
                    "code": "routing",
                    "list": [{"code": "type", "value": "P2P"}],
                },
            ],
        }

        # End location
        if order.get("shipping_gps") or order.get("shipping_address"):
            end_address = {}
            if order.get("shipping_address"):
                try:
                    end_address = json.loads(order.shipping_address) if isinstance(order.shipping_address, str) else order.shipping_address
                except (json.JSONDecodeError, TypeError):
                    end_address = {}
            fulfillment_obj["end"] = {
                "location": {
                    "gps": order.get("shipping_gps") or "",
                    "address": end_address,
                },
                "contact": {
                    "phone": order.customer_phone or "",
                },
            }

        # Payment
        payment_obj = {
            "type": order.payment_type or "ON-ORDER",
            "collected_by": "BAP" if (order.payment_type or "ON-ORDER") != "ON-FULFILLMENT" else "BPP",
            "status": "PAID" if order.payment_status == "Paid" else "NOT-PAID",
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
                    "beneficiary_name": settings.legal_entity_name or "",
                    "settlement_bank_account_no": settings.get("settlement_bank_account") or "",
                    "settlement_ifsc_code": settings.get("settlement_ifsc_code") or "",
                    "bank_name": settings.get("settlement_bank_name") or "",
                    "branch_name": settings.get("settlement_branch_name") or "",
                }
            ],
        }

        order_payload = {
            "id": order.ondc_order_id,
            "state": order_state,
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items,
            "billing": {
                "name": order.billing_name or order.customer_name or "",
                "address": {
                    "building": order.billing_building or "",
                    "locality": order.billing_locality or "",
                    "city": order.billing_city or "",
                    "state": order.billing_state or "",
                    "country": "IND",
                    "area_code": order.billing_area_code or "",
                },
                "email": order.customer_email or "",
                "phone": order.customer_phone or "",
            },
            "fulfillments": [fulfillment_obj],
            "quote": {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "P1D",
            },
            "payment": payment_obj,
            "created_at": str(order.creation) if order.creation else "",
            "updated_at": datetime.utcnow().isoformat() + "Z",
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
