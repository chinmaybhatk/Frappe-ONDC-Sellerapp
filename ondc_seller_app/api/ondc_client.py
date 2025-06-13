import frappe
import requests
import json
import nacl.signing
import nacl.encoding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519
import base64
from datetime import datetime, timedelta
import hashlib

class ONDCClient:
    def __init__(self, settings):
        self.settings = settings
        self.base_urls = {
            'staging': {
                'registry': 'https://staging.registry.ondc.org',
                'gateway': 'https://pilot-gateway-1.beckn.nsdl.co.in'
            },
            'preprod': {
                'registry': 'https://preprod.registry.ondc.org',
                'gateway': 'https://preprod.gateway.ondc.org'
            },
            'prod': {
                'registry': 'https://prod.registry.ondc.org',
                'gateway': 'https://prod.gateway.ondc.org'
            }
        }
    
    def get_auth_header(self, request_body):
        """Generate authorization header for ONDC requests"""
        created = int(datetime.utcnow().timestamp())
        expires = int((datetime.utcnow() + timedelta(minutes=5)).timestamp())
        
        # Create the signing string
        signing_string = f"(created): {created}\n(expires): {expires}\ndigest: BLAKE-512={self._calculate_digest(request_body)}"
        
        # Sign the string
        private_key = nacl.signing.SigningKey(self.settings.signing_private_key, encoder=nacl.encoding.Base64Encoder)
        signature = private_key.sign(signing_string.encode()).signature
        signature_b64 = base64.b64encode(signature).decode()
        
        # Create auth header
        auth_header = f'Signature keyId="{self.settings.unique_key_id}|{self.settings.subscriber_id}|ed25519",'
        auth_header += f'algorithm="ed25519",created="{created}",expires="{expires}",'
        auth_header += f'headers="(created) (expires) digest",signature="{signature_b64}"'
        
        return auth_header
    
    def _calculate_digest(self, request_body):
        """Calculate BLAKE-512 digest of request body"""
        body_str = json.dumps(request_body, separators=(',', ':'), ensure_ascii=False)
        digest = hashlib.blake2b(body_str.encode(), digest_size=64).digest()
        return base64.b64encode(digest).decode()
    
    def make_request(self, endpoint, method, data=None, use_gateway=False):
        """Make HTTP request to ONDC network"""
        env = self.settings.environment
        base_url = self.base_urls[env]['gateway' if use_gateway else 'registry']
        url = f"{base_url}{endpoint}"
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        if data:
            headers['Authorization'] = self.get_auth_header(data)
        
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                timeout=30
            )
            
            response.raise_for_status()
            return {'success': True, 'data': response.json()}
            
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"ONDC API Error: {str(e)}", "ONDC Client")
            return {'success': False, 'error': str(e)}
    
    def subscribe(self):
        """Register participant on ONDC network"""
        payload = {
            "context": {
                "operation": {
                    "ops_no": 1
                }
            },
            "message": {
                "request_id": frappe.generate_hash(length=16),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "entity": {
                    "gst": {
                        "legal_entity_name": self.settings.legal_entity_name,
                        "business_id": self.settings.gst_no,
                        "city_code": self.settings.city
                    },
                    "pan": {
                        "name_as_per_pan": self.settings.legal_entity_name,
                        "pan_no": self.settings.pan_no,
                        "date_of_incorporation": "01/01/2020"
                    }
                },
                "network_participant": [
                    {
                        "subscriber_id": self.settings.subscriber_id,
                        "subscriber_url": self.settings.subscriber_url,
                        "domain": self.settings.domain,
                        "type": self.settings.participant_type,
                        "msn": False,
                        "city_code": self.settings.city
                    }
                ],
                "key_pair": {
                    "signing_public_key": self.settings.signing_public_key,
                    "encryption_public_key": self.settings.encryption_public_key,
                    "valid_from": datetime.utcnow().isoformat() + "Z",
                    "valid_until": (datetime.utcnow() + timedelta(days=365)).isoformat() + "Z"
                },
                "callback_url": self.settings.webhook_url
            }
        }
        
        return self.make_request('/subscribe', 'POST', payload)
    
    def on_search(self, search_request):
        """Send catalog response"""
        context = self.create_context('on_search', search_request.get('context'))
        catalog = self.build_catalog()
        
        payload = {
            "context": context,
            "message": {
                "catalog": catalog
            }
        }
        
        return self.send_callback(search_request.get('context', {}).get('bap_uri'), '/on_search', payload)
    
    def on_select(self, select_request):
        """Send quote response"""
        context = self.create_context('on_select', select_request.get('context'))
        order = self.calculate_quote(select_request.get('message', {}).get('order', {}))
        
        payload = {
            "context": context,
            "message": {
                "order": order
            }
        }
        
        return self.send_callback(select_request.get('context', {}).get('bap_uri'), '/on_select', payload)
    
    def on_init(self, init_request):
        """Send payment terms response"""
        context = self.create_context('on_init', init_request.get('context'))
        order = self.add_payment_terms(init_request.get('message', {}).get('order', {}))
        
        payload = {
            "context": context,
            "message": {
                "order": order
            }
        }
        
        return self.send_callback(init_request.get('context', {}).get('bap_uri'), '/on_init', payload)
    
    def on_confirm(self, confirm_request):
        """Send order confirmation"""
        context = self.create_context('on_confirm', confirm_request.get('context'))
        order = self.create_order(confirm_request.get('message', {}).get('order', {}))
        
        payload = {
            "context": context,
            "message": {
                "order": order
            }
        }
        
        return self.send_callback(confirm_request.get('context', {}).get('bap_uri'), '/on_confirm', payload)
    
    def create_context(self, action, request_context=None):
        """Create context object for response"""
        context = {
            "domain": self.settings.domain,
            "country": "IND",
            "city": self.settings.city,
            "action": action,
            "core_version": "1.2.0",
            "bap_id": request_context.get('bap_id') if request_context else None,
            "bap_uri": request_context.get('bap_uri') if request_context else None,
            "bpp_id": self.settings.subscriber_id,
            "bpp_uri": self.settings.subscriber_url,
            "transaction_id": request_context.get('transaction_id') if request_context else frappe.generate_hash(length=16),
            "message_id": frappe.generate_hash(length=16),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "ttl": "PT30S"
        }
        return context
    
    def build_catalog(self):
        """Build product catalog from ONDC Products"""
        products = frappe.get_all(
            'ONDC Product',
            filters={'is_active': 1},
            fields=['*']
        )
        
        items = []
        for product in products:
            doc = frappe.get_doc('ONDC Product', product.name)
            items.append(doc.get_ondc_format())
        
        catalog = {
            "bpp/descriptor": {
                "name": self.settings.legal_entity_name or "ONDC Seller",
                "short_desc": "Quality products at best prices",
                "long_desc": "We provide a wide range of products with fast delivery",
                "images": []
            },
            "bpp/providers": [
                {
                    "id": self.settings.subscriber_id,
                    "descriptor": {
                        "name": self.settings.legal_entity_name or "ONDC Seller",
                        "short_desc": "Quality products at best prices",
                        "long_desc": "We provide a wide range of products with fast delivery",
                        "images": []
                    },
                    "locations": [
                        {
                            "id": f"LOC-{self.settings.city}",
                            "gps": "12.9715987,77.5945627",
                            "address": {
                                "locality": "Main Street",
                                "city": self.settings.city,
                                "state": "Karnataka",
                                "country": "IND",
                                "area_code": self.settings.city
                            }
                        }
                    ],
                    "items": items
                }
            ]
        }
        
        return catalog
    
    def calculate_quote(self, order):
        """Calculate quote for selected items"""
        # Implementation for quote calculation
        return order
    
    def add_payment_terms(self, order):
        """Add payment terms to order"""
        # Implementation for payment terms
        return order
    
    def create_order(self, order_data):
        """Create ONDC Order from confirm request"""
        # Implementation for order creation
        return order_data
    
    def send_callback(self, callback_url, endpoint, payload):
        """Send callback to BAP"""
        if not callback_url:
            return {'success': False, 'error': 'No callback URL provided'}
        
        url = f"{callback_url}{endpoint}"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': self.get_auth_header(payload)
        }
        
        try:
            response = requests.post(
                url=url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            response.raise_for_status()
            return {'success': True, 'data': response.json()}
            
        except requests.exceptions.RequestException as e:
            frappe.log_error(f"ONDC Callback Error: {str(e)}", "ONDC Client")
            return {'success': False, 'error': str(e)}

@frappe.whitelist()
def test_connection(environment):
    """Test connection to ONDC network"""
    urls = {
        'staging': 'https://staging.registry.ondc.org/health',
        'preprod': 'https://preprod.registry.ondc.org/health',
        'prod': 'https://prod.registry.ondc.org/health'
    }
    
    try:
        response = requests.get(urls[environment], timeout=10)
        return {'success': response.status_code == 200}
    except:
        return {'success': False, 'error': 'Connection failed'}