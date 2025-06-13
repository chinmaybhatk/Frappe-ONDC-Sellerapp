from frappe import _

def get_data():
    return [
        {
            "module_name": "ONDC Seller",
            "category": "Modules",
            "label": _("ONDC Seller"),
            "color": "blue",
            "icon": "octicon octicon-package",
            "type": "module",
            "description": "Manage ONDC products, orders and integrations",
        }
    ]