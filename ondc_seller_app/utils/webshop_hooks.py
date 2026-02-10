import frappe
from frappe import _
from frappe.utils import now_datetime

def is_webshop_sync_enabled():
    """Check if Frappe Webshop sync is enabled in ONDC Settings"""
    try:
        settings = frappe.get_single('ONDC Settings')
        sync_source = settings.get('product_sync_source') or ''
        return sync_source in ('Frappe Webshop', 'Both')
    except Exception:
        return False  # Default to disabled if settings not found

def create_ondc_product_from_website_item(doc, method):
    """Create ONDC Product when Website Item is created with sync_to_ondc enabled"""
    # Check if Webshop sync is enabled in ONDC Settings
    if not is_webshop_sync_enabled():
        return

    if frappe.db.exists('ONDC Product', {'website_item': doc.name}):
        return

    # Check if item should be synced to ONDC
    if not doc.get('sync_to_ondc'):
        return

    try:
        ondc_product = frappe.new_doc('ONDC Product')
        ondc_product.website_item = doc.name
        ondc_product.item_code = doc.item_code  # Link to ERPNext Item
        ondc_product.product_name = doc.web_item_name or doc.item_name
        ondc_product.short_desc = doc.short_description or (doc.description[:200] if doc.description else '')
        ondc_product.long_desc = doc.description

        # Get price from Website Item or fallback to Item
        ondc_product.price = get_website_item_price(doc)

        # Get brand from linked Item
        if doc.item_code:
            item = frappe.get_cached_doc('Item', doc.item_code)
            ondc_product.brand = item.brand
            ondc_product.country_of_origin = doc.get('country_of_origin') or item.get('country_of_origin') or 'IND'
        else:
            ondc_product.country_of_origin = doc.get('country_of_origin') or 'IND'

        # Set category
        if doc.get('ondc_category_code'):
            ondc_product.category_code = extract_category_code(doc.ondc_category_code)
        else:
            ondc_product.category_code = map_item_group_to_ondc(doc.item_group)

        # Add images from Website Item slideshow or default image
        add_website_item_images(ondc_product, doc)

        ondc_product.insert(ignore_permissions=True)

        # Update Website Item with ONDC Product ID and sync status
        frappe.db.set_value('Website Item', doc.name, {
            'ondc_product_id': ondc_product.name,
            'ondc_sync_status': 'Synced',
            'ondc_last_synced': now_datetime()
        }, update_modified=False)

        frappe.msgprint(_("ONDC Product created for {0}").format(doc.web_item_name or doc.item_name))

    except Exception as e:
        frappe.db.set_value('Website Item', doc.name, {
            'ondc_sync_status': 'Sync Failed'
        }, update_modified=False)
        frappe.log_error(f"Failed to create ONDC Product from Website Item: {str(e)}", "ONDC Webshop Sync")


def update_ondc_product_from_website_item(doc, method):
    """Update ONDC Product when Website Item is updated"""
    # Check if Webshop sync is enabled in ONDC Settings
    if not is_webshop_sync_enabled():
        return

    if not frappe.db.exists('ONDC Product', {'website_item': doc.name}):
        # Create if sync_to_ondc is enabled
        if doc.get('sync_to_ondc'):
            create_ondc_product_from_website_item(doc, method)
        return

    # If sync_to_ondc is disabled, deactivate the ONDC product
    if not doc.get('sync_to_ondc'):
        try:
            ondc_product = frappe.get_doc('ONDC Product', {'website_item': doc.name})
            if ondc_product.get('is_active'):
                ondc_product.is_active = 0
                ondc_product.save(ignore_permissions=True)
        except Exception:
            pass
        return

    try:
        ondc_product = frappe.get_doc('ONDC Product', {'website_item': doc.name})

        # Update basic details
        ondc_product.product_name = doc.web_item_name or doc.item_name
        ondc_product.short_desc = doc.short_description or (doc.description[:200] if doc.description else '')
        ondc_product.long_desc = doc.description
        ondc_product.price = get_website_item_price(doc) or ondc_product.price

        # Update brand from linked Item
        if doc.item_code:
            item = frappe.get_cached_doc('Item', doc.item_code)
            ondc_product.brand = item.brand
            ondc_product.country_of_origin = doc.get('country_of_origin') or item.get('country_of_origin') or ondc_product.country_of_origin

        # Update category if explicitly set
        if doc.get('ondc_category_code'):
            ondc_product.category_code = extract_category_code(doc.ondc_category_code)

        # Update images
        add_website_item_images(ondc_product, doc)

        # Ensure product is active
        ondc_product.is_active = 1

        ondc_product.save(ignore_permissions=True)

        # Update sync status
        frappe.db.set_value('Website Item', doc.name, {
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
        frappe.db.set_value('Website Item', doc.name, {
            'ondc_sync_status': 'Sync Failed'
        }, update_modified=False)
        frappe.log_error(f"Failed to update ONDC Product from Website Item: {str(e)}", "ONDC Webshop Sync")


def get_website_item_price(doc):
    """Get the best price for a Website Item"""
    # Try to get price from Website Item's pricing rule or Item Price
    try:
        from erpnext.e_commerce.shopping_cart.product_info import get_product_info_for_website
        product_info = get_product_info_for_website(doc.name, skip_quotation_creation=True)
        if product_info and product_info.get('price'):
            return product_info['price'].get('price_list_rate') or product_info['price'].get('formatted_price_sales_uom')
    except Exception:
        pass

    # Fallback to Item's standard rate
    if doc.item_code:
        return frappe.db.get_value('Item', doc.item_code, 'standard_rate') or 0

    return 0


def add_website_item_images(ondc_product, website_item):
    """Add images from Website Item to ONDC Product"""
    ondc_product.images = []

    # Add main image
    if website_item.website_image:
        ondc_product.append('images', {
            'image_url': website_item.website_image,
            'size_type': 'medium'
        })

    # Add slideshow images if available
    if website_item.get('slideshow'):
        try:
            slideshow = frappe.get_doc('Website Slideshow', website_item.slideshow)
            for slide in slideshow.get('slideshow_items', []):
                if slide.image and slide.image != website_item.website_image:
                    ondc_product.append('images', {
                        'image_url': slide.image,
                        'size_type': 'medium'
                    })
        except Exception:
            pass

    # Fallback to Item image if no website image
    if not ondc_product.images and website_item.item_code:
        item_image = frappe.db.get_value('Item', website_item.item_code, 'image')
        if item_image:
            ondc_product.append('images', {
                'image_url': item_image,
                'size_type': 'medium'
            })


def extract_category_code(category_string):
    """Extract ONDC category code from 'ONDC:RET10 - Grocery' format"""
    if ' - ' in category_string:
        return category_string.split(' - ')[0]
    return category_string


def map_item_group_to_ondc(item_group):
    """Map Item Group to ONDC category code"""
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
    return category_map.get(item_group, 'ONDC:RET10')


def on_website_item_delete(doc, method):
    """Handle Website Item deletion - deactivate ONDC Product"""
    # No need to check settings - if product exists, deactivate it
    if not frappe.db.exists('ONDC Product', {'website_item': doc.name}):
        return

    try:
        ondc_product = frappe.get_doc('ONDC Product', {'website_item': doc.name})
        ondc_product.is_active = 0
        ondc_product.save(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(f"Failed to deactivate ONDC Product: {str(e)}", "ONDC Webshop Sync")
