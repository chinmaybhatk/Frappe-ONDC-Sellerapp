"""
ONDC IGM (Issue & Grievance Management) Adapter
Bridges Frappe Helpdesk with ONDC IGM Protocol APIs

ONDC IGM APIs:
- /issue (inbound) - Buyer raises issue
- /on_issue (outbound) - Seller acknowledges issue
- /issue_status (inbound) - Check issue status
- /on_issue_status (outbound) - Return issue resolution status
"""

import frappe
from frappe import _
from datetime import datetime
import json

from ondc_seller_app.api.ondc_client import ONDCClient
from ondc_seller_app.api.ondc_errors import build_ack_response, build_nack_response


# ONDC Issue Categories
ISSUE_CATEGORIES = {
    "ITEM": "Item related issue",
    "FULFILLMENT": "Fulfillment related issue",
    "AGENT": "Agent related issue",
    "PAYMENT": "Payment related issue",
    "ORDER": "Order related issue",
}

# ONDC Issue Sub-Categories
ISSUE_SUB_CATEGORIES = {
    "ITM01": "Missing items",
    "ITM02": "Quantity issue",
    "ITM03": "Quality issue",
    "ITM04": "Wrong item delivered",
    "ITM05": "Damaged item",
    "FLM01": "Delivery delayed",
    "FLM02": "Order not received",
    "FLM03": "Wrong delivery address",
    "FLM04": "Packaging issue",
    "PMT01": "Refund not received",
    "PMT02": "Double charged",
    "PMT03": "Payment failed but order placed",
}

# Issue Status Mapping: Helpdesk -> ONDC
HD_TO_ONDC_STATUS = {
    "Open": "OPEN",
    "Replied": "PROCESSING",
    "Resolved": "RESOLVED",
    "Closed": "CLOSED",
}

# Issue Status Mapping: ONDC -> Helpdesk
ONDC_TO_HD_STATUS = {
    "OPEN": "Open",
    "PROCESSING": "Replied",
    "RESOLVED": "Resolved",
    "CLOSED": "Closed",
}


def handle_issue(data):
    """
    Handle incoming /issue request from BAP.
    Creates a ticket in Frappe Helpdesk.
    """
    try:
        context = data.get("context", {})
        message = data.get("message", {})
        issue_data = message.get("issue", {})

        # Extract issue details
        issue_id = issue_data.get("id")
        category = issue_data.get("category")
        sub_category = issue_data.get("sub_category")
        issue_type = issue_data.get("issue_type")

        # Complainant details
        complainant = issue_data.get("complainant_info", {})
        complainant_name = complainant.get("person", {}).get("name", "Unknown")
        complainant_phone = complainant.get("contact", {}).get("phone")
        complainant_email = complainant.get("contact", {}).get("email")

        # Order reference
        order_details = issue_data.get("order_details", {})
        order_id = order_details.get("id")
        provider_id = order_details.get("provider_id")

        # Issue description
        description = issue_data.get("description", {})
        short_desc = description.get("short_desc", "")
        long_desc = description.get("long_desc", "")
        images = description.get("images", [])

        # Expected resolution
        resolution = issue_data.get("resolution", {})
        expected_action = resolution.get("action_triggered")

        # Create Helpdesk Ticket
        ticket = create_helpdesk_ticket(
            issue_id=issue_id,
            category=category,
            sub_category=sub_category,
            complainant_name=complainant_name,
            complainant_email=complainant_email,
            complainant_phone=complainant_phone,
            order_id=order_id,
            short_desc=short_desc,
            long_desc=long_desc,
            images=images,
            expected_action=expected_action,
            context=context,
        )

        # Send on_issue callback
        send_on_issue(context, issue_data, ticket)

        return build_ack_response()

    except Exception as e:
        frappe.log_error(f"IGM Issue Error: {str(e)}", "ONDC IGM")
        return build_nack_response("30000", str(e))


def handle_issue_status(data):
    """
    Handle incoming /issue_status request from BAP.
    Returns current status of the issue from Helpdesk.
    """
    try:
        context = data.get("context", {})
        message = data.get("message", {})
        issue_id = message.get("issue_id")

        if not issue_id:
            return build_nack_response("20000", "Missing issue_id")

        # Find the Helpdesk ticket
        ticket = get_ticket_by_ondc_issue_id(issue_id)
        if not ticket:
            return build_nack_response("30010", f"Issue not found: {issue_id}")

        # Send on_issue_status callback
        send_on_issue_status(context, issue_id, ticket)

        return build_ack_response()

    except Exception as e:
        frappe.log_error(f"IGM Issue Status Error: {str(e)}", "ONDC IGM")
        return build_nack_response("30000", str(e))


def create_helpdesk_ticket(
    issue_id,
    category,
    sub_category,
    complainant_name,
    complainant_email,
    complainant_phone,
    order_id,
    short_desc,
    long_desc,
    images,
    expected_action,
    context,
):
    """
    Create a Frappe Helpdesk ticket from ONDC issue.
    Falls back to ERPNext Issue DocType if Helpdesk is not installed.
    """
    # Check if Frappe Helpdesk is installed
    if frappe.db.exists("DocType", "HD Ticket"):
        return create_hd_ticket(
            issue_id, category, sub_category, complainant_name, complainant_email,
            complainant_phone, order_id, short_desc, long_desc, images,
            expected_action, context
        )
    elif frappe.db.exists("DocType", "Issue"):
        return create_erpnext_issue(
            issue_id, category, sub_category, complainant_name, complainant_email,
            complainant_phone, order_id, short_desc, long_desc, images,
            expected_action, context
        )
    else:
        # Create a simple ONDC Issue DocType record
        return create_ondc_issue_record(
            issue_id, category, sub_category, complainant_name, complainant_email,
            complainant_phone, order_id, short_desc, long_desc, images,
            expected_action, context
        )


def create_hd_ticket(
    issue_id, category, sub_category, complainant_name, complainant_email,
    complainant_phone, order_id, short_desc, long_desc, images,
    expected_action, context
):
    """Create Frappe Helpdesk HD Ticket"""
    # Get or create contact
    contact = get_or_create_hd_contact(complainant_name, complainant_email, complainant_phone)

    # Build description with ONDC details
    full_description = f"""
**ONDC Issue ID:** {issue_id}
**Order ID:** {order_id}
**Category:** {category} - {ISSUE_CATEGORIES.get(category, '')}
**Sub-Category:** {sub_category} - {ISSUE_SUB_CATEGORIES.get(sub_category, '')}
**Expected Action:** {expected_action}

---

{long_desc or short_desc}
"""

    ticket = frappe.new_doc("HD Ticket")
    ticket.subject = short_desc or f"ONDC Issue: {issue_id}"
    ticket.description = full_description
    ticket.contact = contact
    ticket.raised_by = complainant_email

    # Custom fields for ONDC
    if hasattr(ticket, "custom_ondc_issue_id"):
        ticket.custom_ondc_issue_id = issue_id
    if hasattr(ticket, "custom_ondc_order_id"):
        ticket.custom_ondc_order_id = order_id
    if hasattr(ticket, "custom_ondc_category"):
        ticket.custom_ondc_category = category
    if hasattr(ticket, "custom_ondc_sub_category"):
        ticket.custom_ondc_sub_category = sub_category
    if hasattr(ticket, "custom_bap_id"):
        ticket.custom_bap_id = context.get("bap_id")
    if hasattr(ticket, "custom_transaction_id"):
        ticket.custom_transaction_id = context.get("transaction_id")

    ticket.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.log_error(f"Created HD Ticket {ticket.name} for ONDC Issue {issue_id}", "ONDC IGM")
    return ticket


def create_erpnext_issue(
    issue_id, category, sub_category, complainant_name, complainant_email,
    complainant_phone, order_id, short_desc, long_desc, images,
    expected_action, context
):
    """Create ERPNext Issue DocType (fallback)"""
    # Get or create customer
    customer = get_or_create_customer(complainant_name, complainant_email, complainant_phone)

    full_description = f"""
**ONDC Issue ID:** {issue_id}
**Order ID:** {order_id}
**Category:** {category}
**Sub-Category:** {sub_category}
**Expected Action:** {expected_action}

---

{long_desc or short_desc}
"""

    issue = frappe.new_doc("Issue")
    issue.subject = short_desc or f"ONDC Issue: {issue_id}"
    issue.description = full_description
    issue.raised_by = complainant_email
    issue.customer = customer

    # Custom fields for ONDC if they exist
    if hasattr(issue, "custom_ondc_issue_id"):
        issue.custom_ondc_issue_id = issue_id
    if hasattr(issue, "custom_ondc_order_id"):
        issue.custom_ondc_order_id = order_id

    issue.insert(ignore_permissions=True)
    frappe.db.commit()

    return issue


def create_ondc_issue_record(
    issue_id, category, sub_category, complainant_name, complainant_email,
    complainant_phone, order_id, short_desc, long_desc, images,
    expected_action, context
):
    """
    Create ONDC Issue record if neither Helpdesk nor ERPNext Issue is available.
    This is a minimal fallback - the ONDC Issue DocType should be created.
    """
    # For now, store in ONDC Webhook Log with issue details
    log = frappe.new_doc("ONDC Webhook Log")
    log.webhook_type = "issue"
    log.transaction_id = context.get("transaction_id")
    log.message_id = context.get("message_id")
    log.request_body = json.dumps({
        "issue_id": issue_id,
        "category": category,
        "sub_category": sub_category,
        "order_id": order_id,
        "complainant_name": complainant_name,
        "complainant_email": complainant_email,
        "description": long_desc or short_desc,
    }, indent=2)
    log.status = "Received"
    log.insert(ignore_permissions=True)
    frappe.db.commit()

    return log


def get_or_create_hd_contact(name, email, phone):
    """Get or create Helpdesk contact"""
    if email and frappe.db.exists("HD Contact", {"email_id": email}):
        return frappe.db.get_value("HD Contact", {"email_id": email}, "name")

    contact = frappe.new_doc("HD Contact")
    contact.first_name = name
    contact.email_id = email
    contact.phone = phone
    contact.insert(ignore_permissions=True)
    return contact.name


def get_or_create_customer(name, email, phone):
    """Get or create ERPNext Customer"""
    if email:
        existing = frappe.db.get_value("Customer", {"email_id": email}, "name")
        if existing:
            return existing

    if phone:
        existing = frappe.db.get_value("Customer", {"mobile_no": phone}, "name")
        if existing:
            return existing

    customer = frappe.new_doc("Customer")
    customer.customer_name = name or "ONDC Customer"
    customer.customer_type = "Individual"
    customer.customer_group = "ONDC Customers"
    customer.territory = "India"
    customer.email_id = email
    customer.mobile_no = phone
    customer.insert(ignore_permissions=True)
    return customer.name


def get_ticket_by_ondc_issue_id(issue_id):
    """Find Helpdesk ticket or ERPNext Issue by ONDC Issue ID"""
    # Try HD Ticket first
    if frappe.db.exists("DocType", "HD Ticket"):
        ticket_name = frappe.db.get_value(
            "HD Ticket", {"custom_ondc_issue_id": issue_id}, "name"
        )
        if ticket_name:
            return frappe.get_doc("HD Ticket", ticket_name)

    # Try ERPNext Issue
    if frappe.db.exists("DocType", "Issue"):
        issue_name = frappe.db.get_value(
            "Issue", {"custom_ondc_issue_id": issue_id}, "name"
        )
        if issue_name:
            return frappe.get_doc("Issue", issue_name)

    # Try webhook log fallback
    log_name = frappe.db.sql("""
        SELECT name FROM `tabONDC Webhook Log`
        WHERE webhook_type = 'issue'
        AND request_body LIKE %s
        LIMIT 1
    """, (f'%"issue_id": "{issue_id}"%',))

    if log_name:
        return frappe.get_doc("ONDC Webhook Log", log_name[0][0])

    return None


def send_on_issue(context, issue_data, ticket):
    """Send /on_issue callback to BAP"""
    settings = frappe.get_single("ONDC Settings")
    client = ONDCClient(settings)

    response_context = client.create_context("on_issue", context)

    # Determine ticket status
    ticket_status = "Open"
    if hasattr(ticket, "status"):
        ticket_status = ticket.status
    elif hasattr(ticket, "ticket_status"):
        ticket_status = ticket.ticket_status

    ondc_status = HD_TO_ONDC_STATUS.get(ticket_status, "OPEN")

    payload = {
        "context": response_context,
        "message": {
            "issue": {
                "id": issue_data.get("id"),
                "issue_status": ondc_status,
                "issue_actions": {
                    "respondent_actions": [
                        {
                            "respondent_action": "PROCESSING",
                            "short_desc": "Issue received and being processed",
                            "updated_at": datetime.utcnow().isoformat() + "Z",
                            "updated_by": {
                                "org": {
                                    "name": settings.legal_entity_name or settings.subscriber_id
                                },
                                "contact": {
                                    "phone": settings.get("consumer_care_phone") or "",
                                    "email": settings.get("consumer_care_email") or "",
                                },
                                "person": {
                                    "name": "Support Team"
                                }
                            }
                        }
                    ]
                },
                "created_at": datetime.utcnow().isoformat() + "Z",
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
        }
    }

    # Queue the callback
    frappe.enqueue(
        "ondc_seller_app.api.igm_adapter.send_igm_callback",
        queue="default",
        callback_url=context.get("bap_uri"),
        endpoint="/on_issue",
        payload=payload,
    )


def send_on_issue_status(context, issue_id, ticket):
    """Send /on_issue_status callback to BAP"""
    settings = frappe.get_single("ONDC Settings")
    client = ONDCClient(settings)

    response_context = client.create_context("on_issue_status", context)

    # Determine ticket status
    ticket_status = "Open"
    resolution_remarks = ""

    if hasattr(ticket, "status"):
        ticket_status = ticket.status
    elif hasattr(ticket, "ticket_status"):
        ticket_status = ticket.ticket_status

    if hasattr(ticket, "resolution"):
        resolution_remarks = ticket.resolution or ""

    ondc_status = HD_TO_ONDC_STATUS.get(ticket_status, "OPEN")

    payload = {
        "context": response_context,
        "message": {
            "issue": {
                "id": issue_id,
                "issue_status": ondc_status,
                "resolution": {
                    "short_desc": resolution_remarks[:200] if resolution_remarks else f"Issue is {ondc_status}",
                    "action_triggered": "NO-ACTION" if ondc_status == "OPEN" else "RESOLVE",
                },
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
        }
    }

    # Queue the callback
    frappe.enqueue(
        "ondc_seller_app.api.igm_adapter.send_igm_callback",
        queue="default",
        callback_url=context.get("bap_uri"),
        endpoint="/on_issue_status",
        payload=payload,
    )


def send_igm_callback(callback_url, endpoint, payload):
    """Send IGM callback to BAP (called from queue)"""
    settings = frappe.get_single("ONDC Settings")
    client = ONDCClient(settings)

    result = client.send_callback(callback_url, endpoint, payload)

    # Log the callback
    log = frappe.new_doc("ONDC Webhook Log")
    log.webhook_type = endpoint.replace("/", "")
    log.transaction_id = payload.get("context", {}).get("transaction_id")
    log.message_id = payload.get("context", {}).get("message_id")
    log.request_body = json.dumps(payload, indent=2)
    log.response_body = json.dumps(result, indent=2)
    log.status = "Processed" if result.get("success") else "Failed"
    log.insert(ignore_permissions=True)
    frappe.db.commit()


# Hook for Helpdesk ticket status change
def on_hd_ticket_update(doc, method):
    """
    Hook triggered when HD Ticket is updated.
    Sends on_issue_status to BAP if this is an ONDC issue.
    """
    if not hasattr(doc, "custom_ondc_issue_id") or not doc.custom_ondc_issue_id:
        return

    # Check if status changed
    if doc.has_value_changed("status") or doc.has_value_changed("ticket_status"):
        # Build context from stored values
        context = {
            "bap_id": doc.get("custom_bap_id"),
            "bap_uri": get_bap_uri(doc.get("custom_bap_id")),
            "transaction_id": doc.get("custom_transaction_id"),
        }

        if context.get("bap_uri"):
            send_on_issue_status(context, doc.custom_ondc_issue_id, doc)


def get_bap_uri(bap_id):
    """Look up BAP URI from registry or cache"""
    # For now, we store it in the ticket. In production, query ONDC registry.
    # This is a placeholder - real implementation would cache registry lookups.
    return None  # Will use bap_uri from original context stored in ticket


# ---------------------------------------------------------------------------
# Direct API Endpoint Handlers (for website_route_rules)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def issue():
    """
    Handle /issue API endpoint directly.
    Called when ONDC routes /issue directly to this handler.
    """
    try:
        data = json.loads(frappe.request.data)
        result = handle_issue(data)
        frappe.local.response.update(result)
    except Exception as e:
        frappe.log_error(f"IGM /issue endpoint error: {str(e)}", "ONDC IGM")
        frappe.local.response.update(build_nack_response("30000", str(e)))


@frappe.whitelist(allow_guest=True)
def issue_status():
    """
    Handle /issue_status API endpoint directly.
    Called when ONDC routes /issue_status directly to this handler.
    """
    try:
        data = json.loads(frappe.request.data)
        result = handle_issue_status(data)
        frappe.local.response.update(result)
    except Exception as e:
        frappe.log_error(f"IGM /issue_status endpoint error: {str(e)}", "ONDC IGM")
        frappe.local.response.update(build_nack_response("30000", str(e)))


# ---------------------------------------------------------------------------
# IGM Adapter Class (for use by webhook handler)
# ---------------------------------------------------------------------------

class IGMAdapter:
    """Adapter class for use by webhook handler"""

    def handle_issue(self, data):
        """Handle /issue request"""
        return handle_issue(data)

    def handle_issue_status(self, data):
        """Handle /issue_status request"""
        return handle_issue_status(data)
