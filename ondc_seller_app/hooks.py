app_name = "ondc_seller_app"
app_title = "ONDC Seller App"
app_publisher = "Your Company"
app_description = "Frappe app for ONDC sellers to manage products, orders, and integrations"
app_icon = "octicon octicon-package"
app_color = "blue"
app_email = "info@example.com"
app_license = "MIT"

# Document Events
doc_events = {
    "Item": {
        "after_insert": "ondc_seller_app.utils.item_hooks.create_ondc_product",
        "on_update": "ondc_seller_app.utils.item_hooks.update_ondc_product"
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
        "ondc_seller_app.tasks.sync_orders"
    ]
}

fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            ["dt", "in", ["Item", "Sales Order", "Customer"]]
        ]
    }
]

# Website routes
website_route_rules = [
    {"from_route": "/ondc/webhook/<path:api>", "to_route": "ondc_seller_app.api.webhook.handle_webhook"}
]