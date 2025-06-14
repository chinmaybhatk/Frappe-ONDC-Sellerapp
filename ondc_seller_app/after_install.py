import frappe
from .patches.create_custom_fields import execute as create_custom_fields

def after_install():
    """Run after app installation"""
    print("Running ONDC Seller App post-installation setup...")
    
    # Create custom fields
    create_custom_fields()
    
    # Create default ONDC Settings if not exists
    if not frappe.db.exists('ONDC Settings', 'ONDC Settings'):
        settings = frappe.new_doc('ONDC Settings')
        settings.auto_sync_inventory = 1
        settings.auto_sync_orders = 1
        settings.insert(ignore_permissions=True)
        print("Created default ONDC Settings")
    
    # Create ONDC Customer Group if not exists
    if not frappe.db.exists('Customer Group', 'ONDC Customers'):
        customer_group = frappe.new_doc('Customer Group')
        customer_group.customer_group_name = 'ONDC Customers'
        customer_group.parent_customer_group = 'All Customer Groups'
        customer_group.is_ondc_group = 1
        customer_group.insert(ignore_permissions=True)
        print("Created ONDC Customer Group")
    
    frappe.db.commit()
    print("ONDC Seller App installation completed successfully!")