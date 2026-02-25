import frappe
from frappe.utils import now_datetime, add_to_date
from datetime import datetime, timedelta

def sync_inventory():
    """Sync inventory levels from ERPNext stock to ONDC Products.
    
    V12 FIX: Only syncs products that have a valid, linked ERPNext Item.
    Products created standalone (e.g. via demo script) without an ERPNext
    item_code are SKIPPED to prevent their available_quantity being reset to 0.
    """
    try:
        # Check if erpnext is installed (not all Frappe sites have it)
        try:
            from erpnext.stock.utils import get_stock_balance
            has_erpnext = True
        except ImportError:
            has_erpnext = False
            frappe.log_error(
                "ERPNext not installed — skipping inventory sync",
                "ONDC Inventory Sync"
            )
            return

        # Get all active ONDC products
        products = frappe.get_all(
            'ONDC Product',
            filters={'is_active': 1},
            fields=['name', 'item_code', 'available_quantity']
        )
        
        synced = 0
        skipped = 0
        
        for product in products:
            item_code = product.get('item_code')
            
            # V12: Skip products without a linked ERPNext Item
            if not item_code:
                skipped += 1
                continue
            
            # Verify the Item actually exists in ERPNext
            if not frappe.db.exists('Item', item_code):
                skipped += 1
                continue
            
            # Get current stock from ERPNext
            stock = get_item_stock(item_code, get_stock_balance)
            
            # Only update if stock actually changed
            current_qty = int(product.get('available_quantity') or 0)
            if current_qty != int(stock):
                frappe.db.set_value(
                    'ONDC Product', product['name'],
                    'available_quantity', int(stock),
                    update_modified=False
                )
                synced += 1
                
                # Sync to ONDC if auto-sync enabled
                settings = frappe.get_single('ONDC Settings')
                if settings.get('auto_sync_inventory'):
                    doc = frappe.get_doc('ONDC Product', product['name'])
                    doc.sync_to_ondc()
        
        if synced or skipped:
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

def get_item_stock(item_code, get_stock_balance_fn=None):
    """Get available stock for an item from ERPNext.
    
    Args:
        item_code: The ERPNext Item code
        get_stock_balance_fn: Optional pre-imported get_stock_balance function
    """
    if get_stock_balance_fn is None:
        try:
            from erpnext.stock.utils import get_stock_balance
            get_stock_balance_fn = get_stock_balance
        except ImportError:
            return 0
    
    # Get default warehouse from settings
    settings = frappe.get_single('ONDC Settings')
    warehouse = settings.get('default_warehouse') or frappe.db.get_single_value('Stock Settings', 'default_warehouse')
    
    if warehouse:
        return get_stock_balance_fn(item_code, warehouse) or 0
    
    # If no warehouse, get total stock
    result = frappe.db.sql("""
        SELECT SUM(actual_qty) 
        FROM `tabBin` 
        WHERE item_code = %s
    """, item_code)
    return (result[0][0] or 0) if result else 0

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
