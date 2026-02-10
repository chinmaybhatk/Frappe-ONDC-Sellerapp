from frappe import _

def get_data():
    return [
        {
            "label": _("Setup"),
            "items": [
                {
                    "type": "doctype",
                    "name": "ONDC Settings",
                    "description": _("Configure ONDC Integration"),
                    "onboard": 1,
                }
            ]
        },
        {
            "label": _("Products"),
            "items": [
                {
                    "type": "doctype",
                    "name": "ONDC Product",
                    "description": _("Manage ONDC Product Catalog"),
                    "onboard": 1,
                },
                {
                    "type": "doctype",
                    "name": "Item",
                    "description": _("Item Master"),
                }
            ]
        },
        {
            "label": _("Orders"),
            "items": [
                {
                    "type": "doctype",
                    "name": "ONDC Order",
                    "description": _("ONDC Order Management"),
                },
                {
                    "type": "doctype",
                    "name": "Sales Order",
                    "description": _("Sales Orders"),
                }
            ]
        },
        {
            "label": _("Logs"),
            "items": [
                {
                    "type": "doctype",
                    "name": "ONDC Webhook Log",
                    "description": _("Webhook Activity Logs"),
                }
            ]
        },
        {
            "label": _("Reports"),
            "items": [
                {
                    "type": "report",
                    "name": "ONDC Order Summary",
                    "doctype": "ONDC Order",
                    "is_query_report": True,
                },
                {
                    "type": "report",
                    "name": "ONDC Product Performance",
                    "doctype": "ONDC Product",
                    "is_query_report": True,
                }
            ]
        }
    ]