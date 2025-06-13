import frappe
from frappe.model.document import Document
import json

class ONDCOrder(Document):
    def validate(self):
        self.calculate_totals()
        self.validate_order_status()
    
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
        
        # Define valid status transitions
        valid_transitions = {
            "Pending": ["Accepted", "Cancelled"],
            "Accepted": ["In-progress", "Cancelled"],
            "In-progress": ["Completed", "Cancelled"],
            "Completed": [],
            "Cancelled": []
        }
        
        if new_status != old_status:
            if new_status not in valid_transitions.get(old_status, []):
                frappe.throw(f"Invalid status transition from {old_status} to {new_status}")
    
    @frappe.whitelist()
    def create_sales_order(self):
        """Create Sales Order from ONDC Order"""
        if self.sales_order:
            frappe.throw("Sales Order already created")
        
        # Create customer if not exists
        customer = self.get_or_create_customer()
        
        # Create sales order
        so = frappe.new_doc("Sales Order")
        so.customer = customer
        so.delivery_date = frappe.utils.today()
        so.po_no = self.ondc_order_id
        
        # Add items
        for item in self.items:
            so.append("items", {
                "item_code": item.item_code,
                "qty": item.quantity,
                "rate": item.price
            })
        
        so.insert()
        so.submit()
        
        self.sales_order = so.name
        self.save()
        
        frappe.msgprint(f"Sales Order {so.name} created successfully")
        return so.name
    
    def get_or_create_customer(self):
        """Get or create customer from order details"""
        # Check if customer exists with phone
        if self.customer_phone:
            customer = frappe.db.get_value("Customer", 
                {"mobile_no": self.customer_phone}, "name")
            if customer:
                return customer
        
        # Create new customer
        customer = frappe.new_doc("Customer")
        customer.customer_name = self.customer_name or self.billing_name
        customer.customer_type = "Individual"
        customer.customer_group = "ONDC Customer"
        customer.territory = "India"
        
        if self.customer_phone:
            customer.mobile_no = self.customer_phone
        if self.customer_email:
            customer.email_id = self.customer_email
        
        # Add billing address
        if self.billing_name:
            customer.append("addresses", {
                "address_title": self.billing_name,
                "address_line1": f"{self.billing_building}, {self.billing_locality}",
                "city": self.billing_city,
                "state": self.billing_state,
                "pincode": self.billing_area_code,
                "is_primary_address": 1,
                "is_billing_address": 1
            })
        
        # Add shipping address
        if self.shipping_address:
            customer.append("addresses", {
                "address_title": "Shipping Address",
                "address_line1": self.shipping_address,
                "is_shipping_address": 1
            })
        
        customer.insert(ignore_permissions=True)
        return customer.name
    
    @frappe.whitelist()
    def update_fulfillment_status(self, status, tracking_url=None):
        """Update fulfillment status"""
        from ondc_seller_app.api.ondc_client import ONDCClient
        
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        
        response = client.on_status({
            "order_id": self.ondc_order_id,
            "fulfillment_id": self.fulfillment_id,
            "status": status,
            "tracking_url": tracking_url
        })
        
        if response.get("success"):
            frappe.msgprint("Status updated on ONDC network")
        else:
            frappe.throw(f"Status update failed: {response.get('error')}")