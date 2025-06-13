import frappe
from frappe import _

def create_ondc_product(doc, method):
    """Create ONDC Product when new Item is created"""
    if frappe.db.exists('ONDC Product', {'item_code': doc.name}):
        return
    
    # Check if item should be synced to ONDC
    if not doc.get('sync_to_ondc'):
        return
    
    try:
        ondc_product = frappe.new_doc('ONDC Product')
        ondc_product.item_code = doc.name
        ondc_product.product_name = doc.item_name
        ondc_product.short_desc = doc.description[:200] if doc.description else ''
        ondc_product.long_desc = doc.description
        ondc_product.price = doc.standard_rate or 0
        ondc_product.brand = doc.brand
        ondc_product.country_of_origin = doc.country_of_origin or 'IND'
        
        # Set category based on item group
        category_map = {
            'Grocery': 'ONDC:RET10',
            'Food & Beverages': 'ONDC:RET11',
            'Fashion': 'ONDC:RET12',
            'Beauty & Personal Care': 'ONDC:RET13',
            'Electronics': 'ONDC:RET14',
            'Home & Decor': 'ONDC:RET15'
        }
        
        ondc_product.category_code = category_map.get(doc.item_group, 'ONDC:RET10')
        
        # Add default image if available
        if doc.image:
            ondc_product.append('images', {
                'image_url': doc.image,
                'size_type': 'medium'
            })
        
        ondc_product.insert(ignore_permissions=True)
        frappe.msgprint(_("ONDC Product created for {0}").format(doc.item_name))
        
    except Exception as e:
        frappe.log_error(f"Failed to create ONDC Product: {str(e)}", "ONDC Product Creation")

def update_ondc_product(doc, method):
    """Update ONDC Product when Item is updated"""
    if not frappe.db.exists('ONDC Product', {'item_code': doc.name}):
        # Create if sync_to_ondc is enabled
        if doc.get('sync_to_ondc'):
            create_ondc_product(doc, method)
        return
    
    try:
        ondc_product = frappe.get_doc('ONDC Product', {'item_code': doc.name})
        
        # Update basic details
        ondc_product.product_name = doc.item_name
        ondc_product.short_desc = doc.description[:200] if doc.description else ''
        ondc_product.long_desc = doc.description
        ondc_product.price = doc.standard_rate or ondc_product.price
        ondc_product.brand = doc.brand
        
        # Update image if changed
        if doc.image and not any(img.image_url == doc.image for img in ondc_product.images):
            # Clear existing images and add new one
            ondc_product.images = []
            ondc_product.append('images', {
                'image_url': doc.image,
                'size_type': 'medium'
            })
        
        ondc_product.save(ignore_permissions=True)
        
        # Auto-sync if enabled
        settings = frappe.get_single('ONDC Settings')
        if settings.get('auto_sync_products'):
            ondc_product.sync_to_ondc()
        
    except Exception as e:
        frappe.log_error(f"Failed to update ONDC Product: {str(e)}", "ONDC Product Update")