"""
ONDC Middleware – before_request PATH_INFO rewriter.

Frappe's ``before_request`` hook fires **before** the route resolver.
When ONDC's gateway POSTs to a root-level endpoint such as ``/search``,
this middleware rewrites ``environ['PATH_INFO']`` to the corresponding
``/api/method/…`` path so Frappe's standard whitelisted-method handler
takes over.

This approach:
  * works on Frappe Cloud without any nginx config changes,
  * co-exists with ``website_route_rules`` (belt-and-suspenders), and
  * only touches POST requests to known ONDC action paths.
"""

import frappe

# Map of root-level ONDC paths → fully-qualified whitelisted methods.
ONDC_ROUTE_MAP = {
    "/search":           "/api/method/ondc_seller_app.api.webhook.handle_search",
    "/select":           "/api/method/ondc_seller_app.api.webhook.handle_select",
    "/init":             "/api/method/ondc_seller_app.api.webhook.handle_init",
    "/confirm":          "/api/method/ondc_seller_app.api.webhook.handle_confirm",
    "/status":           "/api/method/ondc_seller_app.api.webhook.handle_status",
    "/track":            "/api/method/ondc_seller_app.api.webhook.handle_track",
    "/cancel":           "/api/method/ondc_seller_app.api.webhook.handle_cancel",
    "/update":           "/api/method/ondc_seller_app.api.webhook.handle_update",
    "/rating":           "/api/method/ondc_seller_app.api.webhook.handle_rating",
    "/support":          "/api/method/ondc_seller_app.api.webhook.handle_support",
    # Registry onboarding callback
    "/on_subscribe":     "/api/method/ondc_seller_app.api.ondc_client.handle_on_subscribe",
    # IGM (Issue & Grievance Management)
    "/issue":            "/api/method/ondc_seller_app.api.igm_adapter.issue",
    "/issue_status":     "/api/method/ondc_seller_app.api.igm_adapter.issue_status",
    # RSP (Reconciliation & Settlement Protocol)
    "/receiver_recon":   "/api/method/ondc_seller_app.api.rsp_adapter.receiver_recon",
}


def before_request():
    """Rewrite PATH_INFO for root-level ONDC endpoints so that
    Frappe resolves them as whitelisted API methods."""

    # Only act on POST (all ONDC protocol calls are POST)
    if frappe.request and frappe.request.method == "POST":
        path = frappe.request.environ.get("PATH_INFO", "")
        new_path = ONDC_ROUTE_MAP.get(path)
        if new_path:
            frappe.request.environ["PATH_INFO"] = new_path
