import frappe
from frappe.model.document import Document
import json

from ondc_seller_app.api.ondc_errors import is_valid_fulfillment_transition


class ONDCOrder(Document):
    def validate(self):
        self.calculate_totals()
        self.validate_order_status()
        self.validate_fulfillment_state()

    def calculate_totals(self):
        """Calculate order totals"""
        total = 0
        for item in self.items:
            item.amount = (item.quantity or 0) * (item.price or 0)
            total += item.amount
        self.total_amount = total

    def validate_order_status(self):
        """Validate order status transitions"""
        if self.is_new():
            return

        old_doc = frappe.get_doc(self.doctype, self.name)
        old_status = old_doc.order_status
        new_status = self.order_status

        valid_transitions = {
            "Pending": ["Accepted", "Cancelled"],
            "Accepted": ["In-progress", "Cancelled"],
            "In-progress": ["Completed", "Cancelled"],
            "Completed": [],
            "Cancelled": [],
        }

        if new_status != old_status:
            if new_status not in valid_transitions.get(old_status, []):
                frappe.throw(f"Invalid status transition from {old_status} to {new_status}")

    def validate_fulfillment_state(self):
        """Validate fulfillment state transitions using ONDC state machine"""
        if self.is_new():
            return

        old_doc = frappe.get_doc(self.doctype, self.name)
        old_state = old_doc.get("fulfillment_state") or "Pending"
        new_state = self.get("fulfillment_state") or "Pending"

        if new_state != old_state:
            if not is_valid_fulfillment_transition(old_state, new_state):
                frappe.throw(
                    f"Invalid fulfillment state transition from {old_state} to {new_state}"
                )

    @frappe.whitelist()
    def create_sales_order(self):
        """Create Sales Order from ONDC Order"""
        if self.sales_order:
            frappe.throw("Sales Order already created")

        customer = self.get_or_create_customer()

        so = frappe.new_doc("Sales Order")
        so.customer = customer
        so.delivery_date = frappe.utils.today()
        so.po_no = self.ondc_order_id

        for item in self.items:
            so.append("items", {
                "item_code": item.item_code,
                "qty": item.quantity,
                "rate": item.price,
            })

        so.insert()
        so.submit()

        self.sales_order = so.name
        self.save()

        frappe.msgprint(f"Sales Order {so.name} created successfully")
        return so.name

    def get_or_create_customer(self):
        """Get or create customer from order details"""
        if self.customer_phone:
            customer = frappe.db.get_value(
                "Customer", {"mobile_no": self.customer_phone}, "name"
            )
            if customer:
                return customer

        customer = frappe.new_doc("Customer")
        customer.customer_name = self.customer_name or self.billing_name
        customer.customer_type = "Individual"
        customer.customer_group = "ONDC Customers"
        customer.territory = "India"

        if self.customer_phone:
            customer.mobile_no = self.customer_phone
        if self.customer_email:
            customer.email_id = self.customer_email

        if self.billing_name:
            customer.append("addresses", {
                "address_title": self.billing_name,
                "address_line1": f"{self.billing_building or ''}, {self.billing_locality or ''}".strip(", "),
                "city": self.billing_city,
                "state": self.billing_state,
                "pincode": self.billing_area_code,
                "is_primary_address": 1,
                "is_billing_address": 1,
            })

        if self.shipping_address:
            customer.append("addresses", {
                "address_title": "Shipping Address",
                "address_line1": self.shipping_address,
                "is_shipping_address": 1,
            })

        customer.insert(ignore_permissions=True)
        return customer.name

    @frappe.whitelist()
    def update_fulfillment_status(self, status, tracking_url=None):
        """
        Update fulfillment status locally.

        NOTE: on_status callbacks are now ONLY sent by process_status() in
        webhook.py, which builds the full ONDC-compliant payload.  This
        method must NOT send its own callback to avoid duplicate / minimal
        responses that confuse Pramaan certification.
        """
        if tracking_url:
            self.tracking_url = tracking_url

        # Just save locally – no network callback
        self.save(ignore_permissions=True)
        frappe.msgprint("Fulfillment status updated locally")
