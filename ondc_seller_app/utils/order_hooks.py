import frappe
from frappe import _

def create_ondc_order(doc, method):
    """Create ONDC Order when Sales Order is created from ONDC"""
    # Check if this Sales Order is from ONDC (has po_no starting with ONDC)
    if not doc.po_no or not doc.po_no.startswith('ONDC'):
        return
    
    # Check if ONDC Order already exists
    if frappe.db.exists('ONDC Order', {'sales_order': doc.name}):
        return
    
    # Link back to ONDC Order
    ondc_order = frappe.db.get_value('ONDC Order', {'ondc_order_id': doc.po_no}, 'name')
    if ondc_order:
        frappe.db.set_value('ONDC Order', ondc_order, 'sales_order', doc.name)
        frappe.msgprint(_("Linked to ONDC Order {0}").format(doc.po_no))

def update_ondc_order_status(doc, method):
    """Update ONDC Order status when Sales Order status changes"""
    # Check if this Sales Order is linked to ONDC Order
    ondc_order_name = frappe.db.get_value('ONDC Order', {'sales_order': doc.name}, 'name')
    if not ondc_order_name:
        return
    
    try:
        ondc_order = frappe.get_doc('ONDC Order', ondc_order_name)
        
        # Map Sales Order status to ONDC Order status
        status_map = {
            'Draft': 'Pending',
            'On Hold': 'Pending',
            'To Deliver and Bill': 'Accepted',
            'To Bill': 'In-progress',
            'To Deliver': 'In-progress',
            'Completed': 'Completed',
            'Cancelled': 'Cancelled',
            'Closed': 'Completed'
        }
        
        new_status = status_map.get(doc.status)
        if new_status and new_status != ondc_order.order_status:
            ondc_order.order_status = new_status
            ondc_order.save(ignore_permissions=True)
            
            # Send status update to ONDC network
            if new_status in ['In-progress', 'Completed', 'Cancelled']:
                ondc_order.update_fulfillment_status(new_status)
        
    except Exception as e:
        frappe.log_error(f"Failed to update ONDC Order status: {str(e)}", "ONDC Order Status Update")