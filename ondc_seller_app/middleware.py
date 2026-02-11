"""
ONDC Middleware – before_request route handler via frappe.form_dict.cmd.

Frappe's ``before_request`` hook fires **before** the route resolver.
When ONDC's gateway POSTs to a root-level endpoint such as ``/search``,
this middleware sets ``frappe.form_dict.cmd`` to the corresponding
whitelisted method dotpath.  In Frappe's routing, ``form_dict.cmd``
takes priority over ALL path-based routing and routes through the
JSON API handler directly.

Note: We do NOT rewrite ``environ['PATH_INFO']`` because Werkzeug
caches ``request.path`` as a ``@cached_property`` — modifying the
environ after it has been read has no effect.

This approach:
  * works on Frappe Cloud without any nginx config changes,
  * co-exists with ``website_route_rules`` (belt-and-suspenders), and
  * only touches POST requests to known ONDC action paths.
"""

import frappe

# Map of root-level ONDC paths → fully-qualified whitelisted method dotpaths.
ONDC_ROUTE_MAP = {
    "/search":           "ondc_seller_app.api.webhook.handle_search",
    "/select":           "ondc_seller_app.api.webhook.handle_select",
    "/init":             "ondc_seller_app.api.webhook.handle_init",
    "/confirm":          "ondc_seller_app.api.webhook.handle_confirm",
    "/status":           "ondc_seller_app.api.webhook.handle_status",
    "/track":            "ondc_seller_app.api.webhook.handle_track",
    "/cancel":           "ondc_seller_app.api.webhook.handle_cancel",
    "/update":           "ondc_seller_app.api.webhook.handle_update",
    "/rating":           "ondc_seller_app.api.webhook.handle_rating",
    "/support":          "ondc_seller_app.api.webhook.handle_support",
    # Registry onboarding callback
    "/on_subscribe":     "ondc_seller_app.api.ondc_client.handle_on_subscribe",
    # IGM (Issue & Grievance Management)
    "/issue":            "ondc_seller_app.api.igm_adapter.issue",
    "/issue_status":     "ondc_seller_app.api.igm_adapter.issue_status",
    # RSP (Reconciliation & Settlement Protocol)
    "/receiver_recon":   "ondc_seller_app.api.rsp_adapter.receiver_recon",
}


def before_request():
    """Route root-level ONDC endpoints by setting frappe.form_dict.cmd
    so Frappe's JSON API handler processes them as whitelisted methods."""

    if frappe.request and frappe.request.method == "POST":
        path = frappe.request.path
        cmd = ONDC_ROUTE_MAP.get(path)
        if cmd:
            frappe.local.form_dict.cmd = cmd
