import frappe
import json
from frappe import _
from werkzeug.wrappers import Response
import traceback

@frappe.whitelist(allow_guest=True)
def handle_webhook(api):
    """Handle incoming ONDC webhooks"""
    try:
        # Get request data
        data = frappe.request.get_json()
        
        # Log the webhook
        log = frappe.new_doc('ONDC Webhook Log')
        log.webhook_type = api
        log.request_id = data.get('context', {}).get('message_id')
        log.transaction_id = data.get('context', {}).get('transaction_id')
        log.message_id = data.get('context', {}).get('message_id')
        log.request_body = json.dumps(data, indent=2)
        log.status = 'Received'
        log.insert(ignore_permissions=True)
        
        # Route to appropriate handler
        handler_map = {
            'search': handle_search,
            'select': handle_select,
            'init': handle_init,
            'confirm': handle_confirm,
            'status': handle_status,
            'track': handle_track,
            'cancel': handle_cancel,
            'update': handle_update,
            'rating': handle_rating,
            'support': handle_support
        }
        
        handler = handler_map.get(api)
        if handler:
            response = handler(data)
            log.response_body = json.dumps(response, indent=2)
            log.status = 'Processed'
        else:
            response = {'error': f'Unknown webhook type: {api}'}
            log.status = 'Failed'
            log.error_message = response['error']
        
        log.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Return appropriate response
        if 'error' in response:
            return Response(json.dumps(response), status=400, mimetype='application/json')
        else:
            return Response(json.dumps({'message': {'ack': {'status': 'ACK'}}}), status=200, mimetype='application/json')
            
    except Exception as e:
        frappe.log_error(traceback.format_exc(), f"ONDC Webhook Error - {api}")
        return Response(json.dumps({'error': str(e)}), status=500, mimetype='application/json')

def handle_search(data):
    """Handle search request"""
    from ondc_seller_app.api.ondc_client import ONDCClient
    
    settings = frappe.get_single('ONDC Settings')
    client = ONDCClient(settings)
    
    return client.on_search(data)

def handle_select(data):
    """Handle select request"""
    from ondc_seller_app.api.ondc_client import ONDCClient
    
    settings = frappe.get_single('ONDC Settings')
    client = ONDCClient(settings)
    
    return client.on_select(data)

def handle_init(data):
    """Handle init request"""
    from ondc_seller_app.api.ondc_client import ONDCClient
    
    settings = frappe.get_single('ONDC Settings')
    client = ONDCClient(settings)
    
    return client.on_init(data)

def handle_confirm(data):
    """Handle confirm request and create order"""
    from ondc_seller_app.api.ondc_client import ONDCClient
    
    try:
        # Create ONDC Order
        order_data = data.get('message', {}).get('order', {})
        context = data.get('context', {})
        
        order = frappe.new_doc('ONDC Order')
        order.ondc_order_id = order_data.get('id')
        order.transaction_id = context.get('transaction_id')
        order.message_id = context.get('message_id')
        order.bap_id = context.get('bap_id')
        order.bap_uri = context.get('bap_uri')
        order.order_status = 'Pending'
        
        # Customer details
        billing = order_data.get('billing', {})
        order.customer_name = billing.get('name')
        order.customer_email = billing.get('email')
        order.customer_phone = billing.get('phone')
        
        # Billing address
        address = billing.get('address', {})
        order.billing_name = billing.get('name')
        order.billing_building = address.get('building')
        order.billing_locality = address.get('locality')
        order.billing_city = address.get('city')
        order.billing_state = address.get('state')
        order.billing_area_code = address.get('area_code')
        
        # Fulfillment details
        fulfillment = order_data.get('fulfillments', [{}])[0]
        order.fulfillment_id = fulfillment.get('id')
        order.fulfillment_type = fulfillment.get('type', 'Delivery')
        
        end_location = fulfillment.get('end', {}).get('location', {})
        order.shipping_gps = end_location.get('gps')
        order.shipping_address = json.dumps(end_location.get('address', {}))
        
        # Items
        for item_data in order_data.get('items', []):
            order.append('items', {
                'ondc_item_id': item_data.get('id'),
                'item_code': self.get_item_code_from_ondc_id(item_data.get('id')),
                'quantity': item_data.get('quantity', {}).get('count', 1),
                'price': float(item_data.get('price', {}).get('value', 0))
            })
        
        # Payment details
        payment = order_data.get('payment', {})
        order.payment_type = payment.get('type', 'Prepaid')
        order.payment_status = 'Pending' if payment.get('type') == 'Prepaid' else 'Pending'
        
        order.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Send confirmation response
        settings = frappe.get_single('ONDC Settings')
        client = ONDCClient(settings)
        return client.on_confirm(data)
        
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "Order Creation Error")
        return {'error': str(e)}

def handle_status(data):
    """Handle status request"""
    order_id = data.get('message', {}).get('order_id')
    
    try:
        order = frappe.get_doc('ONDC Order', {'ondc_order_id': order_id})
        
        # Build status response
        response = {
            'order': {
                'id': order.ondc_order_id,
                'state': order.order_status,
                'items': [],
                'fulfillments': [{
                    'id': order.fulfillment_id,
                    'state': {
                        'descriptor': {
                            'code': order.order_status
                        }
                    }
                }]
            }
        }
        
        return response
        
    except Exception as e:
        return {'error': f'Order not found: {order_id}'}

def handle_track(data):
    """Handle track request"""
    # Implementation for tracking
    return {'tracking': {'status': 'In Transit'}}

def handle_cancel(data):
    """Handle cancel request"""
    order_id = data.get('message', {}).get('order_id')
    
    try:
        order = frappe.get_doc('ONDC Order', {'ondc_order_id': order_id})
        order.order_status = 'Cancelled'
        order.save(ignore_permissions=True)
        frappe.db.commit()
        
        return {'order': {'id': order_id, 'status': 'Cancelled'}}
        
    except Exception as e:
        return {'error': f'Cancel failed: {str(e)}'}

def handle_update(data):
    """Handle update request"""
    # Implementation for order updates
    return {'status': 'Updated'}

def handle_rating(data):
    """Handle rating request"""
    # Implementation for ratings
    return {'status': 'Rating received'}

def handle_support(data):
    """Handle support request"""
    # Implementation for support
    return {
        'support': {
            'phone': '+91-9999999999',
            'email': 'support@example.com'
        }
    }

def get_item_code_from_ondc_id(ondc_id):
    """Get Frappe Item code from ONDC product ID"""
    product = frappe.db.get_value('ONDC Product', 
        {'ondc_product_id': ondc_id}, 'item_code')
    return product or ondc_id