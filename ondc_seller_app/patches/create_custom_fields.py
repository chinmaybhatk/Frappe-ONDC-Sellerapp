import frappe
import json
import os

def execute():
    """Create custom fields for ONDC app"""
    
    # Get the path to custom fields JSON
    app_path = frappe.get_app_path('ondc_seller_app')
    custom_fields_path = os.path.join(app_path, 'setup', 'custom_fields.json')
    
    if not os.path.exists(custom_fields_path):
        frappe.log_error("Custom fields file not found", "ONDC Setup")
        return
    
    # Read the custom fields
    with open(custom_fields_path, 'r') as f:
        custom_fields = json.load(f)
    
    # Create each custom field
    for field_data in custom_fields:
        try:
            # Check if custom field already exists
            if not frappe.db.exists('Custom Field', field_data.get('name')):
                # Remove the name field for creation
                field_name = field_data.pop('name', None)
                
                # Create the custom field
                cf = frappe.get_doc(field_data)
                cf.insert(ignore_permissions=True)
                
                # Update with the correct name if provided
                if field_name and cf.name != field_name:
                    frappe.rename_doc('Custom Field', cf.name, field_name, force=True)
                
                print(f"Created custom field: {field_name or cf.name}")
            else:
                print(f"Custom field already exists: {field_data.get('name')}")
                
        except Exception as e:
            frappe.log_error(f"Failed to create custom field: {field_data}\nError: {str(e)}", "ONDC Setup")
    
    frappe.db.commit()
    print("Custom fields creation completed")