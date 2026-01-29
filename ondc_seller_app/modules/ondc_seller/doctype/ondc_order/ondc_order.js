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

        frm.dashboard.add_indicator(
            __('Status: {0}', [frm.doc.order_status]),
            status_color[frm.doc.order_status] || 'gray'
        );

        // Show fulfillment state
        if (frm.doc.fulfillment_state) {
            const fulfillment_color = {
                'Pending': 'orange',
                'Packed': 'blue',
                'Agent-assigned': 'blue',
                'At-pickup': 'blue',
                'Order-picked-up': 'blue',
                'Out-for-delivery': 'purple',
                'Order-delivered': 'green',
                'Delivery-failed': 'red',
                'Cancelled': 'red',
                'RTO-Initiated': 'orange',
                'RTO-Delivered': 'green',
                'RTO-Disposed': 'gray'
            };
            frm.dashboard.add_indicator(
                __('Fulfillment: {0}', [frm.doc.fulfillment_state]),
                fulfillment_color[frm.doc.fulfillment_state] || 'gray'
            );
        }

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

            // Update Order Status button
            if (frm.doc.order_status !== 'Completed' && frm.doc.order_status !== 'Cancelled') {
                frm.add_custom_button(__('Update Order Status'), function() {
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
                        }
                    ], (values) => {
                        frm.set_value('order_status', values.status);
                        frm.save();
                    }, __('Update Order Status'));
                }, __('Actions'));
            }

            // Update Fulfillment State button (granular ONDC states)
            if (frm.doc.order_status !== 'Completed' && frm.doc.order_status !== 'Cancelled') {
                frm.add_custom_button(__('Update Fulfillment'), function() {
                    const valid_next = {
                        'Pending': ['Packed', 'Cancelled'],
                        'Packed': ['Agent-assigned', 'Cancelled'],
                        'Agent-assigned': ['At-pickup', 'Cancelled'],
                        'At-pickup': ['Order-picked-up', 'Cancelled'],
                        'Order-picked-up': ['Out-for-delivery', 'Cancelled', 'RTO-Initiated'],
                        'Out-for-delivery': ['Order-delivered', 'Delivery-failed', 'RTO-Initiated'],
                        'Delivery-failed': ['Out-for-delivery', 'RTO-Initiated']
                    };

                    const current_state = frm.doc.fulfillment_state || 'Pending';
                    const next_states = valid_next[current_state] || [];

                    if (!next_states.length) {
                        frappe.msgprint(__('No further fulfillment transitions available'));
                        return;
                    }

                    frappe.prompt([
                        {
                            label: 'Fulfillment State',
                            fieldname: 'fulfillment_state',
                            fieldtype: 'Select',
                            options: next_states.join('\n'),
                            reqd: 1
                        },
                        {
                            label: 'Tracking URL',
                            fieldname: 'tracking_url',
                            fieldtype: 'Data',
                            description: 'Optional: URL for shipment tracking'
                        }
                    ], (values) => {
                        frm.set_value('fulfillment_state', values.fulfillment_state);
                        if (values.tracking_url) {
                            frm.set_value('tracking_url', values.tracking_url);
                        }
                        frm.save().then(() => {
                            frappe.call({
                                method: 'update_fulfillment_status',
                                doc: frm.doc,
                                args: {
                                    status: values.fulfillment_state,
                                    tracking_url: values.tracking_url || null
                                }
                            });
                        });
                    }, __('Update Fulfillment State'));
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
        if (!frm.is_new()) {
            frm.set_value('updated_at', frappe.datetime.now_datetime());
        }
    }
});
