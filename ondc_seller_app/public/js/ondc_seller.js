// ONDC Seller App Client Scripts

frappe.provide('ondc_seller_app');

// Add ONDC button to Item form
frappe.ui.form.on('Item', {
    refresh: function(frm) {
        if (!frm.is_new() && frm.doc.sync_to_ondc) {
            frm.add_custom_button(__('View ONDC Product'), function() {
                frappe.db.get_value('ONDC Product', 
                    {'item_code': frm.doc.name}, 
                    'name',
                    function(r) {
                        if (r.name) {
                            frappe.set_route('Form', 'ONDC Product', r.name);
                        } else {
                            frappe.msgprint(__('ONDC Product not found'));
                        }
                    }
                );
            }, __('ONDC'));
        }
    }
});

// Add ONDC indicators to Sales Order
frappe.ui.form.on('Sales Order', {
    refresh: function(frm) {
        if (frm.doc.po_no && frm.doc.po_no.startsWith('ONDC')) {
            frm.dashboard.add_indicator(__('ONDC Order'), 'blue');
            
            frm.add_custom_button(__('View ONDC Order'), function() {
                frappe.db.get_value('ONDC Order', 
                    {'ondc_order_id': frm.doc.po_no}, 
                    'name',
                    function(r) {
                        if (r.name) {
                            frappe.set_route('Form', 'ONDC Order', r.name);
                        }
                    }
                );
            }, __('ONDC'));
        }
    }
});