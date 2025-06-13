import frappe
from frappe import _

def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {
            "fieldname": "ondc_order_id",
            "label": _("ONDC Order ID"),
            "fieldtype": "Link",
            "options": "ONDC Order",
            "width": 150
        },
        {
            "fieldname": "created_at",
            "label": _("Order Date"),
            "fieldtype": "Datetime",
            "width": 150
        },
        {
            "fieldname": "customer_name",
            "label": _("Customer"),
            "fieldtype": "Data",
            "width": 150
        },
        {
            "fieldname": "order_status",
            "label": _("Status"),
            "fieldtype": "Data",
            "width": 100
        },
        {
            "fieldname": "payment_type",
            "label": _("Payment Type"),
            "fieldtype": "Data",
            "width": 100
        },
        {
            "fieldname": "total_amount",
            "label": _("Total Amount"),
            "fieldtype": "Currency",
            "width": 120
        },
        {
            "fieldname": "sales_order",
            "label": _("Sales Order"),
            "fieldtype": "Link",
            "options": "Sales Order",
            "width": 120
        },
        {
            "fieldname": "fulfillment_type",
            "label": _("Fulfillment"),
            "fieldtype": "Data",
            "width": 100
        }
    ]

def get_data(filters):
    conditions = ""
    
    if filters.get("from_date"):
        conditions += f" AND created_at >= '{filters.get('from_date')}'"
    
    if filters.get("to_date"):
        conditions += f" AND created_at <= '{filters.get('to_date')} 23:59:59'"
    
    if filters.get("order_status"):
        conditions += f" AND order_status = '{filters.get('order_status')}'"
    
    if filters.get("payment_type"):
        conditions += f" AND payment_type = '{filters.get('payment_type')}'"
    
    data = frappe.db.sql(f"""
        SELECT
            ondc_order_id,
            created_at,
            customer_name,
            order_status,
            payment_type,
            total_amount,
            sales_order,
            fulfillment_type
        FROM `tabONDC Order`
        WHERE 1=1 {conditions}
        ORDER BY created_at DESC
    """, as_dict=True)
    
    return data