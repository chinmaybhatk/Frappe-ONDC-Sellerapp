import frappe
from frappe import _
from frappe.utils import now_datetime

def is_item_sync_enabled():
    """Check if ERPNext Item sync is enabled in ONDC Settings"""
    try:
        settings = frappe.get_single('ONDC Settings')
        sync_source = settings.get('product_sync_source') or ''
        return sync_source in ('ERPNext Item', 'Both')
    except Exception:
        return True  # Default to enabled if settings not found

def create_ondc_product(doc, method):
    """Create ONDC Product when new Item is created with sync_to_ondc enabled"""
    # Check if Item sync is enabled in ONDC Settings
    if not is_item_sync_enabled():
        return

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
        ondc_product.country_of_origin = doc.get('country_of_origin') or 'IND'

        # Use explicit category if set, otherwise map from item group
        if doc.get('ondc_category_code'):
            # Extract code from "ONDC:RET10 - Grocery" format
            ondc_product.category_code = doc.ondc_category_code.split(' - ')[0] if ' - ' in doc.ondc_category_code else doc.ondc_category_code
        else:
            # Auto-map from item group
            category_map = {
                'Grocery': 'ONDC:RET10',
                'Food & Beverages': 'ONDC:RET11',
                'Fashion': 'ONDC:RET12',
                'Beauty & Personal Care': 'ONDC:RET13',
                'Electronics': 'ONDC:RET14',
                'Home & Decor': 'ONDC:RET15',
                'Health & Wellness': 'ONDC:RET16',
                'Pharma': 'ONDC:RET17',
                'Agriculture': 'ONDC:RET18'
            }
            ondc_product.category_code = category_map.get(doc.item_group, 'ONDC:RET10')

        # Add default image if available
        if doc.image:
            ondc_product.append('images', {
                'image_url': doc.image,
                'size_type': 'medium'
            })

        ondc_product.insert(ignore_permissions=True)

        # Update Item with ONDC Product ID and sync status
        frappe.db.set_value('Item', doc.name, {
            'ondc_product_id': ondc_product.name,
            'ondc_sync_status': 'Synced',
            'ondc_last_synced': now_datetime()
        }, update_modified=False)

        frappe.msgprint(_("ONDC Product created for {0}").format(doc.item_name))

    except Exception as e:
        # Update sync status to failed
        frappe.db.set_value('Item', doc.name, {
            'ondc_sync_status': 'Sync Failed'
        }, update_modified=False)
        frappe.log_error(f"Failed to create ONDC Product: {str(e)}", "ONDC Product Creation")

def update_ondc_product(doc, method):
    """Update ONDC Product when Item is updated"""
    # Check if Item sync is enabled in ONDC Settings
    if not is_item_sync_enabled():
        return

    if not frappe.db.exists('ONDC Product', {'item_code': doc.name}):
        # Create if sync_to_ondc is enabled
        if doc.get('sync_to_ondc'):
            create_ondc_product(doc, method)
        return

    # If sync_to_ondc is disabled, optionally disable the ONDC product
    if not doc.get('sync_to_ondc'):
        try:
            ondc_product = frappe.get_doc('ONDC Product', {'item_code': doc.name})
            if ondc_product.get('is_active'):
                ondc_product.is_active = 0
                ondc_product.save(ignore_permissions=True)
        except Exception:
            pass
        return

    try:
        ondc_product = frappe.get_doc('ONDC Product', {'item_code': doc.name})

        # Update basic details
        ondc_product.product_name = doc.item_name
        ondc_product.short_desc = doc.description[:200] if doc.description else ''
        ondc_product.long_desc = doc.description
        ondc_product.price = doc.standard_rate or ondc_product.price
        ondc_product.brand = doc.brand
        ondc_product.country_of_origin = doc.get('country_of_origin') or ondc_product.country_of_origin

        # Update category if explicitly set
        if doc.get('ondc_category_code'):
            ondc_product.category_code = doc.ondc_category_code.split(' - ')[0] if ' - ' in doc.ondc_category_code else doc.ondc_category_code

        # Update image if changed
        if doc.image and not any(img.image_url == doc.image for img in ondc_product.get('images', [])):
            # Clear existing images and add new one
            ondc_product.images = []
            ondc_product.append('images', {
                'image_url': doc.image,
                'size_type': 'medium'
            })

        # Ensure product is active
        ondc_product.is_active = 1

        ondc_product.save(ignore_permissions=True)

        # Update sync status
        frappe.db.set_value('Item', doc.name, {
            'ondc_sync_status': 'Synced',
            'ondc_last_synced': now_datetime()
        }, update_modified=False)

        # Auto-sync to ONDC network if enabled in settings
        settings = frappe.get_single('ONDC Settings')
        if settings.get('auto_sync_products'):
            try:
                ondc_product.sync_to_ondc()
            except Exception as e:
                frappe.log_error(f"Failed to sync to ONDC network: {str(e)}", "ONDC Network Sync")

    except Exception as e:
        frappe.db.set_value('Item', doc.name, {
            'ondc_sync_status': 'Sync Failed'
        }, update_modified=False)
        frappe.log_error(f"Failed to update ONDC Product: {str(e)}", "ONDC Product Update")
