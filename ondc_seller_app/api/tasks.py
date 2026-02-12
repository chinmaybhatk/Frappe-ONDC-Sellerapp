import frappe
from frappe.utils import now_datetime, add_to_date
from datetime import datetime, timedelta

def sync_inventory():
    """Sync inventory levels to ONDC network.
    Only syncs products that have a linked ERPNext item_code AND
    the item actually exists in ERPNext stock. Products without
    ERPNext stock entries keep their manually-set available_quantity.
    """
    try:
        # Get all active ONDC products that have an item_code linked
        products = frappe.get_all(
            'ONDC Product',
            filters={'is_active': 1, 'item_code': ['is', 'set']},
            fields=['name', 'item_code']
        )

        for product in products:
            if not product.get('item_code'):
                continue

            # Only sync if the ERPNext Item actually exists
            if not frappe.db.exists('Item', product['item_code']):
                continue

            # Get current stock
            stock = get_item_stock(product['item_code'])

            # Update ONDC Product
            doc = frappe.get_doc('ONDC Product', product['name'])
            if doc.available_quantity != stock:
                doc.available_quantity = stock
                doc.save(ignore_permissions=True)

                # Sync to ONDC if auto-sync enabled
                settings = frappe.get_single('ONDC Settings')
                if settings.get('auto_sync_inventory'):
                    doc.sync_to_ondc()

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(f"Inventory sync failed: {str(e)}", "ONDC Inventory Sync")

def sync_orders():
    """Sync pending orders from ONDC network"""
    try:
        # This would typically poll for new orders from ONDC
        # For now, we'll just check for pending orders that need status updates
        
        pending_orders = frappe.get_all(
            'ONDC Order',
            filters={
                'order_status': ['in', ['Accepted', 'In-progress']],
                'updated_at': ['<', add_to_date(now_datetime(), hours=-1)]
            },
            fields=['name', 'ondc_order_id', 'order_status']
        )
        
        for order in pending_orders:
            doc = frappe.get_doc('ONDC Order', order['name'])
            
            # Check if linked Sales Order exists and update status
            if doc.sales_order:
                so_status = frappe.db.get_value('Sales Order', doc.sales_order, 'status')
                
                status_map = {
                    'To Deliver and Bill': 'Accepted',
                    'To Bill': 'In-progress',
                    'To Deliver': 'In-progress',
                    'Completed': 'Completed',
                    'Cancelled': 'Cancelled'
                }
                
                new_status = status_map.get(so_status)
                if new_status and new_status != doc.order_status:
                    doc.order_status = new_status
                    doc.save(ignore_permissions=True)
        
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(f"Order sync failed: {str(e)}", "ONDC Order Sync")

def get_item_stock(item_code):
    """Get available stock for an item"""
    from erpnext.stock.utils import get_stock_balance
    
    # Get default warehouse from settings
    settings = frappe.get_single('ONDC Settings')
    warehouse = settings.get('default_warehouse') or frappe.db.get_single_value('Stock Settings', 'default_warehouse')
    
    if warehouse:
        return get_stock_balance(item_code, warehouse) or 0
    
    # If no warehouse, get total stock
    return frappe.db.sql("""
        SELECT SUM(actual_qty) 
        FROM `tabBin` 
        WHERE item_code = %s
    """, item_code)[0][0] or 0

def cleanup_webhook_logs():
    """Clean up old webhook logs"""
    try:
        # Delete logs older than 30 days
        cutoff_date = datetime.now() - timedelta(days=30)
        
        frappe.db.sql("""
            DELETE FROM `tabONDC Webhook Log`
            WHERE created_at < %s
        """, cutoff_date)
        
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(f"Webhook log cleanup failed: {str(e)}", "ONDC Webhook Cleanup")