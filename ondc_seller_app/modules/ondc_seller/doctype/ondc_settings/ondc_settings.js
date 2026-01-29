frappe.ui.form.on('ONDC Settings', {
    refresh: function(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button(__('Register on Network'), function() {
                frappe.call({
                    method: 'register_on_network',
                    doc: frm.doc,
                    callback: function(r) {
                        frm.reload_doc();
                    }
                });
            });

            frm.add_custom_button(__('Test Connection'), function() {
                frappe.call({
                    method: 'ondc_seller_app.api.ondc_client.test_connection',
                    args: {
                        environment: frm.doc.environment
                    },
                    callback: function(r) {
                        if (r.message.success) {
                            frappe.msgprint(__('Connection successful!'));
                        } else {
                            frappe.msgprint(__('Connection failed: ') + r.message.error);
                        }
                    }
                });
            });

            frm.add_custom_button(__('Generate Key Pairs'), function() {
                frappe.confirm(
                    __('This will generate new Ed25519 signing and X25519 encryption key pairs. Existing keys will be overwritten. Continue?'),
                    function() {
                        frappe.call({
                            method: 'generate_keys',
                            doc: frm.doc,
                            callback: function(r) {
                                frm.reload_doc();
                            }
                        });
                    }
                );
            });
        }
    },

    environment: function(frm) {
        if (frm.doc.webhook_url) {
            frm.set_value('webhook_url', '');
        }
    }
});
