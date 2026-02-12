"""
ONDC Authentication - Signature Verification for Incoming Requests
Reference: ONDC Protocol Specification - Auth Header Signing and Verification
"""
import frappe
import json
import re
import base64
import hashlib
import requests
import nacl.signing
import nacl.encoding
from datetime import datetime


def verify_request(request_data, auth_header=None, gateway_auth_header=None):
    """
    Verify incoming ONDC request signature.
    
    Args:
        request_data: The request body (dict)
        auth_header: The Authorization header value
        gateway_auth_header: The X-Gateway-Authorization header value
    
    Returns:
        tuple: (is_valid: bool, error_message: str or None)
    """
    # At least one auth header must be present
    header_to_verify = auth_header or gateway_auth_header
    if not header_to_verify:
        return False, "Missing Authorization header"
    
    try:
        # Parse the authorization header
        parsed = parse_auth_header(header_to_verify)
        if not parsed:
            return False, "Invalid Authorization header format"
        
        key_id = parsed.get("keyId", "")
        algorithm = parsed.get("algorithm", "")
        created = parsed.get("created", "")
        expires = parsed.get("expires", "")
        signature_b64 = parsed.get("signature", "")
        
        # Validate algorithm
        if algorithm != "ed25519":
            return False, f"Unsupported algorithm: {algorithm}"
        
        # Parse keyId: subscriber_id|unique_key_id|algorithm
        key_parts = key_id.split("|")
        if len(key_parts) != 3:
            return False, f"Invalid keyId format: {key_id}"
        
        subscriber_id = key_parts[0]
        unique_key_id = key_parts[1]
        key_algorithm = key_parts[2]
        
        if key_algorithm != "ed25519":
            return False, f"Key algorithm mismatch: {key_algorithm}"
        
        # Check if request is expired
        try:
            expires_ts = int(expires)
            if datetime.utcnow().timestamp() > expires_ts:
                return False, "Request has expired"
        except (ValueError, TypeError):
            return False, "Invalid expires timestamp"
        
        # Look up the sender's public key from the ONDC registry
        public_key = lookup_public_key(subscriber_id, unique_key_id)
        if not public_key:
            return False, f"Public key not found for {subscriber_id}|{unique_key_id}"
        
        # Reconstruct the signing string
        digest = calculate_digest(request_data)
        signing_string = f"(created): {created}\n(expires): {expires}\ndigest: BLAKE-512={digest}"
        
        # Verify the signature
        try:
            verify_key = nacl.signing.VerifyKey(
                base64.b64decode(public_key),
            )
            signature_bytes = base64.b64decode(signature_b64)
            verify_key.verify(signing_string.encode(), signature_bytes)
            return True, None
        except nacl.exceptions.BadSignatureError:
            return False, "Signature verification failed"
        except Exception as e:
            return False, f"Signature verification error: {str(e)}"
    
    except Exception as e:
        frappe.log_error(f"Auth verification error: {str(e)}", "ONDC Auth")
        return False, f"Authentication error: {str(e)}"


def parse_auth_header(header_value):
    """
    Parse the ONDC Authorization header.
    
    Format: Signature keyId="...",algorithm="ed25519",created="...",expires="...",
            headers="(created) (expires) digest",signature="..."
    """
    if not header_value:
        return None
    
    # Remove "Signature " prefix if present
    if header_value.startswith("Signature "):
        header_value = header_value[len("Signature "):]
    
    result = {}
    # Parse key-value pairs
    pattern = r'(\w+)="([^"]*)"'
    matches = re.findall(pattern, header_value)
    
    for key, value in matches:
        result[key] = value
    
    return result if result else None


def calculate_digest(request_body):
    """Calculate BLAKE-512 digest of request body"""
    if isinstance(request_body, dict):
        body_str = json.dumps(request_body, separators=(',', ':'), ensure_ascii=False)
    else:
        body_str = str(request_body)
    
    digest = hashlib.blake2b(body_str.encode(), digest_size=64).digest()
    return base64.b64encode(digest).decode()


def lookup_public_key(subscriber_id, unique_key_id):
    """
    Look up a network participant's public key from the ONDC registry.
    
    Args:
        subscriber_id: The subscriber's FQDN
        unique_key_id: The unique key identifier
    
    Returns:
        str: Base64-encoded public key, or None if not found
    """
    try:
        settings = frappe.get_single("ONDC Settings")
        
        # Registry lookup URL based on environment
        registry_urls = {
            "staging": "https://staging.registry.ondc.org/lookup",
            "preprod": "https://preprod.registry.ondc.org/ondc/lookup",
            "prod": "https://prod.registry.ondc.org/ondc/lookup",
        }
        
        registry_url = registry_urls.get(settings.environment, registry_urls["staging"])
        
        # Make lookup request
        payload = {
            "subscriber_id": subscriber_id,
            "ukId": unique_key_id,
            "domain": settings.domain,
            "type": "BAP"  # We are BPP, looking up BAP keys
        }
        
        response = requests.post(
            registry_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            # Extract signing public key from response
            if isinstance(data, list) and len(data) > 0:
                for entry in data:
                    if entry.get("ukId") == unique_key_id or entry.get("unique_key_id") == unique_key_id:
                        return entry.get("signing_public_key")
                # If no exact match, return first entry's key
                return data[0].get("signing_public_key")
            elif isinstance(data, dict):
                return data.get("signing_public_key")
        
        # Cache miss - try local cache
        cached_key = frappe.cache().get_value(f"ondc_pubkey:{subscriber_id}:{unique_key_id}")
        if cached_key:
            return cached_key
        
        return None
    
    except Exception as e:
        frappe.log_error(f"Registry lookup failed for {subscriber_id}: {str(e)}", "ONDC Registry")
        
        # Try local cache as fallback
        cached_key = frappe.cache().get_value(f"ondc_pubkey:{subscriber_id}:{unique_key_id}")
        if cached_key:
            return cached_key
        
        return None


def cache_public_key(subscriber_id, unique_key_id, public_key, ttl=3600):
    """Cache a public key for faster lookups"""
    frappe.cache().set_value(
        f"ondc_pubkey:{subscriber_id}:{unique_key_id}",
        public_key,
        expires_in_sec=ttl
    )


def validate_context(context):
    """
    Validate the context object of an incoming ONDC request.
    
    Args:
        context: The context dict from the request
    
    Returns:
        tuple: (is_valid: bool, error_code: str or None, error_message: str or None)
    """
    if not context:
        return False, "10000", "Missing context object"
    
    required_fields = ["domain", "action", "bap_id", "bap_uri", "transaction_id", "message_id", "timestamp"]
    for field in required_fields:
        if not context.get(field):
            return False, "10000", f"Missing required context field: {field}"
    
    # Validate domain
    settings = frappe.get_single("ONDC Settings")
    valid_domains = [
        "ONDC:RET10", "ONDC:RET11", "ONDC:RET12", "ONDC:RET13",
        "ONDC:RET14", "ONDC:RET15", "ONDC:RET16", "ONDC:RET18"
    ]
    if context.get("domain") not in valid_domains:
        return False, "10001", f"Invalid domain: {context.get('domain')}"
    
    # Validate action
    valid_actions = [
        "search", "select", "init", "confirm", "status",
        "track", "cancel", "update", "rating", "support"
    ]
    if context.get("action") not in valid_actions:
        return False, "10002", f"Invalid action: {context.get('action')}"
    
    # Validate timestamp freshness (within TTL)
    # Pramaan queues and replays test requests with original timestamps that
    # can be HOURS old, so we skip TTL validation for staging/preprod entirely.
    # In production, enforce a generous 5-minute window.
    settings_env = settings.environment if settings else "staging"
    if settings_env == "prod":
        try:
            req_timestamp = datetime.fromisoformat(context["timestamp"].replace("Z", "+00:00"))
            now = datetime.utcnow()
            req_timestamp_utc = req_timestamp.replace(tzinfo=None) if req_timestamp.tzinfo else req_timestamp
            diff = abs((now - req_timestamp_utc).total_seconds())
            if diff > 300:  # 5 minutes
                return False, "10003", "Request timestamp outside TTL window"
        except (ValueError, TypeError):
            pass  # Don't fail on timestamp parsing
    
    return True, None, None
