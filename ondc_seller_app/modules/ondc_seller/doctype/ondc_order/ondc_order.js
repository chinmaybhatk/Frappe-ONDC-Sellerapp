frappe.ui.form.on('ONDC Order', {
    refresh: function(frm) {
        // Add status indicators
        const status_color = {
            'Pending': 'orange',
            'Accepted': 'blue',
            'In-progress': 'blue',
            'Completed': 'green',
            'Cancelled': 'red'
        };
        
        frm.dashboard.add_indicator(__('Status: {0}', [frm.doc.order_status]), status_color[frm.doc.order_status] || 'gray');
        
        if (!frm.is_new()) {
            // Create Sales Order button
            if (!frm.doc.sales_order && frm.doc.order_status === 'Accepted') {
                frm.add_custom_button(__('Create Sales Order'), function() {
                    frappe.call({
                        method: 'create_sales_order',
                        doc: frm.doc,
                        callback: function(r) {
                            if (r.message) {
                                frappe.set_route('Form', 'Sales Order', r.message);
                            }
                        }
                    });
                }, __('Actions'));
            }
            
            // Update Status button
            if (frm.doc.order_status !== 'Completed' && frm.doc.order_status !== 'Cancelled') {
                frm.add_custom_button(__('Update Status'), function() {
                    const next_status = {
                        'Pending': ['Accepted', 'Cancelled'],
                        'Accepted': ['In-progress', 'Cancelled'],
                        'In-progress': ['Completed', 'Cancelled']
                    };
                    
                    frappe.prompt([
                        {
                            label: 'New Status',
                            fieldname: 'status',
                            fieldtype: 'Select',
                            options: next_status[frm.doc.order_status].join('\n'),
                            reqd: 1
                        },
                        {
                            label: 'Tracking URL',
                            fieldname: 'tracking_url',
                            fieldtype: 'Data',
                            depends_on: "eval:doc.status=='In-progress'"
                        }
                    ], (values) => {
                        frm.set_value('order_status', values.status);
                        frm.save().then(() => {
                            if (values.tracking_url) {
                                frappe.call({
                                    method: 'update_fulfillment_status',
                                    doc: frm.doc,
                                    args: {
                                        status: values.status,
                                        tracking_url: values.tracking_url
                                    }
                                });
                            }
                        });
                    });
                }, __('Actions'));
            }
            
            // View Sales Order button
            if (frm.doc.sales_order) {
                frm.add_custom_button(__('View Sales Order'), function() {
                    frappe.set_route('Form', 'Sales Order', frm.doc.sales_order);
                }, __('Actions'));
            }
        }
    },
    
    order_status: function(frm) {
        // Update timestamp on status change
        if (!frm.is_new()) {
            frm.set_value('updated_at', frappe.datetime.now_datetime());
        }
    }
});