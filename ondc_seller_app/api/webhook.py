import frappe
import json
import time
from frappe import _
from werkzeug.wrappers import Response
import traceback
from datetime import datetime, timedelta

from ondc_seller_app.api.auth import verify_auth_header, create_auth_header
from ondc_seller_app.api.ondc_client import ONDCClient


def get_ondc_settings():
    """Get ONDC settings from Frappe."""
    try:
        settings = frappe.get_single("ONDC Settings")
        return settings
    except Exception:
        return None


def send_nack(message="Invalid request"):
    """Send NACK response."""
    response_data = {
        "message": {
            "ack": {
                "status": "NACK"
            }
        },
        "error": {
            "type": "DOMAIN-ERROR",
            "code": "20000",
            "message": message
        }
    }
    return Response(
        json.dumps(response_data),
        status=200,
        mimetype="application/json"
    )


def send_ack():
    """Send ACK response."""
    response_data = {
        "message": {
            "ack": {
                "status": "ACK"
            }
        }
    }
    return Response(
        json.dumps(response_data),
        status=200,
        mimetype="application/json"
    )


@frappe.whitelist(allow_guest=True)
def search():
    """Handle /search request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.log_error(
                title="ONDC Auth Failed",
                message=f"Auth verification failed for /search"
            )
            return send_nack("Authentication failed")

        data = json.loads(request_body)
        context = data.get("context", {})
        message = data.get("message", {})

        frappe.log_error(
            title="ONDC Search Received",
            message=f"Search from {context.get('bap_id')} for {message.get('intent', {})}"
        )

        # Process search asynchronously
        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_search",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Search Error")
        return send_nack(str(e))


def process_search(data):
    """Process search request and send on_search callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})
        message = data.get("message", {})
        intent = message.get("intent", {})

        settings = get_ondc_settings()
        if not settings:
            frappe.log_error(title="ONDC Settings Missing", message="Cannot process search")
            return

        client = ONDCClient(settings)

        # Get catalog items
        catalog = build_catalog(intent, settings)

        # Build on_search payload
        callback_context = client.create_context("on_search", context)

        payload = {
            "context": callback_context,
            "message": {
                "catalog": catalog
            }
        }

        bap_uri = context.get("bap_uri", "")
        client.send_callback(bap_uri, "/on_search", payload)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_search Error")


def build_catalog(intent, settings):
    """Build catalog from Frappe items."""
    try:
        items = frappe.get_all(
            "Item",
            filters={"disabled": 0, "is_stock_item": 1},
            fields=["name", "item_name", "item_code", "description", "standard_rate", "image"],
            limit=50
        )

        catalog_items = []
        for item in items:
            catalog_item = {
                "id": item.item_code,
                "descriptor": {
                    "name": item.item_name,
                    "short_desc": item.description or item.item_name,
                    "long_desc": item.description or item.item_name,
                },
                "price": {
                    "currency": "INR",
                    "value": str(item.standard_rate or 0),
                    "maximum_value": str(item.standard_rate or 0)
                },
                "quantity": {
                    "available": {
                        "count": "99"
                    },
                    "maximum": {
                        "count": "10"
                    }
                },
                "category_id": "Grocery",
                "fulfillment_id": "1",
                "@ondc/org/returnable": True,
                "@ondc/org/cancellable": True,
                "@ondc/org/return_window": "P7D",
                "@ondc/org/seller_pickup_return": False,
                "@ondc/org/time_to_ship": "PT45M",
                "@ondc/org/available_on_cod": False,
                "@ondc/org/contact_details_consumer_care": "support@waluelab.com,+919999999999",
                "@ondc/org/statutory_reqs_packaged_commodities": {
                    "manufacturer_or_packer_name": "WalueLab",
                    "manufacturer_or_packer_address": "Bangalore, Karnataka",
                    "common_or_generic_name_of_commodity": item.item_name,
                    "net_quantity_or_measure_of_commodity_in_pkg": "1 unit",
                    "month_year_of_manufacture_packing_import": "01/2024"
                }
            }
            catalog_items.append(catalog_item)

        return {
            "bpp/descriptor": {
                "name": settings.company or "WalueLab Store",
                "symbol": settings.logo_url if hasattr(settings, 'logo_url') else "",
                "short_desc": "Online Grocery Store",
                "long_desc": "Fresh groceries delivered to your doorstep",
                "images": []
            },
            "bpp/fulfillments": [
                {
                    "id": "1",
                    "type": "Delivery",
                    "@ondc/org/category": "Immediate Delivery"
                }
            ],
            "bpp/locations": [
                {
                    "id": "L1",
                    "time": {
                        "label": "enable",
                        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "days": "1,2,3,4,5,6,7",
                        "schedule": {
                            "holidays": []
                        },
                        "range": {
                            "start": "0800",
                            "end": "2200"
                        }
                    }
                }
            ],
            "bpp/categories": [
                {
                    "id": "Grocery",
                    "descriptor": {
                        "name": "Grocery"
                    }
                }
            ],
            "bpp/items": catalog_items,
            "bpp/offers": [],
            "exp": (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        }

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC build_catalog Error")
        return {}


@frappe.whitelist(allow_guest=True)
def select():
    """Handle /select request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            return send_nack("Authentication failed")

        data = json.loads(request_body)

        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_select",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Select Error")
        return send_nack(str(e))


def process_select(data):
    """Process select request and send on_select callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})
        message = data.get("message", {})
        order = message.get("order", {})

        settings = get_ondc_settings()
        if not settings:
            return

        client = ONDCClient(settings)

        # Build quote for selected items
        items = order.get("items", [])
        fulfillments = order.get("fulfillments", [])

        quote_items = []
        total_price = 0.0

        for item in items:
            item_id = item.get("id")
            quantity = item.get("quantity", {}).get("count", 1)

            # Get item price from Frappe
            try:
                frappe_item = frappe.get_doc("Item", item_id)
                price = float(frappe_item.standard_rate or 0) * int(quantity)
                total_price += price

                quote_items.append({
                    "id": item_id,
                    "fulfillment_id": item.get("fulfillment_id", "1"),
                    "quantity": item.get("quantity", {}),
                    "price": {
                        "currency": "INR",
                        "value": str(price)
                    }
                })
            except Exception:
                pass

        delivery_charge = 30.0
        grand_total = total_price + delivery_charge

        callback_context = client.create_context("on_select", context)

        payload = {
            "context": callback_context,
            "message": {
                "order": {
                    "provider": {
                        "id": settings.subscriber_id,
                        "locations": [{"id": "L1"}]
                    },
                    "items": quote_items,
                    "fulfillments": fulfillments,
                    "quote": {
                        "price": {
                            "currency": "INR",
                            "value": str(grand_total)
                        },
                        "breakup": [
                            {
                                "@ondc/org/item_id": item.get("id"),
                                "@ondc/org/item_quantity": item.get("quantity", {}).get("count", 1),
                                "title": "Item",
                                "@ondc/org/title_type": "item",
                                "price": {
                                    "currency": "INR",
                                    "value": str(total_price)
                                }
                            } for item in quote_items
                        ] + [
                            {
                                "@ondc/org/item_id": "delivery",
                                "title": "Delivery charges",
                                "@ondc/org/title_type": "delivery",
                                "price": {
                                    "currency": "INR",
                                    "value": str(delivery_charge)
                                }
                            }
                        ],
                        "ttl": "P1D"
                    }
                }
            }
        }

        bap_uri = context.get("bap_uri", "")
        client.send_callback(bap_uri, "/on_select", payload)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_select Error")


@frappe.whitelist(allow_guest=True)
def init():
    """Handle /init request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            return send_nack("Authentication failed")

        data = json.loads(request_body)

        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_init",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Init Error")
        return send_nack(str(e))


def process_init(data):
    """Process init request and send on_init callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})
        message = data.get("message", {})
        order = message.get("order", {})

        settings = get_ondc_settings()
        if not settings:
            return

        client = ONDCClient(settings)

        items = order.get("items", [])
        billing = order.get("billing", {})
        fulfillments = order.get("fulfillments", [])

        # Calculate totals
        total_price = 0.0
        quote_items = []

        for item in items:
            item_id = item.get("id")
            quantity = int(item.get("quantity", {}).get("count", 1))

            try:
                frappe_item = frappe.get_doc("Item", item_id)
                price = float(frappe_item.standard_rate or 0) * quantity
                total_price += price

                quote_items.append({
                    "id": item_id,
                    "fulfillment_id": item.get("fulfillment_id", "1"),
                    "quantity": item.get("quantity", {}),
                    "price": {
                        "currency": "INR",
                        "value": str(price)
                    }
                })
            except Exception:
                pass

        delivery_charge = 30.0
        grand_total = total_price + delivery_charge

        callback_context = client.create_context("on_init", context)

        payload = {
            "context": callback_context,
            "message": {
                "order": {
                    "provider": {
                        "id": settings.subscriber_id,
                        "locations": [{"id": "L1"}]
                    },
                    "items": quote_items,
                    "billing": billing,
                    "fulfillments": fulfillments,
                    "quote": {
                        "price": {
                            "currency": "INR",
                            "value": str(grand_total)
                        },
                        "breakup": [
                            {
                                "@ondc/org/item_id": item.get("id"),
                                "@ondc/org/item_quantity": item.get("quantity", {}).get("count", 1),
                                "title": "Item",
                                "@ondc/org/title_type": "item",
                                "price": {
                                    "currency": "INR",
                                    "value": str(total_price)
                                }
                            } for item in quote_items
                        ] + [
                            {
                                "@ondc/org/item_id": "delivery",
                                "title": "Delivery charges",
                                "@ondc/org/title_type": "delivery",
                                "price": {
                                    "currency": "INR",
                                    "value": str(delivery_charge)
                                }
                            }
                        ],
                        "ttl": "P1D"
                    },
                    "payment": {
                        "@ondc/org/buyer_app_finder_fee_type": "percent",
                        "@ondc/org/buyer_app_finder_fee_amount": "0",
                        "@ondc/org/settlement_details": [
                            {
                                "settlement_counterparty": "seller-app",
                                "settlement_phase": "sale-amount",
                                "settlement_type": "upi",
                                "upi_address": settings.upi_id if hasattr(settings, 'upi_id') and settings.upi_id else "waluelab@upi",
                                "settlement_bank_account_no": "",
                                "settlement_ifsc_code": "",
                                "beneficiary_name": settings.company or "WalueLab",
                                "bank_name": "",
                                "branch_name": ""
                            }
                        ]
                    }
                }
            }
        }

        bap_uri = context.get("bap_uri", "")
        client.send_callback(bap_uri, "/on_init", payload)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_init Error")


@frappe.whitelist(allow_guest=True)
def confirm():
    """Handle /confirm request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            return send_nack("Authentication failed")

        data = json.loads(request_body)

        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_confirm",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Confirm Error")
        return send_nack(str(e))


def process_confirm(data):
    """Process confirm request and send on_confirm callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})
        message = data.get("message", {})
        order_data = message.get("order", {})

        settings = get_ondc_settings()
        if not settings:
            return

        client = ONDCClient(settings)

        transaction_id = context.get("transaction_id", "")
        message_id = context.get("message_id", "")
        bap_id = context.get("bap_id", "")
        bap_uri = context.get("bap_uri", "")

        items = order_data.get("items", [])
        billing = order_data.get("billing", {})
        fulfillments = order_data.get("fulfillments", [])
        payment = order_data.get("payment", {})

        # Calculate totals
        total_price = 0.0
        quote_items = []
        quote_breakup = []

        for item in items:
            item_id = item.get("id")
            quantity = int(item.get("quantity", {}).get("count", 1))

            try:
                frappe_item = frappe.get_doc("Item", item_id)
                price = float(frappe_item.standard_rate or 0) * quantity
                total_price += price

                quote_items.append({
                    "id": item_id,
                    "fulfillment_id": item.get("fulfillment_id", "1"),
                    "quantity": item.get("quantity", {}),
                    "price": {
                        "currency": "INR",
                        "value": str(price)
                    }
                })

                quote_breakup.append({
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/item_quantity": quantity,
                    "title": frappe_item.item_name,
                    "@ondc/org/title_type": "item",
                    "price": {
                        "currency": "INR",
                        "value": str(price)
                    },
                    "item": {
                        "price": {
                            "currency": "INR",
                            "value": str(frappe_item.standard_rate or 0)
                        }
                    }
                })
            except Exception:
                pass

        delivery_charge = 30.0
        grand_total = total_price + delivery_charge

        quote_breakup.append({
            "@ondc/org/item_id": "delivery",
            "title": "Delivery charges",
            "@ondc/org/title_type": "delivery",
            "price": {
                "currency": "INR",
                "value": str(delivery_charge)
            }
        })

        # Generate ONDC order ID
        import frappe
        ondc_order_id = "ORD-" + frappe.generate_hash(length=8).upper()

        # Build fulfillment with state
        confirmed_fulfillments = []
        for f in fulfillments:
            f_copy = dict(f)
            f_copy["state"] = {
                "descriptor": {
                    "code": "Pending"
                }
            }
            confirmed_fulfillments.append(f_copy)

        callback_context = client.create_context("on_confirm", context)

        order_payload = {
            "id": ondc_order_id,
            "state": "Accepted",
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": "L1"}]
            },
            "items": quote_items,
            "billing": billing,
            "fulfillments": confirmed_fulfillments,
            "quote": {
                "price": {
                    "currency": "INR",
                    "value": str(grand_total)
                },
                "breakup": quote_breakup,
                "ttl": "P1D"
            },
            "payment": payment,
            "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        }

        payload = {
            "context": callback_context,
            "message": {
                "order": order_payload
            }
        }

        # Send on_confirm callback
        client.send_callback(bap_uri, "/on_confirm", payload)

        # Create Frappe Order document
        try:
            order_doc = frappe.new_doc("ONDC Order")
            order_doc.ondc_order_id = ondc_order_id
            order_doc.transaction_id = transaction_id
            order_doc.message_id = message_id
            order_doc.bap_id = bap_id
            order_doc.bap_uri = bap_uri
            order_doc.order_state = "Accepted"
            order_doc.fulfillment_state = "Pending"
            order_doc.total_amount = grand_total
            order_doc.order_data = json.dumps(order_data)
            order_doc.context_data = json.dumps(context)
            order_doc.insert(ignore_permissions=True)
            frappe.db.commit()

            # Cache confirm data for proactive push
            cache_key = f"ondc_confirm_{ondc_order_id}"
            cache_data = {
                "context": context,
                "order": order_data,
                "items": quote_items,
                "billing": billing,
                "fulfillments": fulfillments,
                "payment": payment,
                "quote_breakup": quote_breakup,
                "grand_total": str(grand_total),
                "bap_uri": bap_uri,
                "ondc_order_id": ondc_order_id
            }
            frappe.cache().set_value(cache_key, json.dumps(cache_data), expires_in_sec=7200)

            frappe.log_error(
                title="ONDC Order Created",
                message=f"Order {ondc_order_id} created successfully for txn {transaction_id}"
            )

            # V25: Enqueue proactive status push for Flow 3A Pramaan certification.
            # This sends all on_status states (Packed → Order-delivered) + on_update(Cancelled)
            # proactively WITHOUT waiting for /status or /update from Pramaan.
            try:
                frappe.enqueue(
                    "ondc_seller_app.api.webhook.trigger_proactive_status_push",
                    order_id=ondc_order_id,
                    original_context=context,
                    queue="short",
                    is_async=True,
                    enqueue_after_commit=True,
                )
                frappe.log_error(
                    title="ONDC Proactive Push Enqueued",
                    message=f"Order {ondc_order_id}: proactive status push enqueued for Flow 3A"
                )
            except Exception as enqueue_err:
                frappe.log_error(
                    title="ONDC Proactive Push Enqueue Failed (non-blocking)",
                    message=f"Order {ondc_order_id}: {str(enqueue_err)}"
                )

        except Exception as doc_err:
            frappe.log_error(
                title="ONDC Order Doc Create Failed",
                message=f"Order {ondc_order_id}: {str(doc_err)}"
            )

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_confirm Error")


def _auto_progress_fulfillment(order_doc):
    """Auto-advance fulfillment state for demo/testing purposes."""
    try:
        state_progression = {
            "Pending": "Packed",
            "Packed": "Agent-assigned",
            "Agent-assigned": "Order-picked-up",
            "Order-picked-up": "Out-for-delivery",
            "Out-for-delivery": "Order-delivered"
        }

        current_state = order_doc.fulfillment_state
        next_state = state_progression.get(current_state)

        if next_state:
            order_doc.fulfillment_state = next_state
            order_doc.save(ignore_permissions=True)
            frappe.db.commit()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC _auto_progress_fulfillment Error")


@frappe.whitelist(allow_guest=True)
def status():
    """Handle /status request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            return send_nack("Authentication failed")

        data = json.loads(request_body)

        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_status",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Status Error")
        return send_nack(str(e))


def process_status(data):
    """Process status request and send on_status callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})
        message = data.get("message", {})
        order_id = message.get("order_id", "")

        settings = get_ondc_settings()
        if not settings:
            return

        client = ONDCClient(settings)
        bap_uri = context.get("bap_uri", "")

        # Get order from Frappe
        try:
            order_docs = frappe.get_all(
                "ONDC Order",
                filters={"ondc_order_id": order_id},
                fields=["*"],
                limit=1
            )

            if not order_docs:
                frappe.log_error(
                    title="ONDC Order Not Found",
                    message=f"Order {order_id} not found for /status"
                )
                return

            order_doc = frappe.get_doc("ONDC Order", order_docs[0].name)

        except Exception as e:
            frappe.log_error(
                title="ONDC Order Fetch Error",
                message=f"Order {order_id}: {str(e)}"
            )
            return

        # Load cached confirm data
        cache_key = f"ondc_confirm_{order_id}"
        cached_data = {}
        try:
            cached_json = frappe.cache().get_value(cache_key)
            if cached_json:
                cached_data = json.loads(cached_json)
        except Exception:
            pass

        # Get order components
        order_data = json.loads(order_doc.order_data or "{}")
        items = cached_data.get("items") or order_data.get("items", [])
        billing = cached_data.get("billing") or order_data.get("billing", {})
        fulfillments = cached_data.get("fulfillments") or order_data.get("fulfillments", [])
        payment = cached_data.get("payment") or order_data.get("payment", {})
        quote_breakup = cached_data.get("quote_breakup") or []
        grand_total = cached_data.get("grand_total") or str(order_doc.total_amount or 0)

        # Auto-progress fulfillment state for demo
        _auto_progress_fulfillment(order_doc)
        fulfillment_state = order_doc.fulfillment_state or "Pending"
        order_state = "Completed" if fulfillment_state == "Order-delivered" else order_doc.order_state or "Accepted"

        # Build fulfillment with current state
        now_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        status_fulfillments = []
        for f in fulfillments:
            f_copy = dict(f)
            f_copy["state"] = {
                "descriptor": {
                    "code": fulfillment_state
                }
            }
            if fulfillment_state in ["Order-picked-up", "Out-for-delivery", "Order-delivered"]:
                f_copy["start"] = {
                    "time": {
                        "range": {
                            "start": now_ts,
                            "end": now_ts
                        },
                        "timestamp": now_ts
                    }
                }
            if fulfillment_state == "Order-delivered":
                f_copy["end"] = {
                    "time": {
                        "range": {
                            "start": now_ts,
                            "end": now_ts
                        },
                        "timestamp": now_ts
                    }
                }
            status_fulfillments.append(f_copy)

        if not status_fulfillments and fulfillments:
            status_fulfillments = fulfillments

        callback_context = client.create_context("on_status", context)

        payload = {
            "context": callback_context,
            "message": {
                "order": {
                    "id": order_id,
                    "state": order_state,
                    "provider": {
                        "id": settings.subscriber_id,
                        "locations": [{"id": "L1"}]
                    },
                    "items": items,
                    "billing": billing,
                    "fulfillments": status_fulfillments,
                    "quote": {
                        "price": {
                            "currency": "INR",
                            "value": grand_total
                        },
                        "breakup": quote_breakup,
                        "ttl": "P1D"
                    },
                    "payment": payment,
                    "updated_at": now_ts
                }
            }
        }

        client.send_callback(bap_uri, "/on_status", payload)

        frappe.log_error(
            title=f"ONDC on_status Sent",
            message=f"Order {order_id}: sent on_status with state {fulfillment_state}"
        )

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_status Error")


@frappe.whitelist(allow_guest=True)
def track():
    """Handle /track request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            return send_nack("Authentication failed")

        data = json.loads(request_body)

        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_track",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Track Error")
        return send_nack(str(e))


def process_track(data):
    """Process track request and send on_track callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})
        message = data.get("message", {})
        order_id = message.get("order_id", "")

        settings = get_ondc_settings()
        if not settings:
            return

        client = ONDCClient(settings)
        bap_uri = context.get("bap_uri", "")

        callback_context = client.create_context("on_track", context)

        payload = {
            "context": callback_context,
            "message": {
                "tracking": {
                    "url": f"https://ondc.waluelab.com/track/{order_id}",
                    "status": "active"
                }
            }
        }

        client.send_callback(bap_uri, "/on_track", payload)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_track Error")


@frappe.whitelist(allow_guest=True)
def cancel():
    """Handle /cancel request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            return send_nack("Authentication failed")

        data = json.loads(request_body)

        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_cancel",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Cancel Error")
        return send_nack(str(e))


def process_cancel(data):
    """Process cancel request and send on_cancel callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})
        message = data.get("message", {})
        order_id = message.get("order_id", "")
        cancellation_reason_id = message.get("cancellation_reason_id", "004")

        settings = get_ondc_settings()
        if not settings:
            return

        client = ONDCClient(settings)
        bap_uri = context.get("bap_uri", "")

        # Get order from Frappe
        try:
            order_docs = frappe.get_all(
                "ONDC Order",
                filters={"ondc_order_id": order_id},
                fields=["*"],
                limit=1
            )

            if not order_docs:
                return

            order_doc = frappe.get_doc("ONDC Order", order_docs[0].name)

        except Exception:
            return

        # Load cached data
        cache_key = f"ondc_confirm_{order_id}"
        cached_data = {}
        try:
            cached_json = frappe.cache().get_value(cache_key)
            if cached_json:
                cached_data = json.loads(cached_json)
        except Exception:
            pass

        order_data = json.loads(order_doc.order_data or "{}")
        items = cached_data.get("items") or order_data.get("items", [])
        billing = cached_data.get("billing") or order_data.get("billing", {})
        fulfillments = cached_data.get("fulfillments") or order_data.get("fulfillments", [])
        payment = cached_data.get("payment") or order_data.get("payment", {})
        quote_breakup = cached_data.get("quote_breakup") or []
        grand_total = cached_data.get("grand_total") or str(order_doc.total_amount or 0)

        # Update order state
        order_doc.order_state = "Cancelled"
        order_doc.fulfillment_state = "Cancelled"
        order_doc.save(ignore_permissions=True)
        frappe.db.commit()

        now_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build cancelled fulfillments
        cancelled_fulfillments = []
        for f in fulfillments:
            f_copy = dict(f)
            f_copy["state"] = {
                "descriptor": {
                    "code": "Cancelled"
                }
            }
            cancelled_fulfillments.append(f_copy)

        callback_context = client.create_context("on_cancel", context)

        payload = {
            "context": callback_context,
            "message": {
                "order": {
                    "id": order_id,
                    "state": "Cancelled",
                    "provider": {
                        "id": settings.subscriber_id,
                        "locations": [{"id": "L1"}]
                    },
                    "items": items,
                    "billing": billing,
                    "fulfillments": cancelled_fulfillments,
                    "quote": {
                        "price": {
                            "currency": "INR",
                            "value": grand_total
                        },
                        "breakup": quote_breakup,
                        "ttl": "P1D"
                    },
                    "payment": payment,
                    "cancellation": {
                        "cancelled_by": context.get("bap_id", ""),
                        "reason": {
                            "id": cancellation_reason_id
                        }
                    },
                    "updated_at": now_ts
                }
            }
        }

        client.send_callback(bap_uri, "/on_cancel", payload)

        frappe.log_error(
            title="ONDC on_cancel Sent",
            message=f"Order {order_id}: cancelled with reason {cancellation_reason_id}"
        )

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_cancel Error")


@frappe.whitelist(allow_guest=True)
def update():
    """Handle /update request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            return send_nack("Authentication failed")

        data = json.loads(request_body)

        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_update",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Update Error")
        return send_nack(str(e))


def process_update(data):
    """Process update request and send on_update callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})
        message = data.get("message", {})
        order_data = message.get("order", {})
        order_id = order_data.get("id", "")

        settings = get_ondc_settings()
        if not settings:
            return

        client = ONDCClient(settings)
        bap_uri = context.get("bap_uri", "")

        # Get order from Frappe
        try:
            order_docs = frappe.get_all(
                "ONDC Order",
                filters={"ondc_order_id": order_id},
                fields=["*"],
                limit=1
            )

            if not order_docs:
                return

            order_doc = frappe.get_doc("ONDC Order", order_docs[0].name)

        except Exception:
            return

        # Load cached data
        cache_key = f"ondc_confirm_{order_id}"
        cached_data = {}
        try:
            cached_json = frappe.cache().get_value(cache_key)
            if cached_json:
                cached_data = json.loads(cached_json)
        except Exception:
            pass

        stored_order_data = json.loads(order_doc.order_data or "{}")
        items = cached_data.get("items") or stored_order_data.get("items", [])
        billing = cached_data.get("billing") or stored_order_data.get("billing", {})
        fulfillments = cached_data.get("fulfillments") or stored_order_data.get("fulfillments", [])
        payment = cached_data.get("payment") or stored_order_data.get("payment", {})
        quote_breakup = cached_data.get("quote_breakup") or []
        grand_total = cached_data.get("grand_total") or str(order_doc.total_amount or 0)

        now_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        callback_context = client.create_context("on_update", context)

        payload = {
            "context": callback_context,
            "message": {
                "order": {
                    "id": order_id,
                    "state": order_doc.order_state or "Accepted",
                    "provider": {
                        "id": settings.subscriber_id,
                        "locations": [{"id": "L1"}]
                    },
                    "items": items,
                    "billing": billing,
                    "fulfillments": fulfillments,
                    "quote": {
                        "price": {
                            "currency": "INR",
                            "value": grand_total
                        },
                        "breakup": quote_breakup,
                        "ttl": "P1D"
                    },
                    "payment": payment,
                    "updated_at": now_ts
                }
            }
        }

        client.send_callback(bap_uri, "/on_update", payload)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_update Error")


@frappe.whitelist(allow_guest=True)
def rating():
    """Handle /rating request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            return send_nack("Authentication failed")

        data = json.loads(request_body)

        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_rating",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Rating Error")
        return send_nack(str(e))


def process_rating(data):
    """Process rating request and send on_rating callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})

        settings = get_ondc_settings()
        if not settings:
            return

        client = ONDCClient(settings)
        bap_uri = context.get("bap_uri", "")

        callback_context = client.create_context("on_rating", context)

        payload = {
            "context": callback_context,
            "message": {
                "feedback_form": {
                    "form": {
                        "url": f"https://ondc.waluelab.com/feedback",
                        "mime_type": "text/html"
                    },
                    "required": False
                }
            }
        }

        client.send_callback(bap_uri, "/on_rating", payload)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_rating Error")


@frappe.whitelist(allow_guest=True)
def support():
    """Handle /support request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            return send_nack("Authentication failed")

        data = json.loads(request_body)

        frappe.enqueue(
            "ondc_seller_app.api.webhook.process_support",
            data=data,
            queue="short",
            is_async=True
        )

        return send_ack()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC Support Error")
        return send_nack(str(e))


def process_support(data):
    """Process support request and send on_support callback."""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        context = data.get("context", {})

        settings = get_ondc_settings()
        if not settings:
            return

        client = ONDCClient(settings)
        bap_uri = context.get("bap_uri", "")

        callback_context = client.create_context("on_support", context)

        payload = {
            "context": callback_context,
            "message": {
                "phone": "+919999999999",
                "email": "support@waluelab.com",
                "uri": "https://ondc.waluelab.com/support"
            }
        }

        client.send_callback(bap_uri, "/on_support", payload)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_support Error")


def trigger_merchant_cancel(order_id, cancellation_reason_id="003"):
    """V24: Trigger merchant-side cancellation (Flow 3C).

    This is called by the merchant to proactively cancel an order.
    Sends on_cancel callback to BAP with merchant-side cancellation reason.
    """
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = get_ondc_settings()
        if not settings:
            frappe.log_error(
                title="ONDC Merchant Cancel Failed",
                message=f"Order {order_id}: ONDC settings not found"
            )
            return

        client = ONDCClient(settings)

        # Get order from Frappe
        order_docs = frappe.get_all(
            "ONDC Order",
            filters={"ondc_order_id": order_id},
            fields=["*"],
            limit=1
        )

        if not order_docs:
            frappe.log_error(
                title="ONDC Merchant Cancel Failed",
                message=f"Order {order_id}: not found in Frappe"
            )
            return

        order_doc = frappe.get_doc("ONDC Order", order_docs[0].name)

        # Load cached confirm data
        cache_key = f"ondc_confirm_{order_id}"
        cached_data = {}
        try:
            cached_json = frappe.cache().get_value(cache_key)
            if cached_json:
                cached_data = json.loads(cached_json)
        except Exception:
            pass

        order_data = json.loads(order_doc.order_data or "{}")
        original_context = cached_data.get("context") or json.loads(order_doc.context_data or "{}")

        items = cached_data.get("items") or order_data.get("items", [])
        billing = cached_data.get("billing") or order_data.get("billing", {})
        fulfillments = cached_data.get("fulfillments") or order_data.get("fulfillments", [])
        payment = cached_data.get("payment") or order_data.get("payment", {})
        quote_breakup = cached_data.get("quote_breakup") or []
        grand_total = cached_data.get("grand_total") or str(order_doc.total_amount or 0)
        bap_uri = cached_data.get("bap_uri") or order_doc.bap_uri or original_context.get("bap_uri", "")

        # Update order state to Cancelled
        order_doc.order_state = "Cancelled"
        order_doc.fulfillment_state = "Cancelled"
        order_doc.save(ignore_permissions=True)
        frappe.db.commit()

        now_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build cancelled fulfillments
        cancelled_fulfillments = []
        for f in fulfillments:
            f_copy = dict(f)
            f_copy["state"] = {
                "descriptor": {
                    "code": "Cancelled"
                }
            }
            f_copy["tags"] = [
                {
                    "code": "cancel_request",
                    "list": [
                        {"code": "id", "value": cancellation_reason_id},
                        {"code": "reason_desc", "value": "Merchant cancelled order"},
                        {"code": "initiated_by", "value": settings.subscriber_id}
                    ]
                }
            ]
            cancelled_fulfillments.append(f_copy)

        callback_context = client.create_context("on_cancel", original_context)

        payload = {
            "context": callback_context,
            "message": {
                "order": {
                    "id": order_id,
                    "state": "Cancelled",
                    "provider": {
                        "id": settings.subscriber_id,
                        "locations": [{"id": "L1"}]
                    },
                    "items": items,
                    "billing": billing,
                    "fulfillments": cancelled_fulfillments,
                    "quote": {
                        "price": {
                            "currency": "INR",
                            "value": grand_total
                        },
                        "breakup": quote_breakup,
                        "ttl": "P1D"
                    },
                    "payment": payment,
                    "cancellation": {
                        "cancelled_by": settings.subscriber_id,
                        "reason": {
                            "id": cancellation_reason_id
                        }
                    },
                    "updated_at": now_ts
                }
            }
        }

        client.send_callback(bap_uri, "/on_cancel", payload)

        frappe.log_error(
            title="ONDC Merchant Cancel Sent",
            message=f"Order {order_id}: on_cancel sent with reason {cancellation_reason_id}"
        )

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC trigger_merchant_cancel Error")


def trigger_proactive_status_push(order_id, original_context):
    """V25: Flow 3A — Proactively push all on_status states + on_update (Cancelled) after confirm.

    For Flow 3A (Merchant Side Partial Order Cancellation), Pramaan does NOT send
    /status or /update requests. Instead, BPP must proactively push:
      1. on_status(Packed)
      2. on_status(Agent-assigned)
      3. on_status(Order-picked-up)
      4. on_status(Out-for-delivery)
      5. on_status(Order-delivered)
      6. on_update(Cancelled) — partial cancellation with reason 003

    This function is enqueued from process_confirm as a background job.
    Each push is separated by a short delay so Pramaan can record them in order.
    """
    try:
        import time as _time
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = get_ondc_settings()
        if not settings:
            frappe.log_error(
                title="ONDC Proactive Push Failed",
                message=f"Order {order_id}: ONDC settings not found"
            )
            return

        client = ONDCClient(settings)

        # Get order from Frappe
        order_docs = frappe.get_all(
            "ONDC Order",
            filters={"ondc_order_id": order_id},
            fields=["*"],
            limit=1
        )

        if not order_docs:
            frappe.log_error(
                title="ONDC Proactive Push Failed",
                message=f"Order {order_id}: not found in Frappe"
            )
            return

        order_doc = frappe.get_doc("ONDC Order", order_docs[0].name)

        # Load cached confirm data (contains billing, items, quote, payment)
        cache_key = f"ondc_confirm_{order_id}"
        cached_data = {}
        try:
            cached_json = frappe.cache().get_value(cache_key)
            if cached_json:
                cached_data = json.loads(cached_json)
        except Exception:
            pass

        # Fall back to order doc fields if cache is empty
        order_data_raw = json.loads(order_doc.order_data or "{}")
        items = cached_data.get("items") or order_data_raw.get("items", [])
        billing = cached_data.get("billing") or order_data_raw.get("billing", {})
        fulfillments = cached_data.get("fulfillments") or order_data_raw.get("fulfillments", [])
        payment = cached_data.get("payment") or order_data_raw.get("payment", {})
        quote_breakup = cached_data.get("quote_breakup") or []
        grand_total = cached_data.get("grand_total") or str(order_doc.total_amount or 0)
        bap_uri = cached_data.get("bap_uri") or order_doc.bap_uri or original_context.get("bap_uri", "")

        def _build_status_payload(fulfillment_state):
            """Build a complete on_status order payload for the given fulfillment state."""
            now_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            order_state = "Completed" if fulfillment_state == "Order-delivered" else "Accepted"

            state_fulfillments = []
            for f in fulfillments:
                f_copy = dict(f)
                f_copy["state"] = {
                    "descriptor": {
                        "code": fulfillment_state
                    }
                }
                if fulfillment_state in ["Order-picked-up", "Out-for-delivery", "Order-delivered"]:
                    f_copy["start"] = {
                        "time": {
                            "range": {
                                "start": now_ts,
                                "end": now_ts
                            },
                            "timestamp": now_ts
                        }
                    }
                if fulfillment_state == "Order-delivered":
                    f_copy["end"] = {
                        "time": {
                            "range": {
                                "start": now_ts,
                                "end": now_ts
                            },
                            "timestamp": now_ts
                        }
                    }
                state_fulfillments.append(f_copy)

            if not state_fulfillments and fulfillments:
                state_fulfillments = list(fulfillments)

            return {
                "id": order_id,
                "state": order_state,
                "provider": {
                    "id": settings.subscriber_id,
                    "locations": [{"id": "L1"}]
                },
                "items": items,
                "billing": billing,
                "fulfillments": state_fulfillments,
                "quote": {
                    "price": {
                        "currency": "INR",
                        "value": grand_total
                    },
                    "breakup": quote_breakup,
                    "ttl": "P1D"
                },
                "payment": payment,
                "updated_at": now_ts
            }

        # Push all 5 on_status states for Flow 3A
        status_states = [
            "Packed",
            "Agent-assigned",
            "Order-picked-up",
            "Out-for-delivery",
            "Order-delivered"
        ]

        for state in status_states:
            try:
                # Update order fulfillment_state in DB
                order_doc.fulfillment_state = state
                if state == "Order-delivered":
                    order_doc.order_state = "Completed"
                order_doc.save(ignore_permissions=True)
                frappe.db.commit()

                # Build context with fresh message_id (create_context always generates new one)
                status_context = client.create_context("on_status", original_context)

                # Build full on_status payload
                status_order = _build_status_payload(state)

                status_payload = {
                    "context": status_context,
                    "message": {
                        "order": status_order
                    }
                }

                # Send callback to BAP
                client.send_callback(bap_uri, "/on_status", status_payload)

                frappe.log_error(
                    title=f"ONDC Proactive on_status [{state}]",
                    message=f"Order {order_id}: sent on_status with fulfillment state={state}"
                )

            except Exception as state_err:
                frappe.log_error(
                    title=f"ONDC Proactive on_status [{state}] Failed",
                    message=f"Order {order_id}: {str(state_err)}\n{traceback.format_exc()}"
                )

            # Delay between state pushes so Pramaan can record them in order
            _time.sleep(2)

        # After all status states, send on_update (Cancelled) for Flow 3A partial cancellation
        try:
            now_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

            # Build cancelled fulfillments with tags
            cancelled_fulfillments = []
            for f in fulfillments:
                f_copy = dict(f)
                f_copy["state"] = {
                    "descriptor": {
                        "code": "Cancelled"
                    }
                }
                f_copy["tags"] = [
                    {
                        "code": "cancel_request",
                        "list": [
                            {"code": "id", "value": "003"},
                            {"code": "reason_desc", "value": "Merchant cancelled - item unavailable"},
                            {"code": "initiated_by", "value": settings.subscriber_id}
                        ]
                    }
                ]
                cancelled_fulfillments.append(f_copy)

            if not cancelled_fulfillments and fulfillments:
                cancelled_fulfillments = list(fulfillments)

            update_context = client.create_context("on_update", original_context)

            update_payload = {
                "context": update_context,
                "message": {
                    "order": {
                        "id": order_id,
                        "state": "Cancelled",
                        "provider": {
                            "id": settings.subscriber_id,
                            "locations": [{"id": "L1"}]
                        },
                        "items": items,
                        "billing": billing,
                        "fulfillments": cancelled_fulfillments,
                        "quote": {
                            "price": {
                                "currency": "INR",
                                "value": grand_total
                            },
                            "breakup": quote_breakup,
                            "ttl": "P1D"
                        },
                        "payment": payment,
                        "cancellation": {
                            "cancelled_by": settings.subscriber_id,
                            "reason": {
                                "id": "003"
                            }
                        },
                        "updated_at": now_ts
                    }
                }
            }

            client.send_callback(bap_uri, "/on_update", update_payload)

            # Update order state to Cancelled in DB
            order_doc.order_state = "Cancelled"
            order_doc.fulfillment_state = "Cancelled"
            order_doc.save(ignore_permissions=True)
            frappe.db.commit()

            frappe.log_error(
                title="ONDC Proactive on_update [Cancelled]",
                message=f"Order {order_id}: sent on_update with Cancelled state (Flow 3A)"
            )

        except Exception as update_err:
            frappe.log_error(
                title="ONDC Proactive on_update [Cancelled] Failed",
                message=f"Order {order_id}: {str(update_err)}\n{traceback.format_exc()}"
            )

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC trigger_proactive_status_push Error")
