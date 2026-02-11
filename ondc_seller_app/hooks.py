app_name = "ondc_seller_app"
app_title = "ONDC Seller App"
app_publisher = "Your Company"
app_description = "Frappe app for ONDC sellers to manage products, orders, and integrations"
app_icon = "octicon octicon-package"
app_color = "blue"
app_email = "info@example.com"
app_license = "MIT"

# After app install
after_install = "ondc_seller_app.after_install.after_install"

# Fixtures - includes custom fields for Item, Website Item, Sales Order, Customer
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            ["dt", "in", ["Item", "Website Item", "Sales Order", "Customer", "Customer Group"]]
        ]
    }
]

# Document Events
# Product sync is controlled by ONDC Settings (sync_source field)
# Both hooks are registered but check settings before executing
doc_events = {
    "Item": {
        "after_insert": "ondc_seller_app.utils.item_hooks.create_ondc_product",
        "on_update": "ondc_seller_app.utils.item_hooks.update_ondc_product"
    },
    "Website Item": {
        "after_insert": "ondc_seller_app.utils.webshop_hooks.create_ondc_product_from_website_item",
        "on_update": "ondc_seller_app.utils.webshop_hooks.update_ondc_product_from_website_item",
        "on_trash": "ondc_seller_app.utils.webshop_hooks.on_website_item_delete"
    },
    "Sales Order": {
        "after_insert": "ondc_seller_app.utils.order_hooks.create_ondc_order",
        "on_update": "ondc_seller_app.utils.order_hooks.update_ondc_order_status"
    }
}

# Scheduled Tasks
scheduler_events = {
    "hourly": [
        "ondc_seller_app.tasks.sync_inventory"
    ],
    "daily": [
        "ondc_seller_app.tasks.sync_orders",
        "ondc_seller_app.tasks.cleanup_webhook_logs"
    ]
}

# Website routes
# Root-level ONDC protocol endpoints (subscriber_url = https://domain.com)
# These map each ONDC action to its own handler function.
# Frappe also exposes them at /api/method/ondc_seller_app.api.webhook.handle_<action>
# as a guaranteed fallback on Frappe Cloud.
website_route_rules = [
    # --- Root-level ONDC protocol endpoints ---
    {"from_route": "/search", "to_route": "ondc_seller_app.api.webhook.handle_search"},
    {"from_route": "/select", "to_route": "ondc_seller_app.api.webhook.handle_select"},
    {"from_route": "/init", "to_route": "ondc_seller_app.api.webhook.handle_init"},
    {"from_route": "/confirm", "to_route": "ondc_seller_app.api.webhook.handle_confirm"},
    {"from_route": "/status", "to_route": "ondc_seller_app.api.webhook.handle_status"},
    {"from_route": "/track", "to_route": "ondc_seller_app.api.webhook.handle_track"},
    {"from_route": "/cancel", "to_route": "ondc_seller_app.api.webhook.handle_cancel"},
    {"from_route": "/update", "to_route": "ondc_seller_app.api.webhook.handle_update"},
    {"from_route": "/rating", "to_route": "ondc_seller_app.api.webhook.handle_rating"},
    {"from_route": "/support", "to_route": "ondc_seller_app.api.webhook.handle_support"},
    # --- Prefixed routes (subscriber_url = https://domain.com/ondc/webhook) ---
    {"from_route": "/ondc/webhook/<path:api>", "to_route": "ondc_seller_app.api.webhook.handle_webhook"},
    # --- Registry onboarding callback ---
    {"from_route": "/on_subscribe", "to_route": "ondc_seller_app.api.ondc_client.handle_on_subscribe"},
    # --- IGM (Issue & Grievance Management) endpoints ---
    {"from_route": "/issue", "to_route": "ondc_seller_app.api.igm_adapter.issue"},
    {"from_route": "/issue_status", "to_route": "ondc_seller_app.api.igm_adapter.issue_status"},
    # --- RSP (Reconciliation & Settlement Protocol) endpoints ---
    {"from_route": "/receiver_recon", "to_route": "ondc_seller_app.api.rsp_adapter.receiver_recon"},
]
