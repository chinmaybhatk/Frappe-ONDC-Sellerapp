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

# Website routes - includes webhook handler, registry, IGM, and RSP endpoints
website_route_rules = [
    # Core ONDC webhook handler (search, select, init, confirm, status, etc.)
    {"from_route": "/ondc/webhook/<path:api>", "to_route": "ondc_seller_app.api.webhook.handle_webhook"},
    # Registry onboarding callback
    {"from_route": "/on_subscribe", "to_route": "ondc_seller_app.api.ondc_client.handle_on_subscribe"},
    # IGM (Issue & Grievance Management) endpoints
    {"from_route": "/issue", "to_route": "ondc_seller_app.api.igm_adapter.issue"},
    {"from_route": "/issue_status", "to_route": "ondc_seller_app.api.igm_adapter.issue_status"},
    # RSP (Reconciliation & Settlement Protocol) endpoints
    {"from_route": "/receiver_recon", "to_route": "ondc_seller_app.api.rsp_adapter.receiver_recon"},
]
