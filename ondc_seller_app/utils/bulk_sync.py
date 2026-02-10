import frappe
from frappe import _
from frappe.utils import now_datetime

def sync_all_items_to_ondc():
    """
    Bulk sync all items with sync_to_ondc enabled to ONDC network.
    Can be run from Console or as a scheduled job.

    Usage from Console:
        from ondc_seller_app.utils.bulk_sync import sync_all_items_to_ondc
        sync_all_items_to_ondc()
    """
    from ondc_seller_app.utils.item_hooks import create_ondc_product

    # Get all items with sync_to_ondc enabled that don't have ONDC Product
    items = frappe.get_all(
        'Item',
        filters={
            'sync_to_ondc': 1,
            'disabled': 0
        },
        fields=['name']
    )

    synced = 0
    failed = 0
    skipped = 0

    for item in items:
        try:
            # Check if ONDC Product already exists
            if frappe.db.exists('ONDC Product', {'item_code': item.name}):
                skipped += 1
                continue

            # Get full item doc
            item_doc = frappe.get_doc('Item', item.name)

            # Create ONDC Product
            create_ondc_product(item_doc, None)

            # Update sync status
            frappe.db.set_value('Item', item.name, {
                'ondc_sync_status': 'Synced',
                'ondc_last_synced': now_datetime()
            })

            synced += 1
            frappe.db.commit()

        except Exception as e:
            failed += 1
            frappe.db.set_value('Item', item.name, {
                'ondc_sync_status': 'Sync Failed'
            })
            frappe.log_error(
                f"Failed to sync Item {item.name}: {str(e)}",
                "ONDC Bulk Sync"
            )
            frappe.db.commit()

    message = f"ONDC Bulk Sync Complete: {synced} synced, {skipped} skipped, {failed} failed"
    frappe.msgprint(_(message))

    return {
        'synced': synced,
        'skipped': skipped,
        'failed': failed
    }


def enable_ondc_sync_for_item_group(item_group):
    """
    Enable ONDC sync for all items in an item group.

    Usage:
        from ondc_seller_app.utils.bulk_sync import enable_ondc_sync_for_item_group
        enable_ondc_sync_for_item_group('Grocery')
    """
    items = frappe.get_all(
        'Item',
        filters={
            'item_group': item_group,
            'disabled': 0
        },
        fields=['name']
    )

    count = 0
    for item in items:
        frappe.db.set_value('Item', item.name, 'sync_to_ondc', 1)
        count += 1

    frappe.db.commit()
    frappe.msgprint(_(f"Enabled ONDC sync for {count} items in {item_group}"))

    return count


def update_all_ondc_products():
    """
    Force update all existing ONDC Products from their source Items.
    Useful when Item data has changed and needs to be re-synced.
    """
    from ondc_seller_app.utils.item_hooks import update_ondc_product

    products = frappe.get_all('ONDC Product', fields=['name', 'item_code'])

    updated = 0
    failed = 0

    for product in products:
        try:
            if not product.item_code:
                continue

            item_doc = frappe.get_doc('Item', product.item_code)
            update_ondc_product(item_doc, None)

            frappe.db.set_value('Item', product.item_code, {
                'ondc_sync_status': 'Synced',
                'ondc_last_synced': now_datetime()
            })

            updated += 1
            frappe.db.commit()

        except Exception as e:
            failed += 1
            frappe.log_error(
                f"Failed to update ONDC Product {product.name}: {str(e)}",
                "ONDC Bulk Update"
            )
            frappe.db.commit()

    message = f"ONDC Bulk Update Complete: {updated} updated, {failed} failed"
    frappe.msgprint(_(message))

    return {
        'updated': updated,
        'failed': failed
    }


@frappe.whitelist()
def bulk_sync_items(item_group=None):
    """
    API endpoint for bulk syncing items.
    Can be called from a button in the UI.

    Args:
        item_group: Optional - only sync items from this group
    """
    frappe.only_for('System Manager')

    if item_group:
        enable_ondc_sync_for_item_group(item_group)

    return sync_all_items_to_ondc()
