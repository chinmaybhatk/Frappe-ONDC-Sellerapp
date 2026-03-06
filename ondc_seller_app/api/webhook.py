import frappe
import json
import time
from frappe import _
from werkzeug.wrappers import Response
import traceback
from datetime import datetime, timedelta

from ondc_seller_app.api.auth import verify_request
from ondc_seller_app.api.ondc_client import ONDCClient


def verify_auth_header(auth_header, request_body):
    """Wrapper for backward compatibility."""
    try:
        request_data = json.loads(request_body) if isinstance(request_body, str) else request_body
        is_valid, error = verify_request(request_data, auth_header=auth_header)
        return is_valid
    except Exception:
        return False


def get_ondc_settings():
    """Get ONDC settings from Frappe."""
    try:
        settings = frappe.get_single("ONDC Settings")
        return settings
    except Exception:
        return None


def send_nack(message="Invalid request"):
    """Send NACK response."""
    response_data = {
        "message": {
            "ack": {
                "status": "NACK"
            }
        },
        "error": {
            "type": "DOMAIN-ERROR",
            "code": "20000",
            "message": message
        }
    }
    return Response(
        json.dumps(response_data),
        status=200,
        mimetype="application/json"
    )


def send_ack():
    """Send ACK response."""
    response_data = {
        "message": {
            "ack": {
                "status": "ACK"
            }
        }
    }
    return Response(
        json.dumps(response_data),
        status=200,
        mimetype="application/json"
    )


@frappe.whitelist(allow_guest=True)
def search():
    """Handle /search request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized search request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"Search request: {json.dumps(data)}")

        # Process search
        try:
            client = ONDCClient(settings)
            result = client.search(data)
            frappe.logger("webhooks").info(f"Search processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing search: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing search: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in search endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def on_search():
    """Handle /on_search callback from BPP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized on_search request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"on_search request: {json.dumps(data)}")

        # Process on_search
        try:
            client = ONDCClient(settings)
            result = client.on_search(data)
            frappe.logger("webhooks").info(f"on_search processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing on_search: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing on_search: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in on_search endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def select():
    """Handle /select request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized select request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"select request: {json.dumps(data)}")

        # Process select
        try:
            client = ONDCClient(settings)
            result = client.select(data)
            frappe.logger("webhooks").info(f"select processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing select: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing select: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in select endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def on_select():
    """Handle /on_select callback from BPP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized on_select request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"on_select request: {json.dumps(data)}")

        # Process on_select
        try:
            client = ONDCClient(settings)
            result = client.on_select(data)
            frappe.logger("webhooks").info(f"on_select processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing on_select: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing on_select: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in on_select endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def init():
    """Handle /init request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized init request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"init request: {json.dumps(data)}")

        # Process init
        try:
            client = ONDCClient(settings)
            result = client.init(data)
            frappe.logger("webhooks").info(f"init processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing init: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing init: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in init endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def on_init():
    """Handle /on_init callback from BPP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized on_init request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"on_init request: {json.dumps(data)}")

        # Process on_init
        try:
            client = ONDCClient(settings)
            result = client.on_init(data)
            frappe.logger("webhooks").info(f"on_init processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing on_init: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing on_init: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in on_init endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def confirm():
    """Handle /confirm request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized confirm request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"confirm request: {json.dumps(data)}")

        # Process confirm
        try:
            client = ONDCClient(settings)
            result = client.confirm(data)
            frappe.logger("webhooks").info(f"confirm processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing confirm: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing confirm: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in confirm endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def on_confirm():
    """Handle /on_confirm callback from BPP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized on_confirm request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"on_confirm request: {json.dumps(data)}")

        # Process on_confirm
        try:
            client = ONDCClient(settings)
            result = client.on_confirm(data)
            frappe.logger("webhooks").info(f"on_confirm processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing on_confirm: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing on_confirm: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in on_confirm endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def track():
    """Handle /track request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized track request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"track request: {json.dumps(data)}")

        # Process track
        try:
            client = ONDCClient(settings)
            result = client.track(data)
            frappe.logger("webhooks").info(f"track processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing track: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing track: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in track endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def on_track():
    """Handle /on_track callback from BPP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized on_track request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"on_track request: {json.dumps(data)}")

        # Process on_track
        try:
            client = ONDCClient(settings)
            result = client.on_track(data)
            frappe.logger("webhooks").info(f"on_track processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing on_track: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing on_track: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in on_track endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def support():
    """Handle /support request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized support request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"support request: {json.dumps(data)}")

        # Process support
        try:
            client = ONDCClient(settings)
            result = client.support(data)
            frappe.logger("webhooks").info(f"support processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing support: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing support: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in support endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def on_support():
    """Handle /on_support callback from BPP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized on_support request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"on_support request: {json.dumps(data)}")

        # Process on_support
        try:
            client = ONDCClient(settings)
            result = client.on_support(data)
            frappe.logger("webhooks").info(f"on_support processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing on_support: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing on_support: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in on_support endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def rating():
    """Handle /rating request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized rating request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"rating request: {json.dumps(data)}")

        # Process rating
        try:
            client = ONDCClient(settings)
            result = client.rating(data)
            frappe.logger("webhooks").info(f"rating processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing rating: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing rating: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in rating endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def on_rating():
    """Handle /on_rating callback from BPP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized on_rating request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"on_rating request: {json.dumps(data)}")

        # Process on_rating
        try:
            client = ONDCClient(settings)
            result = client.on_rating(data)
            frappe.logger("webhooks").info(f"on_rating processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing on_rating: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing on_rating: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in on_rating endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def cancel():
    """Handle /cancel request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized cancel request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"cancel request: {json.dumps(data)}")

        # Process cancel
        try:
            client = ONDCClient(settings)
            result = client.cancel(data)
            frappe.logger("webhooks").info(f"cancel processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing cancel: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing cancel: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in cancel endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def on_cancel():
    """Handle /on_cancel callback from BPP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized on_cancel request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"on_cancel request: {json.dumps(data)}")

        # Process on_cancel
        try:
            client = ONDCClient(settings)
            result = client.on_cancel(data)
            frappe.logger("webhooks").info(f"on_cancel processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing on_cancel: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing on_cancel: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in on_cancel endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def status():
    """Handle /status request from BAP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized status request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"status request: {json.dumps(data)}")

        # Process status
        try:
            client = ONDCClient(settings)
            result = client.status(data)
            frappe.logger("webhooks").info(f"status processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing status: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing status: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in status endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")


@frappe.whitelist(allow_guest=True)
def on_status():
    """Handle /on_status callback from BPP."""
    try:
        if frappe.request.method != "POST":
            return send_nack("Method not allowed")

        # Verify auth header
        auth_header = frappe.request.headers.get("Authorization", "")
        request_body = frappe.request.get_data(as_text=True)

        if not verify_auth_header(auth_header, request_body):
            frappe.logger("webhooks").warning(f"Unauthorized on_status request from {frappe.request.remote_addr}")
            return send_nack("Unauthorized")

        # Parse request
        data = json.loads(request_body)

        # Get ONDC settings
        settings = get_ondc_settings()
        if not settings:
            frappe.logger("webhooks").error("ONDC Settings not found")
            return send_nack("Server configuration error")

        # Log request
        frappe.logger("webhooks").info(f"on_status request: {json.dumps(data)}")

        # Process on_status
        try:
            client = ONDCClient(settings)
            result = client.on_status(data)
            frappe.logger("webhooks").info(f"on_status processed successfully")
            return Response(
                json.dumps(result),
                status=200,
                mimetype="application/json"
            )
        except Exception as e:
            frappe.logger("webhooks").error(f"Error processing on_status: {str(e)}\n{traceback.format_exc()}")
            return send_nack(f"Error processing on_status: {str(e)}")

    except Exception as e:
        frappe.logger("webhooks").error(f"Error in on_status endpoint: {str(e)}\n{traceback.format_exc()}")
        return send_nack(f"Error: {str(e)}")
