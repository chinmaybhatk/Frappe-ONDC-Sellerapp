"""
ONDC Protocol Error Codes and Helpers
Reference: ONDC Protocol Specification v1.2.0
"""

# ONDC Error Types
CONTEXT_ERROR = "CONTEXT-ERROR"
CORE_ERROR = "CORE-ERROR"
DOMAIN_ERROR = "DOMAIN-ERROR"
POLICY_ERROR = "POLICY-ERROR"

# Error codes
ERRORS = {
    # Context errors (10xxx)
    "10000": {"type": CONTEXT_ERROR, "message": "Invalid request context"},
    "10001": {"type": CONTEXT_ERROR, "message": "Invalid domain"},
    "10002": {"type": CONTEXT_ERROR, "message": "Invalid action"},
    "10003": {"type": CONTEXT_ERROR, "message": "Invalid timestamp - stale request"},
    
    # Core errors (20xxx)
    "20000": {"type": CORE_ERROR, "message": "Invalid request"},
    "20001": {"type": CORE_ERROR, "message": "Invalid signature"},
    "20002": {"type": CORE_ERROR, "message": "Stale request"},
    "20003": {"type": CORE_ERROR, "message": "Invalid response"},
    "20004": {"type": CORE_ERROR, "message": "Request timed out"},
    "20005": {"type": CORE_ERROR, "message": "Schema validation failed"},
    "20006": {"type": CORE_ERROR, "message": "Signing algorithm mismatch"},
    
    # Domain errors (30xxx) - Order related
    "30000": {"type": DOMAIN_ERROR, "message": "Provider not found"},
    "30001": {"type": DOMAIN_ERROR, "message": "Provider location not found"},
    "30004": {"type": DOMAIN_ERROR, "message": "Item not found"},
    "30005": {"type": DOMAIN_ERROR, "message": "Category not found"},
    "30006": {"type": DOMAIN_ERROR, "message": "Item out of stock"},
    "30007": {"type": DOMAIN_ERROR, "message": "Item quantity exceeds available stock"},
    "30008": {"type": DOMAIN_ERROR, "message": "Item price has changed"},
    "30009": {"type": DOMAIN_ERROR, "message": "Fulfillment service unavailable"},
    "30010": {"type": DOMAIN_ERROR, "message": "Order not found"},
    "30011": {"type": DOMAIN_ERROR, "message": "Order cannot be updated"},
    "30012": {"type": DOMAIN_ERROR, "message": "Order cannot be cancelled"},
    "30013": {"type": DOMAIN_ERROR, "message": "Cancellation reason not valid"},
    "30014": {"type": DOMAIN_ERROR, "message": "Payment failed"},
    "30015": {"type": DOMAIN_ERROR, "message": "Quote has expired"},
    "30016": {"type": DOMAIN_ERROR, "message": "Order confirmation failed"},
    "30017": {"type": DOMAIN_ERROR, "message": "Invalid fulfillment state transition"},
    "30018": {"type": DOMAIN_ERROR, "message": "Rating value out of range"},
    
    # Policy errors (40xxx)
    "40000": {"type": POLICY_ERROR, "message": "Business policy error"},
    "40001": {"type": POLICY_ERROR, "message": "Cancellation not permitted"},
    "40002": {"type": POLICY_ERROR, "message": "Return not permitted"},
    "40003": {"type": POLICY_ERROR, "message": "Update not permitted"},
}

# ONDC Cancellation Reason Codes
CANCELLATION_REASONS = {
    # Buyer cancellation reasons
    "001": "Price of one or more items has changed",
    "002": "One or more items in the Order not available",
    "003": "Product available at lower than order price",
    "004": "Order in pending shipment / delivery state for too long",
    "005": "Merchant rejected the order",
    "006": "Order not received as per the expected date of delivery",
    "007": "No response from seller",
    "008": "Delivery address not serviceable",
    "009": "Duplicate order",
    "010": "Changed my mind",
    "011": "Buyer wants to modify details",
    "012": "Buyer not available at the time of delivery",
    "013": "Wrong product delivered",
    "014": "Quality not as expected",
    "015": "Delayed delivery",
    
    # Seller cancellation reasons
    "501": "Merchant rejected order",
    "502": "Item(s) out of stock",
    "503": "Cannot service delivery area",
    "504": "Order cannot be fulfilled",
    "505": "Store closed",
    "506": "Incorrect pricing",
    
    # Logistics cancellation reasons
    "901": "Order delivery delayed",
    "902": "Delivery agent could not reach pickup location",
    "903": "Delivery agent could not reach delivery location",
    "904": "Buyer not available",
    "905": "Address incorrect",
}

# Fulfillment States (granular)
FULFILLMENT_STATES = {
    "Pending": "Pending",
    "Packed": "Packed",
    "Agent-assigned": "Agent-assigned",
    "At-pickup": "At-pickup",
    "Order-picked-up": "Order-picked-up",
    "Out-for-delivery": "Out-for-delivery",
    "Order-delivered": "Order-delivered",
    "Delivery-failed": "Delivery-failed",
    "Cancelled": "Cancelled",
    "RTO-Initiated": "RTO-Initiated",
    "RTO-Delivered": "RTO-Delivered",
    "RTO-Disposed": "RTO-Disposed",
}

# Valid fulfillment state transitions
VALID_FULFILLMENT_TRANSITIONS = {
    "Pending": ["Packed", "Cancelled"],
    "Packed": ["Agent-assigned", "Cancelled"],
    "Agent-assigned": ["At-pickup", "Cancelled"],
    "At-pickup": ["Order-picked-up", "Cancelled"],
    "Order-picked-up": ["Out-for-delivery", "Cancelled", "RTO-Initiated"],
    "Out-for-delivery": ["Order-delivered", "Delivery-failed", "RTO-Initiated"],
    "Delivery-failed": ["Out-for-delivery", "RTO-Initiated"],
    "Order-delivered": [],
    "Cancelled": [],
    "RTO-Initiated": ["RTO-Delivered", "RTO-Disposed"],
    "RTO-Delivered": [],
    "RTO-Disposed": [],
}


def build_error(code, custom_message=None):
    """Build ONDC error object from error code"""
    error_info = ERRORS.get(str(code), {
        "type": DOMAIN_ERROR,
        "message": "Unknown error"
    })
    
    return {
        "type": error_info["type"],
        "code": str(code),
        "message": custom_message or error_info["message"]
    }


def build_nack_response(code, custom_message=None):
    """Build a NACK response with error details"""
    return {
        "message": {
            "ack": {
                "status": "NACK"
            }
        },
        "error": build_error(code, custom_message)
    }


def build_ack_response():
    """Build a standard ACK response"""
    return {
        "message": {
            "ack": {
                "status": "ACK"
            }
        }
    }


def get_cancellation_reason(code):
    """Get cancellation reason text from code"""
    return CANCELLATION_REASONS.get(str(code), "Unknown cancellation reason")


def is_valid_fulfillment_transition(current_state, new_state):
    """Check if a fulfillment state transition is valid"""
    valid_next = VALID_FULFILLMENT_TRANSITIONS.get(current_state, [])
    return new_state in valid_next
