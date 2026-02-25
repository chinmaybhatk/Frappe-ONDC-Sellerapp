import frappe
import json
import time
from frappe import _
from werkzeug.wrappers import Response
import traceback
from datetime import datetime, timedelta

from ondc_seller_app.api.auth import verify_request, validate_context
from ondc_seller_app.api.ondc_errors import (
    build_ack_response,
    build_nack_response,
    build_error,
    get_cancellation_reason,
    CANCELLATION_REASONS,
    FULFILLMENT_STATES,
    VALID_FULFILLMENT_TRANSITIONS,
    is_valid_fulfillment_transition,
)


# Fulfillment state progression for Pramaan auto-testing.
# Each /status call advances the order one step through this lifecycle.
FULFILLMENT_PROGRESSION = [
    "Pending",
    "Packed",
    "Agent-assigned",
    "At-pickup",
    "Order-picked-up",
    "Out-for-delivery",
    "Order-delivered",
]