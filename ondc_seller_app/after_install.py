import frappe
import json
import os

def after_install():
    """Run after app installation"""
    print("Running ONDC Seller App post-installation setup...")
    
    # First, remove any existing fixture files that might cause conflicts
    cleanup_fixtures()
    
    # Create custom fields manually
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
        customer_group.insert(ignore_permissions=True)
        
        # Set the custom field after creation
        frappe.db.set_value('Customer Group', 'ONDC Customers', 'is_ondc_group', 1)
        print("Created ONDC Customer Group")
    
    frappe.db.commit()
    print("ONDC Seller App installation completed successfully!")

def cleanup_fixtures():
    """Remove any fixture files that might cause installation issues"""
    try:
        app_path = frappe.get_app_path('ondc_seller_app')
        fixtures_path = os.path.join(app_path, 'fixtures')
        custom_fields_path = os.path.join(fixtures_path, 'custom_fields.json')
        
        if os.path.exists(custom_fields_path):
            os.remove(custom_fields_path)
            print(f"Removed fixture file: {custom_fields_path}")
    except Exception as e:
        print(f"Could not remove fixture file: {str(e)}")

def create_custom_fields():
    """Create custom fields for ONDC app"""
    
    custom_fields = [
        # Item fields
        {
            "doctype": "Custom Field",
            "dt": "Item",
            "fieldname": "sync_to_ondc",
            "fieldtype": "Check",
            "label": "Sync to ONDC",
            "insert_after": "is_stock_item",
            "description": "Enable to sync this item to ONDC network"
        },
        {
            "doctype": "Custom Field",
            "dt": "Item",
            "fieldname": "ondc_section",
            "fieldtype": "Section Break",
            "label": "ONDC Details",
            "insert_after": "website_section",
            "collapsible": 1
        },
        {
            "doctype": "Custom Field",
            "dt": "Item",
            "fieldname": "ondc_product_id",
            "fieldtype": "Data",
            "label": "ONDC Product ID",
            "insert_after": "ondc_section",
            "read_only": 1
        },
        {
            "doctype": "Custom Field",
            "dt": "Item",
            "fieldname": "country_of_origin",
            "fieldtype": "Link",
            "label": "Country of Origin",
            "options": "Country",
            "insert_after": "ondc_product_id",
            "default": "India"
        },
        # Sales Order fields
        {
            "doctype": "Custom Field",
            "dt": "Sales Order",
            "fieldname": "ondc_order_id",
            "fieldtype": "Data",
            "label": "ONDC Order ID",
            "insert_after": "po_no",
            "read_only": 1
        },
        # Customer fields
        {
            "doctype": "Custom Field",
            "dt": "Customer",
            "fieldname": "is_ondc_customer",
            "fieldtype": "Check",
            "label": "Is ONDC Customer",
            "insert_after": "customer_type",
            "read_only": 1
        },
        # Customer Group fields
        {
            "doctype": "Custom Field",
            "dt": "Customer Group",
            "fieldname": "is_ondc_group",
            "fieldtype": "Check",
            "label": "Is ONDC Group",
            "insert_after": "is_group",
            "description": "Check if this is the default group for ONDC customers"
        }
    ]
    
    for field_data in custom_fields:
        try:
            # Check if custom field already exists
            exists = frappe.db.exists('Custom Field', {
                'dt': field_data['dt'],
                'fieldname': field_data['fieldname']
            })
            
            if not exists:
                cf = frappe.get_doc(field_data)
                cf.insert(ignore_permissions=True)
                print(f"Created custom field: {field_data['dt']}-{field_data['fieldname']}")
            else:
                print(f"Custom field already exists: {field_data['dt']}-{field_data['fieldname']}")
                
        except Exception as e:
            frappe.log_error(f"Failed to create custom field: {field_data}\nError: {str(e)}", "ONDC Setup")