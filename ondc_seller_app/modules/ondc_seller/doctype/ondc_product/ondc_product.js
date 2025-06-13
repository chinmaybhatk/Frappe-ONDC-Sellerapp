frappe.ui.form.on('ONDC Product', {
    refresh: function(frm) {
        if (!frm.is_new() && frm.doc.is_active) {
            frm.add_custom_button(__('Sync to ONDC'), function() {
                frappe.call({
                    method: 'sync_to_ondc',
                    doc: frm.doc,
                    callback: function(r) {
                        frm.reload_doc();
                    }
                });
            }, __('Actions'));
            
            frm.add_custom_button(__('Update Inventory'), function() {
                frappe.prompt([
                    {
                        label: 'Available Quantity',
                        fieldname: 'available_quantity',
                        fieldtype: 'Float',
                        default: frm.doc.available_quantity
                    }
                ], (values) => {
                    frm.set_value('available_quantity', values.available_quantity);
                    frm.save();
                });
            }, __('Actions'));
        }
        
        // Show sync status
        if (frm.doc.last_sync_date) {
            const indicator = frappe.datetime.get_diff(frappe.datetime.now_datetime(), frm.doc.last_sync_date) > 86400 ? 'orange' : 'green';
            frm.dashboard.add_indicator(__('Last Sync: {0}', [frappe.datetime.str_to_user(frm.doc.last_sync_date)]), indicator);
        }
    },
    
    item_code: function(frm) {
        if (frm.doc.item_code) {
            // Fetch item images
            frappe.call({
                method: 'frappe.client.get',
                args: {
                    doctype: 'Item',
                    name: frm.doc.item_code
                },
                callback: function(r) {
                    if (r.message && r.message.image) {
                        // Add default image
                        frm.add_child('images', {
                            image_url: r.message.image,
                            size_type: 'medium'
                        });
                        frm.refresh_field('images');
                    }
                }
            });
        }
    }
});