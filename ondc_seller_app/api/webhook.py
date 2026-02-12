import frappe
import json
from frappe import _
from werkzeug.wrappers import Response
import traceback

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
    
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_confirm Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_status(data, log_name=None):
    """Process status request and send on_status callback with granular fulfillment states"""
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
        
        # Build items list
        items = []
        for item in order.items:
            items.append({
                "id": item.ondc_item_id,
                "fulfillment_id": order.fulfillment_id,
                "quantity": {"count": int(item.quantity)},
                "price": {"currency": "INR", "value": str(item.price)},
            })
        
        # Build fulfillment with granular state
        fulfillment_state = order.get("fulfillment_state") or "Pending"
        
        order_payload = {
            "id": order.ondc_order_id,
            "state": order.order_status,
            "provider": {
                "id": settings.subscriber_id,
            },
            "items": items,
            "billing": {
                "name": order.billing_name or order.customer_name,
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
            "fulfillments": [
                {
                    "id": order.fulfillment_id,
                    "type": order.fulfillment_type or "Delivery",
                    "state": {
                        "descriptor": {
                            "code": fulfillment_state,
                        }
                    },
                    "tracking": bool(order.get("tracking_url")),
                }
            ],
            "payment": {
                "type": order.payment_type or "PRE-FULFILLMENT",
                "status": "PAID" if order.payment_status == "Paid" else "NOT-PAID",
            },
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
