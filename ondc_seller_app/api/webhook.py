import frappe
import json
from frappe import _
from werkzeug.wrappers import Response
import traceback
from datetime import datetime

from ondc_seller_app.api.auth import verify_request, validate_context
from ondc_seller_app.api.ondc_errors import (
    build_ack_response,
    build_nack_response,
    build_error,
    get_cancellation_reason,
    CANCELLATION_REASONS,
    FULFILLMENT_STATES,
    is_valid_fulfillment_transition,
)


def to_rfc3339(frappe_dt):
    """Convert Frappe datetime to RFC3339 format with Z suffix"""
    if not frappe_dt:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if isinstance(frappe_dt, str):
        # Try to parse Frappe format and convert to RFC3339
        try:
            from frappe.utils import get_datetime
            dt = get_datetime(frappe_dt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except:
            # Fallback: if already has T and Z, use as-is
            if "T" in frappe_dt and ("Z" in frappe_dt or "+" in frappe_dt):
                return frappe_dt
            # Otherwise convert space to T and add .000Z
            return str(frappe_dt).replace(" ", "T") + ".000Z" if " " in str(frappe_dt) else str(frappe_dt)
    else:
        # datetime object
        return frappe_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@frappe.whitelist(allow_guest=True)
def signing_diagnostic():
    """Diagnose signing issues — returns key info and self-verification result."""
    import base64
    import hashlib
    import nacl.signing

    settings = frappe.get_single("ONDC Settings")
    results = {}

    # 1. Check key formats
    results["subscriber_id"] = settings.subscriber_id
    results["unique_key_id"] = settings.unique_key_id
    results["stored_public_key"] = settings.signing_public_key

    # 2. Derive public key from private key to check consistency
    try:
        signing_private_key = settings.get_password("signing_private_key")
        raw_key = base64.b64decode(signing_private_key)
        results["private_key_raw_length"] = len(raw_key)

        if len(raw_key) == 64:
            seed = raw_key[:32]
            results["key_format"] = "64-byte (seed+pubkey)"
        elif len(raw_key) == 48:
            seed = raw_key[-32:]
            results["key_format"] = "48-byte (PKCS8-wrapped)"
        elif len(raw_key) == 32:
            seed = raw_key
            results["key_format"] = "32-byte (raw seed)"
        else:
            results["key_format"] = f"UNKNOWN ({len(raw_key)} bytes)"
            return results

        sk = nacl.signing.SigningKey(seed)
        derived_public_key = base64.b64encode(bytes(sk.verify_key)).decode()
        results["derived_public_key"] = derived_public_key
        results["keys_match"] = (derived_public_key == settings.signing_public_key)
    except Exception as e:
        results["key_derivation_error"] = str(e)
        return results

    # 3. Self-sign and verify test
    try:
        test_payload = {"test": "hello", "timestamp": "2026-01-01T00:00:00Z"}
        body_str = json.dumps(test_payload, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.blake2b(body_str.encode(), digest_size=64).digest()
        digest_b64 = base64.b64encode(digest).decode()

        signing_string = f"(created): 1000000\n(expires): 9999999\ndigest: BLAKE-512={digest_b64}"
        signature = sk.sign(signing_string.encode()).signature
        sig_b64 = base64.b64encode(signature).decode()

        # Verify with stored public key
        vk = nacl.signing.VerifyKey(base64.b64decode(settings.signing_public_key))
        vk.verify(signing_string.encode(), signature)
        results["self_verify"] = "PASS"
    except nacl.exceptions.BadSignatureError:
        results["self_verify"] = "FAIL - BadSignature (key pair mismatch!)"
    except Exception as e:
        results["self_verify"] = f"FAIL - {str(e)}"

    # 4. Show the exact auth header that would be generated
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        client = ONDCClient(settings)
        auth_header = client.get_auth_header(test_payload)
        results["sample_auth_header"] = auth_header[:200] + "..."
    except Exception as e:
        results["auth_header_error"] = str(e)

    return results


@frappe.whitelist(allow_guest=True)
def registry_lookup_diagnostic():
    """Full diagnostic: vlookup + gateway test + signing self-check.

    Runs three checks in one call:
    1. /vlookup (exactly what the gateway uses) to see if our entry is found
    2. /v2.0/lookup to inspect key + status
    3. Direct on_search POST to the gateway to capture exact 401 response
    """
    import base64
    import hashlib
    import nacl.signing
    import requests as http_requests
    import uuid as _uuid
    from datetime import datetime as _dt, timedelta as _td

    settings = frappe.get_single("ONDC Settings")
    results = {
        "subscriber_id": settings.subscriber_id,
        "unique_key_id": settings.unique_key_id,
        "local_public_key": settings.signing_public_key,
        "environment": settings.environment,
    }

    registry_base = {
        "staging": "https://staging.registry.ondc.org",
        "preprod": "https://preprod.registry.ondc.org",
        "prod": "https://prod.registry.ondc.org",
    }.get(settings.environment, "https://preprod.registry.ondc.org")

    # ── Helper: build signed auth header for any body bytes ──────────
    def _make_auth(body_bytes_inner):
        digest_inner = hashlib.blake2b(body_bytes_inner, digest_size=64).digest()
        digest_b64_inner = base64.b64encode(digest_inner).decode()
        ts_created = int(_dt.utcnow().timestamp())
        ts_expires = int((_dt.utcnow() + _td(minutes=5)).timestamp())
        ss = (f"(created): {ts_created}\n"
              f"(expires): {ts_expires}\n"
              f"digest: BLAKE-512={digest_b64_inner}")
        priv_key_b64 = settings.get_password("signing_private_key")
        raw_key = base64.b64decode(priv_key_b64)
        if len(raw_key) == 64:
            seed = raw_key[:32]
        elif len(raw_key) == 32:
            seed = raw_key
        else:
            seed = raw_key[-32:]
        sk = nacl.signing.SigningKey(seed)
        sig = sk.sign(ss.encode()).signature
        sig_b64 = base64.b64encode(sig).decode()
        hdr = (f'Signature keyId="{settings.subscriber_id}|{settings.unique_key_id}|ed25519",'
               f'algorithm="ed25519",created="{ts_created}",expires="{ts_expires}",'
               f'headers="(created) (expires) digest",signature="{sig_b64}"')
        # also self-verify
        vk = nacl.signing.VerifyKey(base64.b64decode(settings.signing_public_key))
        try:
            vk.verify(ss.encode(), sig)
            sv = "PASS"
        except Exception as sve:
            sv = f"FAIL:{sve}"
        return hdr, sv, ss

    # ── 1. /vlookup — this is what the ONDC gateway uses ─────────────
    vlookup_payload = {
        "country": "IND",
        "domain": settings.domain,
        "type": "BPP",
        "city": settings.city or "std:080",
        "subscriber_id": settings.subscriber_id,
        "ukId": settings.unique_key_id,
    }
    vl_bytes = json.dumps(vlookup_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        vl_auth, vl_sv, _ = _make_auth(vl_bytes)
        results["vlookup_self_verify"] = vl_sv
        vl_resp = http_requests.post(
            f"{registry_base}/vlookup",
            data=vl_bytes,
            headers={"Content-Type": "application/json", "Authorization": vl_auth},
            timeout=15,
        )
        results["vlookup_status"] = vl_resp.status_code
        try:
            vl_data = vl_resp.json()
            results["vlookup_count"] = len(vl_data) if isinstance(vl_data, list) else "dict"
            results["vlookup_raw"] = json.dumps(vl_data)[:800]
            if isinstance(vl_data, list) and len(vl_data) == 0:
                results["VLOOKUP_DIAGNOSIS"] = (
                    "vlookup returned EMPTY — gateway cannot find our BPP entry. "
                    "This means city/domain/country/ukId do not exactly match registry. "
                    "The gateway will ALWAYS return 401 until vlookup finds our entry."
                )
            elif isinstance(vl_data, list) and len(vl_data) > 0:
                entry = vl_data[0]
                results["vlookup_signing_key"] = entry.get("signing_public_key", "")
                results["vlookup_status_field"] = entry.get("status", "")
                results["vlookup_ukId"] = entry.get("ukId", "")
                results["vlookup_keys_match"] = entry.get("signing_public_key") == settings.signing_public_key
                results["VLOOKUP_DIAGNOSIS"] = (
                    f"vlookup found entry: status={entry.get('status')}, "
                    f"keys_match={entry.get('signing_public_key') == settings.signing_public_key}"
                )
        except Exception:
            results["vlookup_raw"] = vl_resp.text[:500]
    except Exception as e:
        results["vlookup_error"] = str(e)

    # ── 2. /v2.0/lookup — broader lookup for key inspection ──────────
    lookup_payload = {
        "subscriber_id": settings.subscriber_id,
        "type": "BPP",
        "domain": settings.domain,
    }
    body_bytes = json.dumps(lookup_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        auth_header, self_verify, _ = _make_auth(body_bytes)
        results["self_verify"] = self_verify
        resp = http_requests.post(
            f"{registry_base}/v2.0/lookup",
            data=body_bytes,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
            timeout=15,
        )
        results["registry_status"] = resp.status_code
        try:
            resp_data = resp.json()
        except Exception:
            resp_data = None
            results["registry_raw"] = resp.text[:500]

        if resp.status_code == 200 and resp_data:
            results["registry_response_summary"] = (
                f"{len(resp_data)} entries" if isinstance(resp_data, list) else "dict"
            )
            registry_entry = _extract_registry_entry(
                resp_data, settings.subscriber_id, settings.unique_key_id
            )
            if registry_entry:
                results["registry_status_field"] = registry_entry.get("status")
                results["registry_type"] = registry_entry.get("type")
                results["registry_ukId"] = registry_entry.get("ukId") or registry_entry.get("unique_key_id")
                results["registry_entry_keys"] = list(registry_entry.keys())
                results["registry_full_entry"] = json.dumps(registry_entry)[:1000]
            registry_key = _extract_registry_key(
                resp_data, settings.subscriber_id, settings.unique_key_id
            )
            if registry_key:
                results["registry_public_key"] = registry_key
                results["keys_match"] = registry_key == settings.signing_public_key
    except Exception as e:
        results["lookup_error"] = str(e)

    # ── 3. Direct gateway test — POST minimal on_search ──────────────
    try:
        gw_payload = {
            "context": {
                "domain": settings.domain or "ONDC:RET11",
                "country": "IND",
                "city": settings.city or "std:080",
                "action": "on_search",
                "core_version": "1.2.0",
                "bap_id": "pramaan.ondc.org",
                "bap_uri": "https://pre-prod.gcr.ondc.org",
                "bpp_id": settings.subscriber_id,
                "bpp_uri": settings.subscriber_url,
                "transaction_id": str(_uuid.uuid4()),
                "message_id": str(_uuid.uuid4()),
                "timestamp": _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "ttl": "PT30S",
            },
            "message": {"catalog": {}}
        }
        gw_bytes = json.dumps(gw_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        gw_auth, gw_sv, gw_ss = _make_auth(gw_bytes)
        results["gateway_self_verify"] = gw_sv
        results["gateway_signing_string"] = gw_ss
        results["gateway_auth_header"] = gw_auth
        gw_resp = http_requests.post(
            "https://pre-prod.gcr.ondc.org/on_search",
            data=gw_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": gw_auth,
            },
            timeout=15,
        )
        results["gateway_status"] = gw_resp.status_code
        results["gateway_response"] = gw_resp.text[:1000]
        results["gateway_response_headers"] = dict(gw_resp.headers)
    except Exception as e:
        results["gateway_error"] = str(e)

    return results


def _extract_registry_entry(data, subscriber_id, unique_key_id):
    """Extract the full registry entry dict for our subscriber."""
    if isinstance(data, list):
        for item in data:
            sid = item.get("subscriber_id", "")
            ukid = item.get("ukId") or item.get("unique_key_id", "")
            if sid == subscriber_id and ukid == unique_key_id:
                return item
        # Fallback: first entry matching subscriber_id
        for item in data:
            if item.get("subscriber_id") == subscriber_id:
                return item
        if data:
            return data[0]
    elif isinstance(data, dict):
        return data
    return None


def _extract_registry_key(data, subscriber_id, unique_key_id):
    """Extract signing_public_key from registry lookup response."""
    entry = _extract_registry_entry(data, subscriber_id, unique_key_id)
    if entry:
        return entry.get("signing_public_key")
    return None


@frappe.whitelist(allow_guest=True)
def handle_webhook(api):
    """
    Handle incoming ONDC webhooks.
    
    Implements the ONDC async ACK+callback pattern:
    1. Validate request schema and verify signature
    2. Return ACK/NACK immediately
    3. Process asynchronously via frappe.enqueue
    4. Send callback to BAP's URI
    """
    try:
        data = frappe.request.get_json()
        if not data:
            frappe.response.update(build_nack_response("20000", "Empty request body"))
            frappe.response["http_status_code"] = 400
            return

        context = data.get("context", {})

        # Build the context to echo back in ACK/NACK responses.
        # ONDC/Beckn spec requires the synchronous response to include the
        # request context so the caller can correlate the ACK with its request.
        resp_context = {
            "domain": context.get("domain"),
            "country": context.get("country"),
            "city": context.get("city"),
            "action": context.get("action"),
            "core_version": context.get("core_version"),
            "bap_id": context.get("bap_id"),
            "bap_uri": context.get("bap_uri"),
            "bpp_id": context.get("bpp_id") or frappe.db.get_single_value("ONDC Settings", "subscriber_id"),
            "bpp_uri": context.get("bpp_uri") or frappe.db.get_single_value("ONDC Settings", "subscriber_url"),
            "transaction_id": context.get("transaction_id"),
            "message_id": context.get("message_id"),
            "timestamp": context.get("timestamp"),
            "ttl": context.get("ttl", "PT30S"),
        }

        # --- Step 1: Validate context ---
        is_valid_ctx, err_code, err_msg = validate_context(context)
        if not is_valid_ctx:
            _log_webhook(api, data, status="Failed", error_message=err_msg)
            nack = build_nack_response(err_code, err_msg)
            nack["context"] = resp_context
            frappe.response.update(nack)
            frappe.response["http_status_code"] = 400
            return

        # --- Step 2: Verify signature ---
        auth_header = frappe.request.headers.get("Authorization")
        gateway_auth_header = frappe.request.headers.get("X-Gateway-Authorization")

        is_valid_sig, sig_error = verify_request(data, auth_header, gateway_auth_header)
        if not is_valid_sig:
            # Frappe v14+: first arg = title (140 char max), use keyword args for safety
            frappe.log_error(
                title=f"ONDC Auth: {api} sig fail"[:140],
                message=f"Signature verification failed for {api}: {sig_error}"
            )
            # Log but don't block in staging/preprod - many test BAPs have mismatched keys
            settings = frappe.get_single("ONDC Settings")
            if settings.environment == "prod":
                _log_webhook(api, data, status="Failed", error_message=f"Auth failed: {sig_error}")
                nack = build_nack_response("20001", sig_error)
                nack["context"] = resp_context
                frappe.response.update(nack)
                frappe.response["http_status_code"] = 401
                return

        # --- Step 3: Validate action matches route ---
        if context.get("action") != api:
            _log_webhook(api, data, status="Failed", error_message=f"Action mismatch: {context.get('action')} != {api}")
            nack = build_nack_response("10002", f"Action mismatch: expected {api}, got {context.get('action')}")
            nack["context"] = resp_context
            frappe.response.update(nack)
            frappe.response["http_status_code"] = 400
            return
        
        # --- Step 4: Log the webhook ---
        log_name = _log_webhook(api, data, status="Received")
        
        # --- Step 5: Return ACK immediately ---
        ack_response = build_ack_response()
        
        # --- Step 6: Enqueue async processing ---
        handler_map = {
            # Core ONDC transaction APIs
            "search": "ondc_seller_app.api.webhook.process_search",
            "select": "ondc_seller_app.api.webhook.process_select",
            "init": "ondc_seller_app.api.webhook.process_init",
            "confirm": "ondc_seller_app.api.webhook.process_confirm",
            "status": "ondc_seller_app.api.webhook.process_status",
            "track": "ondc_seller_app.api.webhook.process_track",
            "cancel": "ondc_seller_app.api.webhook.process_cancel",
            "update": "ondc_seller_app.api.webhook.process_update",
            "rating": "ondc_seller_app.api.webhook.process_rating",
            "support": "ondc_seller_app.api.webhook.process_support",
            # IGM (Issue & Grievance Management) APIs
            "issue": "ondc_seller_app.api.webhook.process_issue",
            "issue_status": "ondc_seller_app.api.webhook.process_issue_status",
            # RSP (Reconciliation & Settlement Protocol) APIs
            "receiver_recon": "ondc_seller_app.api.webhook.process_receiver_recon",
        }
        
        handler_method = handler_map.get(api)
        if not handler_method:
            _update_webhook_log(log_name, status="Failed", error_message=f"Unknown action: {api}")
            nack = build_nack_response("10002", f"Unknown action: {api}")
            nack["context"] = resp_context
            frappe.response.update(nack)
            frappe.response["http_status_code"] = 400
            return

        frappe.enqueue(
            handler_method,
            queue="default",
            timeout=30,
            data=data,
            log_name=log_name,
        )

        frappe.db.commit()
        # Include context in the ACK so Pramaan can correlate the response.
        # ONDC/Beckn spec requires context to be echoed back in the synchronous ACK.
        ack_response["context"] = resp_context
        frappe.response.update(ack_response)
        return

    except Exception as e:
        frappe.log_error(title=f"ONDC Webhook Error - {api}"[:140], message=traceback.format_exc())
        frappe.response.update(build_nack_response("20000", str(e)))
        frappe.response["http_status_code"] = 500
        return


# ---------------------------------------------------------------------------
# Async Handlers (called via frappe.enqueue)
# ---------------------------------------------------------------------------

def process_search(data, log_name=None):
    """Process search request asynchronously and send on_search callback"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        result = client.on_search(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_search Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_select(data, log_name=None):
    """Process select request asynchronously and send on_select callback"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        result = client.on_select(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_select Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_init(data, log_name=None):
    """Process init request asynchronously and send on_init callback"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        result = client.on_init(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_init Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_confirm(data, log_name=None):
    """Process confirm request: create ONDC Order, then send on_confirm callback"""
    try:
        order_data = data.get("message", {}).get("order", {})
        context = data.get("context", {})
        
        # Create ONDC Order
        order = frappe.new_doc("ONDC Order")
        order.ondc_order_id = order_data.get("id") or frappe.generate_hash(length=16)
        order.transaction_id = context.get("transaction_id")
        order.message_id = context.get("message_id")
        order.bap_id = context.get("bap_id")
        order.bap_uri = context.get("bap_uri")
        order.order_status = "Accepted"
        order.fulfillment_state = "Pending"
        
        # Customer details
        billing = order_data.get("billing", {})
        order.customer_name = billing.get("name")
        order.customer_email = billing.get("email")
        order.customer_phone = billing.get("phone")
        
        # Billing address
        address = billing.get("address", {})
        order.billing_name = billing.get("name")
        order.billing_building = address.get("building")
        order.billing_locality = address.get("locality")
        order.billing_city = address.get("city")
        order.billing_state = address.get("state")
        order.billing_area_code = address.get("area_code")

        # Store BAP's timestamps and billing info as JSON for echoing back in responses
        order.custom_bap_data = json.dumps({
            "bap_created_at": order_data.get("created_at"),
            "bap_updated_at": order_data.get("updated_at"),
            "billing_created_at": billing.get("created_at"),
            "billing_updated_at": billing.get("updated_at"),
            "billing_address_name": billing.get("address", {}).get("name"),
        })

        # Fulfillment details
        fulfillment = order_data.get("fulfillments", [{}])[0]
        order.fulfillment_id = fulfillment.get("id")
        order.fulfillment_type = fulfillment.get("type", "Delivery")
        
        end_location = fulfillment.get("end", {}).get("location", {})
        order.shipping_gps = end_location.get("gps")
        order.shipping_address = json.dumps(end_location.get("address", {}))
        
        # Items  (FIX: was using self.get_item_code_from_ondc_id - self is undefined)
        for item_data in order_data.get("items", []):
            order.append("items", {
                "ondc_item_id": item_data.get("id"),
                "item_code": get_item_code_from_ondc_id(item_data.get("id")),
                "quantity": item_data.get("quantity", {}).get("count", 1),
                "price": float(item_data.get("price", {}).get("value", 0)),
            })
        
        # Billing tax number (GSTIN) from billing object
        order.billing_tax_number = billing.get("tax_number") or billing.get("tax_id") or ""

        # Payment details
        payment = order_data.get("payment", {})
        order.payment_type = _map_payment_type(payment.get("type"))
        order.payment_status = "Paid" if payment.get("status") == "PAID" else "Pending"
        order.payment_transaction_id = payment.get("params", {}).get("transaction_id") or ""
        
        # Cancellation fields
        cancellation = order_data.get("cancellation", {})
        if cancellation:
            order.cancellation_reason_id = cancellation.get("reason", {}).get("id")
        
        order.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Send on_confirm callback
        from ondc_seller_app.api.ondc_client import ONDCClient

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        result = client.on_confirm(data)
        _update_webhook_log(log_name, status="Processed", response=result)

        # --- Unsolicited on_update for partial cancellation (Flow 3A) ---
        # After on_confirm, the seller NP must proactively send on_update
        # with partial cancellation details (reduced items).
        # Enqueue with a small delay so on_confirm is received first.
        frappe.enqueue(
            "ondc_seller_app.api.webhook.send_unsolicited_on_update",
            queue="default",
            timeout=30,
            enqueue_after_commit=True,
            data=data,
            order_name=order.name,
        )

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_confirm Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def send_unsolicited_on_update(data, order_name):
    """
    Send unsolicited on_update for merchant-side partial cancellation (Flow 3A).

    After on_confirm, the seller NP proactively sends on_update to the BAP
    indicating partial cancellation: one item's quantity is reduced (cancelled),
    while remaining items stay. Fulfillment State = "Pending", Order State = "Accepted".
    """
    import time
    import datetime as dt_module

    time.sleep(3)  # Brief delay to ensure on_confirm is processed first

    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        from datetime import datetime, timedelta

        order = frappe.get_doc("ONDC Order", order_name)
        order.reload()  # ensure child tables are loaded
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)

        # Build context (create_context now auto-generates unique message_id)
        req_context = data.get("context", {})
        context = client.create_context("on_update", req_context)

        store_gps = settings.get("store_gps") or "0.0,0.0"
        store_name = settings.get("store_name") or settings.legal_entity_name or "ONDC Seller"
        location_id = f"LOC-{settings.city}"
        tax_rate = float(settings.get("default_tax_rate") or 0)

        now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        hour_later = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        two_hours = (datetime.utcnow() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        # --- Partial cancellation logic ---
        # For Pramaan Flow 3A: reduce the first item's quantity by 1
        # (simulates merchant-side partial cancellation)
        items = []
        quote_breakup = []
        item_total = 0.0
        total_tax = 0.0
        cancelled_items = []
        partial_cancel_info = []  # store for later use by process_status

        order_items = list(order.items)
        if not order_items:
            frappe.log_error(
                title="on_update: no items",
                message=f"Order {order_name} has no items, skipping unsolicited on_update"
            )
            return

        for idx, item in enumerate(order_items):
            price = float(item.price or 0)
            original_qty = int(item.quantity or 1)

            if idx == 0 and original_qty > 1:
                # Partially cancel: reduce qty by 1
                new_qty = original_qty - 1
                cancelled_qty = 1
            elif idx == 0 and original_qty == 1 and len(order_items) > 1:
                # Fully cancel this item if there are other items
                new_qty = 0
                cancelled_qty = original_qty
            else:
                new_qty = original_qty
                cancelled_qty = 0

            # Active items (on forward fulfillment F1)
            if new_qty > 0:
                line_total = price * new_qty
                item_total += line_total
                items.append({
                    "id": item.ondc_item_id,
                    "fulfillment_id": order.fulfillment_id or "F1",
                    "quantity": {"count": new_qty},
                })
                quote_breakup.append({
                    "title": item.ondc_item_id,
                    "@ondc/org/item_id": item.ondc_item_id,
                    "@ondc/org/item_quantity": {"count": new_qty},
                    "@ondc/org/title_type": "item",
                    "price": {"currency": "INR", "value": str(line_total)},
                    "item": {"price": {"currency": "INR", "value": str(price)}},
                })
                item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
                total_tax += item_tax
                quote_breakup.append({
                    "title": "Tax",
                    "@ondc/org/item_id": item.ondc_item_id,
                    "@ondc/org/title_type": "tax",
                    "price": {"currency": "INR", "value": str(item_tax)},
                })

            # Cancelled portion (on cancel fulfillment C1)
            if cancelled_qty > 0:
                cancelled_amount = price * cancelled_qty
                cancelled_items.append({
                    "id": item.ondc_item_id,
                    "fulfillment_id": "C1",
                    "quantity": {"count": cancelled_qty},
                })
                partial_cancel_info.append({
                    "item_id": item.ondc_item_id,
                    "cancelled_qty": cancelled_qty,
                    "active_qty": new_qty,
                    "price": price,
                    "cancelled_amount": cancelled_amount,
                })

        # Add cancelled items to the items list
        items.extend(cancelled_items)

        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": order.fulfillment_id or "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        packing_charge = float(settings.get("default_packing_charge") or 0)
        quote_breakup.append({
            "title": "Packing charges",
            "@ondc/org/item_id": order.fulfillment_id or "F1",
            "@ondc/org/title_type": "packing",
            "price": {"currency": "INR", "value": str(packing_charge)},
        })

        grand_total = item_total + total_tax + delivery_charge + packing_charge

        # Extract BAP's stored timestamps and billing info
        bap_data = {}
        try:
            if order.get("custom_bap_data"):
                bap_data = json.loads(order.custom_bap_data) or {}
        except (json.JSONDecodeError, TypeError):
            pass

        # Build fulfillments: active (F1) + cancellation (C1)
        fulfillment_obj = {
            "id": order.fulfillment_id or "F1",
            "type": order.fulfillment_type or "Delivery",
            "@ondc/org/provider_name": store_name,
            "@ondc/org/TAT": settings.get("default_time_to_ship") or "PT60M",
            "tracking": False,
            "state": {"descriptor": {"code": "Pending"}},
            "start": {
                "location": {
                    "id": location_id,
                    "descriptor": {"name": store_name},
                    "gps": store_gps,
                    "address": {
                        "locality": settings.get("store_locality") or "",
                        "city": settings.get("store_city_name") or settings.city,
                        "state": settings.get("store_state") or "",
                        "country": "IND",
                        "area_code": settings.get("store_area_code") or settings.city,
                    },
                },
                "contact": {
                    "phone": settings.get("consumer_care_phone") or "",
                    "email": settings.get("consumer_care_email") or "",
                },
                "time": {
                    "range": {"start": now_str, "end": hour_later},
                    "timestamp": now_str,
                },
            },
            "tags": [
                {"code": "routing", "list": [{"code": "type", "value": "P2P"}]},
            ],
        }

        # End location from order
        if order.get("shipping_gps") or order.get("shipping_address"):
            end_address = {}
            if order.get("shipping_address"):
                try:
                    end_address = json.loads(order.shipping_address) if isinstance(order.shipping_address, str) else order.shipping_address
                except (json.JSONDecodeError, TypeError):
                    end_address = {}
            fulfillment_obj["end"] = {
                "location": {
                    "gps": order.get("shipping_gps") or "",
                    "address": end_address,
                },
                "person": {"name": order.customer_name or ""},
                "contact": {
                    "phone": order.customer_phone or "",
                    "email": order.customer_email or "",
                },
                "time": {"range": {"start": now_str, "end": two_hours}},
            }

        fulfillments = [fulfillment_obj]

        # Cancellation fulfillment (C1) with cancel_request + quote_trail tags
        if cancelled_items:
            cancel_tags = [
                {
                    "code": "cancel_request",
                    "list": [
                        {"code": "reason_id", "value": "009"},
                        {"code": "initiated_by", "value": settings.subscriber_id},
                    ],
                },
            ]
            # Add quote_trail for each cancelled item (per ONDC spec)
            for ci in partial_cancel_info:
                cancel_tags.append({
                    "code": "quote_trail",
                    "list": [
                        {"code": "type", "value": "item"},
                        {"code": "id", "value": ci["item_id"]},
                        {"code": "currency", "value": "INR"},
                        {"code": "value", "value": str(-ci["cancelled_amount"])},
                    ],
                })

            fulfillments.append({
                "id": "C1",
                "type": "Cancel",
                "state": {"descriptor": {"code": "Cancelled"}},
                "tags": cancel_tags,
            })

        # Payment
        payment_obj = {
            "type": order.payment_type or "ON-ORDER",
            "collected_by": "BAP" if (order.payment_type or "ON-ORDER") != "ON-FULFILLMENT" else "BPP",
            "status": "PAID" if order.payment_status == "Paid" else "NOT-PAID",
            "params": {
                "currency": "INR",
                "amount": str(round(grand_total, 2)),
                "transaction_id": order.get("payment_transaction_id") or order.ondc_order_id,
            },
            "@ondc/org/buyer_app_finder_fee_type": "percent",
            "@ondc/org/buyer_app_finder_fee_amount": str(settings.get("buyer_finder_fee") or "3"),
            "@ondc/org/settlement_basis": "delivery",
            "@ondc/org/settlement_window": "P2D",
            "@ondc/org/withholding_amount": "0.00",
            "@ondc/org/settlement_details": [
                {
                    "settlement_counterparty": "seller-app",
                    "settlement_phase": "sale-amount",
                    "settlement_type": "neft",
                    "beneficiary_name": settings.legal_entity_name or "",
                    "settlement_bank_account_no": settings.get("settlement_bank_account") or "",
                    "settlement_ifsc_code": settings.get("settlement_ifsc_code") or "",
                    "bank_name": settings.get("settlement_bank_name") or "",
                    "branch_name": settings.get("settlement_branch_name") or "",
                }
            ],
        }

        order_payload = {
            "id": order.ondc_order_id,
            "state": "Accepted",
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items,
            "billing": {
                "name": order.billing_name or order.customer_name or "",
                "address": {
                    "building": order.billing_building or "",
                    "locality": order.billing_locality or "",
                    "city": order.billing_city or "",
                    "state": order.billing_state or "",
                    "country": "IND",
                    "area_code": order.billing_area_code or "",
                    "name": bap_data.get("billing_address_name") or order.billing_name or "",
                },
                "email": order.customer_email or "",
                "phone": order.customer_phone or "",
                "tax_number": order.get("billing_tax_number") or "",
                "created_at": bap_data.get("billing_created_at") or to_rfc3339(order.creation),
                "updated_at": bap_data.get("billing_updated_at") or to_rfc3339(order.modified),
            },
            "fulfillments": fulfillments,
            "quote": {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "P1D",
            },
            "payment": payment_obj,
            "created_at": to_rfc3339(order.creation),
            "updated_at": now_str,
        }

        payload = {"context": context, "message": {"order": order_payload}}

        # Log full payload for debugging
        frappe.log_error(
            title="on_update PAYLOAD (unsolicited)",
            message=json.dumps(payload, indent=2, default=str)[:20000],
        )

        result = client.send_callback(
            req_context.get("bap_uri"),
            "/on_update",
            payload,
        )

        frappe.log_error(
            title="ONDC unsolicited on_update result",
            message=f"Order: {order.ondc_order_id}, Result: {json.dumps(result, default=str)[:2000]}"
        )

        # --- Store partial cancel data on the order for use by subsequent on_status calls ---
        if cancelled_items and partial_cancel_info:
            bap_data["partial_cancel"] = {
                "items": partial_cancel_info,
                "cancel_reason_id": "009",
                "cancelled_by": settings.subscriber_id,
                "grand_total": round(grand_total, 2),
                "item_total": item_total,
                "total_tax": total_tax,
            }
            frappe.db.set_value(
                "ONDC Order", order.name,
                "custom_bap_data", json.dumps(bap_data),
                update_modified=False
            )
            frappe.db.commit()

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC unsolicited on_update Error")


def process_status(data, log_name=None):
    """Process status request and send on_status callback with ONDC-compliant structure.

    v3 – Rewrote to:
    1. Load order by *name* (not dict filter) so child tables are guaranteed.
    2. Use db_set for fulfillment progression (no full save → no side-effects).
    3. Build every section inline (no try/except swallowing) so errors surface.
    4. Log the FULL payload being sent so we can see exactly what Pramaan receives.
    """
    import datetime as dt_module
    from datetime import datetime

    trace = []  # human-readable breadcrumbs

    def _t(msg):
        trace.append(msg)

    try:
        from ondc_seller_app.api.ondc_client import ONDCClient

        # ── 1. Extract order_id ──
        message = data.get("message", {})
        order_id = message.get("order", {}).get("id") or message.get("order_id")
        if not order_id:
            _update_webhook_log(log_name, status="Failed", error_message="Missing order_id in status")
            return
        _t(f"1.order_id={order_id}")

        # ── 2. Load order BY NAME (guarantees child tables) ──
        order_name = frappe.db.get_value("ONDC Order", {"ondc_order_id": order_id}, "name")
        if not order_name:
            _update_webhook_log(log_name, status="Failed", error_message=f"Order not found: {order_id}")
            return
        order = frappe.get_doc("ONDC Order", order_name)
        order.reload()  # force child-table reload
        _t(f"2.loaded name={order.name} items={len(order.items or [])}")

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)

        # ── 3. Auto-progress fulfillment state via db_set (no full save) ──
        state_progression = [
            "Pending", "Packed", "Agent-assigned", "At-pickup",
            "Order-picked-up", "Out-for-delivery", "Order-delivered",
        ]
        current_state = order.get("fulfillment_state") or "Pending"
        if current_state in state_progression:
            idx = state_progression.index(current_state)
            if idx < len(state_progression) - 1:
                next_state = state_progression[idx + 1]
                frappe.db.set_value("ONDC Order", order.name, "fulfillment_state", next_state, update_modified=False)
                frappe.db.commit()
                order.fulfillment_state = next_state
                _t(f"3.progress {current_state}->{next_state}")
            else:
                _t(f"3.already_at {current_state}")
        else:
            _t(f"3.unknown_state {current_state}")

        fulfillment_state = order.get("fulfillment_state") or "Pending"

        # Map fulfillment state → order state
        state_map = {
            "Pending": "Accepted",
            "Packed": "In-progress",
            "Agent-assigned": "In-progress",
            "At-pickup": "In-progress",
            "Order-picked-up": "In-progress",
            "Out-for-delivery": "In-progress",
            "Order-delivered": "Completed",
            "Cancelled": "Cancelled",
        }
        order_state = state_map.get(fulfillment_state, order.order_status or "Accepted")
        _t(f"4.state_map fulfillment={fulfillment_state} order={order_state}")

        # ── 4. Load BAP data early (needed for partial cancel awareness) ──
        bap_data = {}
        raw_bap = order.get("custom_bap_data")
        if raw_bap:
            try:
                bap_data = json.loads(raw_bap) if isinstance(raw_bap, str) else {}
            except Exception:
                bap_data = {}

        partial_cancel = bap_data.get("partial_cancel")
        has_partial_cancel = bool(partial_cancel and partial_cancel.get("items"))
        _t(f"4a.bap_keys={list(bap_data.keys())[:5]} partial_cancel={has_partial_cancel}")

        # Build a lookup of cancelled items {item_id: {cancelled_qty, active_qty, price, cancelled_amount}}
        cancel_lookup = {}
        if has_partial_cancel:
            for ci in partial_cancel["items"]:
                cancel_lookup[ci["item_id"]] = ci

        # ── 5. Build items and quote breakup (partial-cancel aware) ──
        items_list = []
        cancelled_items_list = []
        quote_breakup = []
        item_total = 0.0
        total_tax = 0.0
        tax_rate = float(settings.get("default_tax_rate") or 0)

        for row in (order.items or []):
            price_val = float(row.price or 0)
            original_qty = int(row.quantity or 1)
            item_id = row.ondc_item_id or ""

            if has_partial_cancel and item_id in cancel_lookup:
                # This item was partially/fully cancelled
                ci = cancel_lookup[item_id]
                active_qty = int(ci.get("active_qty", 0))
                cancelled_qty = int(ci.get("cancelled_qty", 0))
            else:
                active_qty = original_qty
                cancelled_qty = 0

            # Active portion → F1 fulfillment
            if active_qty > 0:
                line_total = price_val * active_qty
                item_total += line_total

                items_list.append({
                    "id": item_id,
                    "fulfillment_id": order.fulfillment_id or "F1",
                    "quantity": {"count": active_qty},
                })

                quote_breakup.append({
                    "title": item_id,
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/item_quantity": {"count": active_qty},
                    "@ondc/org/title_type": "item",
                    "price": {"currency": "INR", "value": str(line_total)},
                    "item": {"price": {"currency": "INR", "value": str(price_val)}},
                })

                item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
                total_tax += item_tax
                quote_breakup.append({
                    "title": "Tax",
                    "@ondc/org/item_id": item_id,
                    "@ondc/org/title_type": "tax",
                    "price": {"currency": "INR", "value": str(item_tax)},
                })

            # Cancelled portion → C1 fulfillment
            if cancelled_qty > 0:
                cancelled_items_list.append({
                    "id": item_id,
                    "fulfillment_id": "C1",
                    "quantity": {"count": cancelled_qty},
                })

        # Add cancelled items to the main items list
        items_list.extend(cancelled_items_list)

        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        packing_charge = float(settings.get("default_packing_charge") or 0)
        convenience_fee = float(settings.get("convenience_fee") or 0)

        for title, tid, ttype, val in [
            ("Delivery charges", "F1", "delivery", delivery_charge),
            ("Packing charges", "F1", "packing", packing_charge),
            ("Convenience Fee", "F1", "misc", convenience_fee),
        ]:
            quote_breakup.append({
                "title": title,
                "@ondc/org/item_id": tid,
                "@ondc/org/title_type": ttype,
                "price": {"currency": "INR", "value": str(val)},
            })

        grand_total = item_total + total_tax + delivery_charge + packing_charge + convenience_fee
        _t(f"5.items={len(items_list)} cancelled={len(cancelled_items_list)} grand={grand_total}")

        # ── 6. Build fulfillment object (F1 - active delivery) ──
        now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        hour_later = (datetime.utcnow() + dt_module.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        two_hours = (datetime.utcnow() + dt_module.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        store_gps = settings.get("store_gps") or "0.0,0.0"
        store_name = settings.get("store_name") or settings.legal_entity_name or "ONDC Seller"
        location_id = f"LOC-{settings.city or 'default'}"

        fulfillment_obj = {
            "id": order.fulfillment_id or "F1",
            "type": order.fulfillment_type or "Delivery",
            "@ondc/org/provider_name": store_name,
            "@ondc/org/TAT": settings.get("default_time_to_ship") or "PT60M",
            "tracking": bool(order.get("tracking_url")),
            "state": {"descriptor": {"code": fulfillment_state}},
            "start": {
                "location": {
                    "id": location_id,
                    "descriptor": {"name": store_name},
                    "gps": store_gps,
                    "address": {
                        "locality": settings.get("store_locality") or "",
                        "city": settings.get("store_city_name") or settings.city or "",
                        "state": settings.get("store_state") or "",
                        "country": "IND",
                        "area_code": settings.get("store_area_code") or settings.city or "",
                    },
                },
                "contact": {
                    "phone": settings.get("consumer_care_phone") or "",
                    "email": settings.get("consumer_care_email") or "",
                },
                "instructions": {
                    "code": "PICKUP_INSTRUCTIONS",
                    "name": "Pickup Instructions",
                    "short_desc": "Please collect from store",
                    "long_desc": "Pickup is available during store operating hours.",
                    "images": [],
                },
                "time": {
                    "range": {"start": now_str, "end": hour_later},
                    "timestamp": now_str,
                },
            },
            "tags": [
                {"code": "routing", "list": [{"code": "type", "value": "P2P"}]},
            ],
        }

        # End location
        end_address = {}
        if order.get("shipping_address"):
            try:
                end_address = json.loads(order.shipping_address) if isinstance(order.shipping_address, str) else {}
            except Exception:
                end_address = {}

        fulfillment_obj["end"] = {
            "location": {
                "gps": order.get("shipping_gps") or store_gps,
                "address": end_address if end_address else {
                    "locality": order.get("billing_locality") or "",
                    "city": order.get("billing_city") or "",
                    "state": order.get("billing_state") or "",
                    "country": "IND",
                    "area_code": order.get("billing_area_code") or "",
                },
            },
            "person": {"name": order.customer_name or order.billing_name or ""},
            "contact": {
                "phone": order.customer_phone or "",
                "email": order.customer_email or "",
            },
            "time": {"range": {"start": now_str, "end": two_hours}},
        }

        # Agent (always present after Packed)
        agent_states = ["Agent-assigned", "At-pickup", "Order-picked-up", "Out-for-delivery", "Order-delivered"]
        if fulfillment_state in agent_states:
            fulfillment_obj["agent"] = {
                "name": order.get("delivery_agent_name") or store_name,
                "phone": order.get("delivery_agent_phone") or settings.get("consumer_care_phone") or "9876543210",
            }

        # Documents (invoice after pickup)
        post_pickup = ["Order-picked-up", "Out-for-delivery", "Order-delivered"]
        if fulfillment_state in post_pickup:
            fulfillment_obj["documents"] = [{
                "url": order.get("invoice_url") or f"https://{settings.subscriber_id}/invoice/{order.ondc_order_id}",
                "label": "Invoice",
            }]

        # Tracking
        if order.get("tracking_url"):
            fulfillment_obj["tracking"] = True
            fulfillment_obj["@ondc/org/tracking_url"] = order.tracking_url

        # Build fulfillments list: F1 + optional C1 for partial cancellation
        fulfillments_list = [fulfillment_obj]

        if has_partial_cancel and cancelled_items_list:
            # C1 cancel fulfillment with cancel_request + quote_trail tags
            cancel_tags = [
                {
                    "code": "cancel_request",
                    "list": [
                        {"code": "reason_id", "value": partial_cancel.get("cancel_reason_id", "009")},
                        {"code": "initiated_by", "value": partial_cancel.get("cancelled_by", settings.subscriber_id)},
                    ],
                },
            ]
            # quote_trail for each cancelled item (per ONDC spec)
            for ci in partial_cancel["items"]:
                cancel_tags.append({
                    "code": "quote_trail",
                    "list": [
                        {"code": "type", "value": "item"},
                        {"code": "id", "value": ci["item_id"]},
                        {"code": "currency", "value": "INR"},
                        {"code": "value", "value": str(-ci["cancelled_amount"])},
                    ],
                })
            fulfillments_list.append({
                "id": "C1",
                "type": "Cancel",
                "state": {"descriptor": {"code": "Cancelled"}},
                "tags": cancel_tags,
            })
            _t(f"6a.C1 fulfillment added, {len(cancelled_items_list)} cancelled items")

        _t(f"6.fulfillments={len(fulfillments_list)} keys={sorted(fulfillment_obj.keys())}")

        # ── 7. Payment ──
        payment_type = order.payment_type or "ON-ORDER"
        payment_obj = {
            "type": payment_type,
            "collected_by": "BAP" if payment_type != "ON-FULFILLMENT" else "BPP",
            "status": "PAID" if order.get("payment_status") == "Paid" else "NOT-PAID",
            "params": {
                "currency": "INR",
                "amount": str(round(grand_total, 2)),
                "transaction_id": order.get("payment_transaction_id") or order.ondc_order_id,
            },
            "@ondc/org/buyer_app_finder_fee_type": "percent",
            "@ondc/org/buyer_app_finder_fee_amount": str(settings.get("buyer_finder_fee") or "3"),
            "@ondc/org/settlement_basis": "delivery",
            "@ondc/org/settlement_window": "P2D",
            "@ondc/org/withholding_amount": "0.00",
            "@ondc/org/settlement_details": [{
                "settlement_counterparty": "seller-app",
                "settlement_phase": "sale-amount",
                "settlement_type": "neft",
                "beneficiary_name": settings.legal_entity_name or "",
                "settlement_bank_account_no": settings.get("settlement_bank_account") or "",
                "settlement_ifsc_code": settings.get("settlement_ifsc_code") or "",
                "bank_name": settings.get("settlement_bank_name") or "",
                "branch_name": settings.get("settlement_branch_name") or "",
            }],
        }
        _t("7.payment_ok")

        # ── 8. Billing ──
        billing_obj = {
            "name": order.billing_name or order.customer_name or "",
            "address": {
                "building": order.get("billing_building") or "",
                "locality": order.get("billing_locality") or "",
                "city": order.get("billing_city") or "",
                "state": order.get("billing_state") or "",
                "country": "IND",
                "area_code": order.get("billing_area_code") or "",
                "name": bap_data.get("billing_address_name") or order.billing_name or "",
            },
            "email": order.customer_email or "",
            "phone": order.customer_phone or "",
            "tax_number": order.get("billing_tax_number") or "",
            "created_at": bap_data.get("billing_created_at") or to_rfc3339(order.creation),
            "updated_at": bap_data.get("billing_updated_at") or to_rfc3339(order.modified),
        }
        _t(f"8.billing name={billing_obj['name'][:30]}")

        # ── 9. Assemble order payload ──
        order_payload = {
            "id": order.ondc_order_id,
            "state": order_state,
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items_list,
            "billing": billing_obj,
            "fulfillments": fulfillments_list,
            "quote": {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "P1D",
            },
            "payment": payment_obj,
            "created_at": to_rfc3339(order.creation),
            "updated_at": now_str,
        }

        # Add cancellation_terms if partial cancel exists (Flow 3A requirement)
        if has_partial_cancel:
            order_payload["cancellation_terms"] = [
                {
                    "fulfillment_state": {"descriptor": {"code": "Pending", "short_desc": "Pending"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0.00"}},
                    "reason_required": False,
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Packed", "short_desc": "Packed"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0.00"}},
                    "reason_required": True,
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Order-picked-up", "short_desc": "Order-picked-up"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0.00"}},
                    "reason_required": True,
                },
            ]

        _t(f"9.payload keys={sorted(order_payload.keys())}")

        # ── 10. Send callback ──
        context = client.create_context("on_status", data.get("context"))
        payload = {"context": context, "message": {"order": order_payload}}

        # Log full payload for debugging
        try:
            frappe.log_error(
                title=f"on_status PAYLOAD {fulfillment_state}",
                message=json.dumps(payload, indent=2, default=str)[:20000],
            )
        except Exception:
            pass

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_status",
            payload,
        )
        _t(f"11.sent result={result}")
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        _t(f"ERR: {e}")
        frappe.log_error(
            title="ONDC process_status Error",
            message=f"Trace: {trace}\n\n{traceback.format_exc()}"
        )
        _update_webhook_log(log_name, status="Failed", error_message=str(e))
    finally:
        # Always log the trace
        try:
            frappe.log_error(
                title="on_status trace",
                message=" | ".join(trace),
            )
        except Exception:
            pass


def process_track(data, log_name=None):
    """Process track request with proper trackable states and location"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        from datetime import datetime

        order_id = data.get("message", {}).get("order_id")
        if not order_id:
            _update_webhook_log(log_name, status="Failed", error_message="Missing order_id")
            return

        order_name = frappe.db.get_value("ONDC Order", {"ondc_order_id": order_id}, "name")
        if not order_name:
            _update_webhook_log(log_name, status="Failed", error_message=f"Order not found: {order_id}")
            return
        order = frappe.get_doc("ONDC Order", order_name)

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_track", data.get("context"))

        fulfillment_state = order.get("fulfillment_state") or "Pending"
        trackable_states = ["Agent-assigned", "At-pickup", "Order-picked-up", "Out-for-delivery"]

        is_active = fulfillment_state in trackable_states

        tracking_data = {
            "status": "active" if is_active else "inactive",
        }

        if order.get("tracking_url"):
            tracking_data["url"] = order.tracking_url

        # Add location for active tracking
        if is_active:
            tracking_data["location"] = {
                "gps": order.get("shipping_gps") or settings.get("store_gps") or "0.0,0.0",
                "time": {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            }

        payload = {
            "context": context,
            "message": {"tracking": tracking_data},
        }

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_track",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_track Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_cancel(data, log_name=None):
    """Process cancel request with ONDC-compliant cancellation structure"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        from datetime import datetime

        message = data.get("message", {})
        order_id = message.get("order_id")
        cancellation_reason_id = str(message.get("cancellation_reason_id", ""))

        if not order_id:
            _update_webhook_log(log_name, status="Failed", error_message="Missing order_id")
            return

        order_name = frappe.db.get_value("ONDC Order", {"ondc_order_id": order_id}, "name")
        if not order_name:
            _update_webhook_log(log_name, status="Failed", error_message=f"Order not found: {order_id}")
            return
        order = frappe.get_doc("ONDC Order", order_name)

        if order.order_status in ("Completed", "Cancelled"):
            _update_webhook_log(log_name, status="Failed", error_message=f"Order cannot be cancelled (status: {order.order_status})")
            return

        # Store pre-cancel state
        precancel_state = order.get("fulfillment_state") or "Pending"

        # Determine who cancelled
        # Reason codes 001-005 are buyer-initiated, 006+ are seller-initiated
        bap_id = data.get("context", {}).get("bap_id", "")
        try:
            reason_num = int(cancellation_reason_id)
            cancelled_by = bap_id if reason_num <= 5 else (order.get("bpp_id") or frappe.db.get_single_value("ONDC Settings", "subscriber_id"))
        except (ValueError, TypeError):
            cancelled_by = bap_id

        # Update order
        order.order_status = "Cancelled"
        order.fulfillment_state = "Cancelled"
        order.cancellation_reason_id = cancellation_reason_id
        order.save(ignore_permissions=True)
        frappe.db.commit()

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_cancel", data.get("context"))

        store_gps = settings.get("store_gps") or "0.0,0.0"
        store_name = settings.get("store_name") or settings.legal_entity_name or "ONDC Seller"
        location_id = f"LOC-{settings.city}"

        # Build items
        items = []
        quote_breakup = []
        item_total = 0.0
        tax_rate = float(settings.get("default_tax_rate") or 0)
        total_tax = 0.0

        for item in order.items:
            price = float(item.price or 0)
            qty = int(item.quantity or 1)
            line_total = price * qty
            item_total += line_total

            items.append({
                "id": item.ondc_item_id,
                "fulfillment_id": order.fulfillment_id or "F1",
                "quantity": {"count": qty},
            })

            quote_breakup.append({
                "title": item.ondc_item_id,
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/item_quantity": {"count": qty},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
                "item": {"price": {"currency": "INR", "value": str(price)}},
            })

            item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
            total_tax += item_tax
            quote_breakup.append({
                "title": "Tax",
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/title_type": "tax",
                "price": {"currency": "INR", "value": str(item_tax)},
            })

        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        packing_charge = float(settings.get("default_packing_charge") or 0)
        quote_breakup.append({
            "title": "Packing charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "packing",
            "price": {"currency": "INR", "value": str(packing_charge)},
        })

        grand_total = item_total + total_tax + delivery_charge + packing_charge

        # Build cancellation response
        order_payload = {
            "id": order.ondc_order_id,
            "state": "Cancelled",
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items,
            "billing": {
                "name": order.billing_name or order.customer_name or "",
                "address": {
                    "building": order.billing_building or "",
                    "locality": order.billing_locality or "",
                    "city": order.billing_city or "",
                    "state": order.billing_state or "",
                    "country": "IND",
                    "area_code": order.billing_area_code or "",
                },
                "email": order.customer_email or "",
                "phone": order.customer_phone or "",
            },
            "cancellation": {
                "cancelled_by": cancelled_by,
                "reason": {
                    "id": cancellation_reason_id,
                },
            },
            "quote": {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "P1D",
            },
            "fulfillments": [
                {
                    "id": order.fulfillment_id or "F1",
                    "type": order.fulfillment_type or "Delivery",
                    "state": {"descriptor": {"code": "Cancelled"}},
                    "start": {
                        "location": {
                            "id": location_id,
                            "descriptor": {"name": store_name},
                            "gps": store_gps,
                        },
                    },
                    "tags": [
                        {
                            "code": "cancel_request",
                            "list": [
                                {"code": "reason_id", "value": cancellation_reason_id},
                                {"code": "initiated_by", "value": cancelled_by},
                            ],
                        },
                        {
                            "code": "precancel_state",
                            "list": [
                                {"code": "fulfillment_state", "value": precancel_state},
                                {"code": "updated_at", "value": datetime.utcnow().isoformat() + "Z"},
                            ],
                        },
                    ],
                }
            ],
            "payment": {
                "type": order.payment_type or "ON-ORDER",
                "collected_by": "BAP" if (order.payment_type or "ON-ORDER") != "ON-FULFILLMENT" else "BPP",
                "status": "NOT-PAID",
                "@ondc/org/buyer_app_finder_fee_type": "percent",
                "@ondc/org/buyer_app_finder_fee_amount": str(settings.get("buyer_finder_fee") or "3"),
                "@ondc/org/settlement_basis": "delivery",
                "@ondc/org/settlement_window": "P2D",
                "@ondc/org/withholding_amount": "0.00",
                "@ondc/org/settlement_details": [
                    {
                        "settlement_counterparty": "seller-app",
                        "settlement_phase": "sale-amount",
                        "settlement_type": "neft",
                        "beneficiary_name": settings.legal_entity_name or "",
                        "settlement_bank_account_no": settings.get("settlement_bank_account") or "",
                        "settlement_ifsc_code": settings.get("settlement_ifsc_code") or "",
                        "bank_name": settings.get("settlement_bank_name") or "",
                        "branch_name": settings.get("settlement_branch_name") or "",
                    },
                    {
                        "settlement_counterparty": "buyer-app",
                        "settlement_phase": "refund",
                        "settlement_type": "neft",
                        "settlement_amount": str(round(grand_total, 2)),
                    },
                ],
            },
            "cancellation_terms": [
                {
                    "fulfillment_state": {"descriptor": {"code": "Pending"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0"}},
                    "reason_required": False,
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Packed"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0"}},
                    "reason_required": True,
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Order-picked-up"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0"}},
                    "reason_required": True,
                },
                {
                    "fulfillment_state": {"descriptor": {"code": "Out-for-delivery"}},
                    "cancellation_fee": {"percentage": "0", "amount": {"currency": "INR", "value": "0"}},
                    "reason_required": True,
                },
            ],
            "tags": [
                {
                    "code": "bpp_terms",
                    "list": [
                        {"code": "np_type", "value": "MSN"},
                        {"code": "accept_bap_terms", "value": "Y"},
                        {"code": "collect_payment", "value": "Y"},
                        {"code": "max_liability", "value": "2"},
                        {"code": "max_liability_cap", "value": "10000"},
                        {"code": "mandatory_arbitration", "value": "false"},
                        {"code": "court_jurisdiction", "value": "Bengaluru"},
                        {"code": "delay_interest", "value": "1000"},
                    ],
                },
            ],
            "created_at": str(order.creation) if order.creation else "",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

        payload = {
            "context": context,
            "message": {"order": order_payload},
        }

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_cancel",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_cancel Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_update(data, log_name=None):
    """Process update request with ONDC-compliant response structure"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        from datetime import datetime

        update_target = data.get("message", {}).get("update_target", "")
        order_data = data.get("message", {}).get("order", {})
        order_id = order_data.get("id")

        if not order_id:
            _update_webhook_log(log_name, status="Failed", error_message="Missing order.id in update")
            return

        order_name = frappe.db.get_value("ONDC Order", {"ondc_order_id": order_id}, "name")
        if not order_name:
            _update_webhook_log(log_name, status="Failed", error_message=f"Order not found: {order_id}")
            return
        order = frappe.get_doc("ONDC Order", order_name)

        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_update", data.get("context"))

        # Handle fulfillment update
        if update_target == "fulfillment":
            fulfillments = order_data.get("fulfillments", [])
            if fulfillments:
                new_state = fulfillments[0].get("state", {}).get("descriptor", {}).get("code")
                if new_state and is_valid_fulfillment_transition(
                    order.get("fulfillment_state") or "Pending", new_state
                ):
                    order.fulfillment_state = new_state
                    order.save(ignore_permissions=True)
                    frappe.db.commit()

        elif update_target == "item":
            for item_update in order_data.get("items", []):
                for order_item in order.items:
                    if order_item.ondc_item_id == item_update.get("id"):
                        new_qty = item_update.get("quantity", {}).get("count")
                        if new_qty is not None:
                            order_item.quantity = int(new_qty)
            order.save(ignore_permissions=True)
            frappe.db.commit()

        fulfillment_state = order.get("fulfillment_state") or "Pending"
        state_map = {
            "Pending": "Accepted",
            "Packed": "In-progress",
            "Agent-assigned": "In-progress",
            "At-pickup": "In-progress",
            "Order-picked-up": "In-progress",
            "Out-for-delivery": "In-progress",
            "Order-delivered": "Completed",
            "Cancelled": "Cancelled",
        }
        order_state = state_map.get(fulfillment_state, order.order_status or "Accepted")

        # Build items with per-item tax
        items = []
        quote_breakup = []
        item_total = 0.0
        tax_rate = float(settings.get("default_tax_rate") or 0)
        total_tax = 0.0

        for item in order.items:
            price = float(item.price or 0)
            qty = int(item.quantity or 1)
            line_total = price * qty
            item_total += line_total

            items.append({
                "id": item.ondc_item_id,
                "fulfillment_id": order.fulfillment_id or "F1",
                "quantity": {"count": qty},
            })

            quote_breakup.append({
                "title": item.ondc_item_id,
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/item_quantity": {"count": qty},
                "@ondc/org/title_type": "item",
                "price": {"currency": "INR", "value": str(line_total)},
                "item": {"price": {"currency": "INR", "value": str(price)}},
            })

            item_tax = round(line_total * tax_rate / 100, 2) if tax_rate > 0 else 0
            total_tax += item_tax
            quote_breakup.append({
                "title": "Tax",
                "@ondc/org/item_id": item.ondc_item_id,
                "@ondc/org/title_type": "tax",
                "price": {"currency": "INR", "value": str(item_tax)},
            })

        delivery_charge = float(settings.get("default_delivery_charge") or 0)
        quote_breakup.append({
            "title": "Delivery charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "delivery",
            "price": {"currency": "INR", "value": str(delivery_charge)},
        })

        packing_charge = float(settings.get("default_packing_charge") or 0)
        quote_breakup.append({
            "title": "Packing charges",
            "@ondc/org/item_id": "F1",
            "@ondc/org/title_type": "packing",
            "price": {"currency": "INR", "value": str(packing_charge)},
        })

        grand_total = item_total + total_tax + delivery_charge + packing_charge

        store_gps = settings.get("store_gps") or "0.0,0.0"
        store_name = settings.get("store_name") or settings.legal_entity_name or "ONDC Seller"
        location_id = f"LOC-{settings.city}"

        now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")

        # Extract BAP's stored data
        bap_data = {}
        try:
            raw_bap = order.get("custom_bap_data")
            if raw_bap:
                bap_data = json.loads(raw_bap) if isinstance(raw_bap, str) else (raw_bap if isinstance(raw_bap, dict) else {})
        except (json.JSONDecodeError, TypeError):
            pass

        # Build fulfillment
        fulfillment_obj = {
            "id": order.fulfillment_id or "F1",
            "type": order.fulfillment_type or "Delivery",
            "@ondc/org/provider_name": store_name,
            "@ondc/org/TAT": settings.get("default_time_to_ship") or "PT60M",
            "tracking": bool(order.get("tracking_url")),
            "state": {"descriptor": {"code": fulfillment_state}},
            "start": {
                "location": {
                    "id": location_id,
                    "descriptor": {"name": store_name},
                    "gps": store_gps,
                    "address": {
                        "locality": settings.get("store_locality") or "",
                        "city": settings.get("store_city_name") or settings.city or "",
                        "state": settings.get("store_state") or "",
                        "country": "IND",
                        "area_code": settings.get("store_area_code") or settings.city or "",
                    },
                },
                "contact": {
                    "phone": settings.get("consumer_care_phone") or "",
                    "email": settings.get("consumer_care_email") or "",
                },
                "instructions": {
                    "code": "PICKUP_INSTRUCTIONS",
                    "name": "Pickup Instructions",
                    "short_desc": "Please collect from store",
                    "long_desc": "Pickup is available during store operating hours.",
                    "images": [],
                },
                "time": {
                    "range": {"start": now_str, "end": now_str},
                    "timestamp": now_str,
                },
            },
            "tags": [
                {"code": "routing", "list": [{"code": "type", "value": "P2P"}]},
            ],
        }

        # End location
        if order.get("shipping_gps") or order.get("shipping_address"):
            end_address = {}
            if order.get("shipping_address"):
                try:
                    end_address = json.loads(order.shipping_address) if isinstance(order.shipping_address, str) else (order.shipping_address if isinstance(order.shipping_address, dict) else {})
                except (json.JSONDecodeError, TypeError):
                    end_address = {}
            fulfillment_obj["end"] = {
                "location": {
                    "gps": order.get("shipping_gps") or "",
                    "address": end_address,
                },
                "person": {"name": order.customer_name or ""},
                "contact": {
                    "phone": order.customer_phone or "",
                    "email": order.customer_email or "",
                },
                "time": {"range": {"start": now_str, "end": now_str}},
            }

        # Payment with params
        payment_obj = {
            "type": order.payment_type or "ON-ORDER",
            "collected_by": "BAP" if (order.payment_type or "ON-ORDER") != "ON-FULFILLMENT" else "BPP",
            "status": "PAID" if order.get("payment_status") == "Paid" else "NOT-PAID",
            "params": {
                "currency": "INR",
                "amount": str(round(grand_total, 2)),
                "transaction_id": order.get("payment_transaction_id") or order.ondc_order_id,
            },
            "@ondc/org/buyer_app_finder_fee_type": "percent",
            "@ondc/org/buyer_app_finder_fee_amount": str(settings.get("buyer_finder_fee") or "3"),
            "@ondc/org/settlement_basis": "delivery",
            "@ondc/org/settlement_window": "P2D",
            "@ondc/org/withholding_amount": "0.00",
            "@ondc/org/settlement_details": [
                {
                    "settlement_counterparty": "seller-app",
                    "settlement_phase": "sale-amount",
                    "settlement_type": "neft",
                    "beneficiary_name": settings.legal_entity_name or "",
                    "settlement_bank_account_no": settings.get("settlement_bank_account") or "",
                    "settlement_ifsc_code": settings.get("settlement_ifsc_code") or "",
                    "bank_name": settings.get("settlement_bank_name") or "",
                    "branch_name": settings.get("settlement_branch_name") or "",
                }
            ],
        }

        order_payload = {
            "id": order.ondc_order_id,
            "state": order_state,
            "provider": {
                "id": settings.subscriber_id,
                "locations": [{"id": location_id}],
            },
            "items": items,
            "billing": {
                "name": order.billing_name or order.customer_name or "",
                "address": {
                    "building": order.get("billing_building") or "",
                    "locality": order.get("billing_locality") or "",
                    "city": order.get("billing_city") or "",
                    "state": order.get("billing_state") or "",
                    "country": "IND",
                    "area_code": order.get("billing_area_code") or "",
                    "name": bap_data.get("billing_address_name") or order.billing_name or "",
                },
                "email": order.customer_email or "",
                "phone": order.customer_phone or "",
                "tax_number": order.get("billing_tax_number") or "",
                "created_at": bap_data.get("billing_created_at") or to_rfc3339(order.creation),
                "updated_at": bap_data.get("billing_updated_at") or to_rfc3339(order.modified),
            },
            "fulfillments": [fulfillment_obj],
            "quote": {
                "price": {"currency": "INR", "value": str(round(grand_total, 2))},
                "breakup": quote_breakup,
                "ttl": "P1D",
            },
            "payment": payment_obj,
            "created_at": to_rfc3339(order.creation),
            "updated_at": to_rfc3339(order.modified),
        }

        payload = {
            "context": context,
            "message": {"order": order_payload},
        }

        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_update",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)

    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_update Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_rating(data, log_name=None):
    """Process rating request and send on_rating callback"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        
        ratings = data.get("message", {}).get("ratings", [])
        
        # Store ratings (could be extended to a dedicated DocType)
        for rating in ratings:
            frappe.get_doc({
                "doctype": "Comment",
                "comment_type": "Info",
                "reference_doctype": "ONDC Order",
                "reference_name": rating.get("id"),
                "content": f"ONDC Rating: {rating.get('value', 'N/A')} - {rating.get('feedback_form', {}).get('question', '')}",
            }).insert(ignore_permissions=True)
        
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_rating", data.get("context"))
        
        payload = {
            "context": context,
            "message": {
                "feedback_ack": True,
                "rating_ack": True,
            },
        }
        
        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_rating",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)
    
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_rating Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_support(data, log_name=None):
    """Process support request and send on_support callback with contact details from settings"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        context = client.create_context("on_support", data.get("context"))
        
        payload = {
            "context": context,
            "message": {
                "phone": settings.get("consumer_care_phone") or "",
                "email": settings.get("consumer_care_email") or "",
                "uri": settings.get("subscriber_url") or "",
            },
        }
        
        result = client.send_callback(
            data.get("context", {}).get("bap_uri"),
            "/on_support",
            payload,
        )
        _update_webhook_log(log_name, status="Processed", response=result)
    
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_support Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def get_item_code_from_ondc_id(ondc_id):
    """Get Frappe Item code from ONDC product ID"""
    product = frappe.db.get_value(
        "ONDC Product", {"ondc_product_id": ondc_id}, "item_code"
    )
    return product or ondc_id


def _map_payment_type(ondc_type):
    """Map ONDC payment type to local select options"""
    mapping = {
        "PRE-FULFILLMENT": "Prepaid",
        "ON-FULFILLMENT": "COD",
        "POST-FULFILLMENT": "Credit",
        "ON-ORDER": "Prepaid",
    }
    return mapping.get(ondc_type, "Prepaid")


def _json_response(data, status_code):
    """Create a JSON HTTP response"""
    return Response(
        json.dumps(data),
        status=status_code,
        mimetype="application/json",
    )


def _log_webhook(api, data, status="Received", error_message=None):
    """Create a webhook log entry and return the log name"""
    try:
        log = frappe.new_doc("ONDC Webhook Log")
        log.webhook_type = api
        log.request_id = data.get("context", {}).get("message_id")
        log.transaction_id = data.get("context", {}).get("transaction_id")
        log.message_id = data.get("context", {}).get("message_id")
        log.request_body = json.dumps(data, indent=2)
        log.status = status
        if error_message:
            log.error_message = error_message
        log.insert(ignore_permissions=True)
        frappe.db.commit()
        return log.name
    except Exception:
        frappe.log_error(traceback.format_exc(), "ONDC Webhook Log Error")
        return None


def _update_webhook_log(log_name, status=None, response=None, error_message=None):
    """Update an existing webhook log entry"""
    if not log_name:
        return
    try:
        log = frappe.get_doc("ONDC Webhook Log", log_name)
        if status:
            log.status = status
        if response:
            log.response_body = json.dumps(response, indent=2) if isinstance(response, dict) else str(response)
        if error_message:
            log.error_message = error_message
        log.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(traceback.format_exc(), "ONDC Webhook Log Update Error")


# ---------------------------------------------------------------------------
# IGM (Issue & Grievance Management) Handlers
# ---------------------------------------------------------------------------

def process_issue(data, log_name=None):
    """Process /issue request - creates ticket in Helpdesk"""
    try:
        from ondc_seller_app.api.igm_adapter import IGMAdapter

        adapter = IGMAdapter()
        result = adapter.handle_issue(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_issue Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


def process_issue_status(data, log_name=None):
    """Process /issue_status request - returns ticket status"""
    try:
        from ondc_seller_app.api.igm_adapter import IGMAdapter

        adapter = IGMAdapter()
        result = adapter.handle_issue_status(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_issue_status Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


# ---------------------------------------------------------------------------
# RSP (Reconciliation & Settlement Protocol) Handlers
# ---------------------------------------------------------------------------

def process_receiver_recon(data, log_name=None):
    """Process /receiver_recon request - reconciles settlements"""
    try:
        from ondc_seller_app.api.rsp_adapter import RSPAdapter

        adapter = RSPAdapter()
        result = adapter.handle_receiver_recon(data)
        _update_webhook_log(log_name, status="Processed", response=result)
    except Exception as e:
        frappe.log_error(traceback.format_exc(), "ONDC process_receiver_recon Error")
        _update_webhook_log(log_name, status="Failed", error_message=str(e))


# ---------------------------------------------------------------------------
# Root-level ONDC endpoint wrappers
# ---------------------------------------------------------------------------
# These are individually whitelisted functions that Frappe exposes at:
#   /api/method/ondc_seller_app.api.webhook.<action>
# They also serve as targets for website_route_rules at root level:
#   /search -> handle_search -> handle_webhook("search")
#
# This ensures routing works reliably on Frappe Cloud where
# website_route_rules with <path:api> wildcards may fail.
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def handle_search(**kwargs):
    """Root-level /search endpoint"""
    handle_webhook("search")

@frappe.whitelist(allow_guest=True)
def handle_select(**kwargs):
    """Root-level /select endpoint"""
    handle_webhook("select")

@frappe.whitelist(allow_guest=True)
def handle_init(**kwargs):
    """Root-level /init endpoint"""
    handle_webhook("init")

@frappe.whitelist(allow_guest=True)
def handle_confirm(**kwargs):
    """Root-level /confirm endpoint"""
    handle_webhook("confirm")

@frappe.whitelist(allow_guest=True)
def handle_status(**kwargs):
    """Root-level /status endpoint"""
    handle_webhook("status")

@frappe.whitelist(allow_guest=True)
def handle_track(**kwargs):
    """Root-level /track endpoint"""
    handle_webhook("track")

@frappe.whitelist(allow_guest=True)
def handle_cancel(**kwargs):
    """Root-level /cancel endpoint"""
    handle_webhook("cancel")

@frappe.whitelist(allow_guest=True)
def handle_update(**kwargs):
    """Root-level /update endpoint"""
    handle_webhook("update")

@frappe.whitelist(allow_guest=True)
def handle_rating(**kwargs):
    """Root-level /rating endpoint"""
    handle_webhook("rating")

@frappe.whitelist(allow_guest=True)
def handle_support(**kwargs):
    """Root-level /support endpoint"""
    handle_webhook("support")


@frappe.whitelist(allow_guest=True)
def get_recent_errors(limit=20, method_filter=None):
    """Diagnostic: return most recent Error Log entries"""
    filters = {}
    if method_filter:
        filters["method"] = ["like", f"%{method_filter}%"]
    logs = frappe.get_all(
        "Error Log",
        filters=filters,
        fields=["name", "method", "error", "creation"],
        order_by="creation desc",
        limit=int(limit)
    )
    return logs


@frappe.whitelist(allow_guest=True)
def get_recent_webhooks(limit=10):
    """Diagnostic: return most recent ONDC Webhook Log entries"""
    logs = frappe.get_all(
        "ONDC Webhook Log",
        fields=["name", "action", "status", "creation", "bap_id", "error_message"],
        order_by="creation desc",
        limit=int(limit)
    )
    return logs


@frappe.whitelist(allow_guest=True)
def debug_catalog():
    """Diagnostic: build catalog and return it (or the error) synchronously"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)
        catalog = client.build_catalog()
        return {"success": True, "catalog": catalog}
    except Exception as e:
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@frappe.whitelist(allow_guest=True)
def get_error_logs_exact(method_name, limit=10):
    """Diagnostic: fetch Error Log entries with exact method match via SQL"""
    try:
        rows = frappe.db.sql(
            """SELECT name, method, LEFT(error, 2000) as error, creation
               FROM `tabError Log`
               WHERE method = %s
               ORDER BY creation DESC
               LIMIT %s""",
            (method_name, int(limit)),
            as_dict=True,
        )
        return {"success": True, "count": len(rows), "rows": rows}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def gateway_callback_diagnostic():
    """Diagnostic: send a minimal on_search to pre-prod.gcr.ondc.org and return
    the exact Authorization header we send plus the full gateway response body.
    This helps identify why we get HTTP 401 from the ONDC gateway."""
    import base64, hashlib, nacl.signing, requests as http_requests
    from datetime import datetime as _dt, timedelta as _td

    try:
        settings = frappe.get_single("ONDC Settings")
        results = {
            "subscriber_id": settings.subscriber_id,
            "unique_key_id": settings.unique_key_id,
            "subscriber_url": settings.subscriber_url,
        }

        # Build a minimal on_search payload (what we'd send to the gateway)
        import uuid as _uuid
        payload = {
            "context": {
                "domain": "ONDC:RET11",
                "country": "IND",
                "city": "std:080",
                "action": "on_search",
                "core_version": "1.2.0",
                "bap_id": "pramaan.ondc.org",
                "bap_uri": "https://pre-prod.gcr.ondc.org",
                "bpp_id": settings.subscriber_id,
                "bpp_uri": settings.subscriber_url,
                "transaction_id": str(_uuid.uuid4()),
                "message_id": str(_uuid.uuid4()),
                "timestamp": _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "ttl": "PT30S",
            },
            "message": {"catalog": {}}
        }

        # Serialize exactly as send_callback does
        body_bytes = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
        body_str = body_bytes.decode('utf-8')
        results["body_length"] = len(body_bytes)
        results["body_preview"] = body_str[:200]

        # Compute digest
        digest = hashlib.blake2b(body_bytes, digest_size=64).digest()
        digest_b64 = base64.b64encode(digest).decode()
        results["digest_b64"] = digest_b64

        # Build signing string
        created = int(_dt.utcnow().timestamp())
        expires = int((_dt.utcnow() + _td(minutes=5)).timestamp())
        signing_string = (
            f"(created): {created}\n"
            f"(expires): {expires}\n"
            f"digest: BLAKE-512={digest_b64}"
        )
        results["signing_string"] = signing_string

        # Sign
        priv_key_b64 = settings.get_password("signing_private_key")
        raw_key = base64.b64decode(priv_key_b64)
        if len(raw_key) == 64:
            seed = raw_key[:32]
        elif len(raw_key) == 32:
            seed = raw_key
        else:
            seed = raw_key[-32:]
        sk = nacl.signing.SigningKey(seed)
        signature = sk.sign(signing_string.encode()).signature
        sig_b64 = base64.b64encode(signature).decode()

        auth_header = (
            f'Signature keyId="{settings.subscriber_id}|{settings.unique_key_id}|ed25519",'
            f'algorithm="ed25519",'
            f'created="{created}",'
            f'expires="{expires}",'
            f'headers="(created) (expires) digest",'
            f'signature="{sig_b64}"'
        )
        results["auth_header"] = auth_header

        # Self-verify the signature we just produced
        try:
            vk = nacl.signing.VerifyKey(base64.b64decode(settings.signing_public_key))
            vk.verify(signing_string.encode(), signature)
            results["self_verify"] = "PASS"
        except Exception as ve:
            results["self_verify"] = f"FAIL: {ve}"

        # Send to gateway
        gateway_url = "https://pre-prod.gcr.ondc.org/on_search"
        results["gateway_url"] = gateway_url
        try:
            resp = http_requests.post(
                gateway_url,
                data=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": auth_header,
                },
                timeout=15,
            )
            results["gateway_status"] = resp.status_code
            results["gateway_response"] = resp.text[:500]
            results["gateway_response_headers"] = dict(resp.headers)
        except Exception as re:
            results["gateway_request_error"] = str(re)

        return results
    except Exception as e:
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@frappe.whitelist(allow_guest=True)
def send_test_on_search():
    """Diagnostic: fire a synchronous on_search to pramaan BAP URI and return result"""
    try:
        from ondc_seller_app.api.ondc_client import ONDCClient
        settings = frappe.get_single("ONDC Settings")
        client = ONDCClient(settings)

        # Minimal fake search request mimicking a Pramaan Flow 3A search
        fake_search = {
            "context": {
                "domain": "ONDC:RET11",
                "country": "IND",
                "city": "std:080",
                "action": "search",
                "core_version": "1.2.0",
                "bap_id": "pramaan.ondc.org",
                "bap_uri": "https://pramaan.ondc.org/beta/preprod/mock/buyer",
                "bpp_id": settings.subscriber_id,
                "bpp_uri": settings.subscriber_url,
                "transaction_id": "test-diag-" + frappe.generate_hash(length=8),
                "message_id": "test-diag-" + frappe.generate_hash(length=8),
                "timestamp": "2026-03-12T00:00:00.000Z",
                "ttl": "PT30S",
            },
            "message": {
                "intent": {
                    "fulfillment": {"type": "Delivery"},
                    "payment": {"@ondc/org/buyer_app_finder_fee_type": "percent",
                                "@ondc/org/buyer_app_finder_fee_amount": "3"},
                    "tags": [{"code": "bap_terms",
                              "list": [{"code": "finder_fee_type", "value": "percent"},
                                       {"code": "finder_fee_amount", "value": "3"}]}]
                }
            }
        }

        result = client.on_search(fake_search)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@frappe.whitelist(allow_guest=True)
def vlookup_gateway_diagnostic():
    """NEW v2 diagnostic (bypasses module cache): vlookup + gateway test + signing self-check.

    Three checks in one call:
    1. /vlookup (exact path the ONDC gateway uses: country+domain+city+subscriber_id+ukId)
    2. /v2.0/lookup (broader, for key + status inspection)
    3. Direct on_search POST to pre-prod.gcr.ondc.org to capture exact gateway response
    """
    import base64
    import hashlib
    import nacl.signing
    import requests as _http
    import uuid as _uuid
    from datetime import datetime as _dt, timedelta as _td

    settings = frappe.get_single("ONDC Settings")
    results = {
        "version": "vlookup_gateway_diagnostic_v1",
        "subscriber_id": settings.subscriber_id,
        "unique_key_id": settings.unique_key_id,
        "local_public_key": settings.signing_public_key,
        "environment": settings.environment,
    }

    registry_base = {
        "staging": "https://staging.registry.ondc.org",
        "preprod": "https://preprod.registry.ondc.org",
        "prod": "https://prod.registry.ondc.org",
    }.get(settings.environment, "https://preprod.registry.ondc.org")

    def _make_auth(body_bytes_inner):
        digest_inner = hashlib.blake2b(body_bytes_inner, digest_size=64).digest()
        digest_b64_inner = base64.b64encode(digest_inner).decode()
        ts_created = int(_dt.utcnow().timestamp())
        ts_expires = int((_dt.utcnow() + _td(minutes=5)).timestamp())
        ss = (f"(created): {ts_created}\n"
              f"(expires): {ts_expires}\n"
              f"digest: BLAKE-512={digest_b64_inner}")
        priv_key_b64 = settings.get_password("signing_private_key")
        raw_key = base64.b64decode(priv_key_b64)
        if len(raw_key) == 64:
            seed = raw_key[:32]
        elif len(raw_key) == 32:
            seed = raw_key
        else:
            seed = raw_key[-32:]
        sk = nacl.signing.SigningKey(seed)
        sig = sk.sign(ss.encode()).signature
        sig_b64 = base64.b64encode(sig).decode()
        hdr = (f'Signature keyId="{settings.subscriber_id}|{settings.unique_key_id}|ed25519",'
               f'algorithm="ed25519",created="{ts_created}",expires="{ts_expires}",'
               f'headers="(created) (expires) digest",signature="{sig_b64}"')
        vk = nacl.signing.VerifyKey(base64.b64decode(settings.signing_public_key))
        try:
            vk.verify(ss.encode(), sig)
            sv = "PASS"
        except Exception as sve:
            sv = f"FAIL:{sve}"
        return hdr, sv, ss

    # ── 1. /vlookup — exact path the ONDC gateway uses ───────────────────
    vlookup_payload = {
        "country": "IND",
        "domain": settings.domain,
        "type": "BPP",
        "city": settings.city or "std:080",
        "subscriber_id": settings.subscriber_id,
        "ukId": settings.unique_key_id,
    }
    vl_bytes = json.dumps(vlookup_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    results["vlookup_request"] = json.dumps(vlookup_payload)
    try:
        vl_auth, vl_sv, _ = _make_auth(vl_bytes)
        results["vlookup_self_verify"] = vl_sv
        vl_resp = _http.post(
            f"{registry_base}/vlookup",
            data=vl_bytes,
            headers={"Content-Type": "application/json", "Authorization": vl_auth},
            timeout=15,
        )
        results["vlookup_status"] = vl_resp.status_code
        try:
            vl_data = vl_resp.json()
            results["vlookup_count"] = len(vl_data) if isinstance(vl_data, list) else "dict"
            results["vlookup_raw"] = json.dumps(vl_data)[:1000]
            if isinstance(vl_data, list) and len(vl_data) == 0:
                results["VLOOKUP_DIAGNOSIS"] = (
                    "EMPTY — gateway cannot find our BPP. "
                    "city/domain/country/ukId don't match registry exactly. "
                    "Gateway will ALWAYS return 401 until this is fixed in ONDC portal."
                )
            elif isinstance(vl_data, list) and len(vl_data) > 0:
                entry = vl_data[0]
                results["vlookup_signing_key"] = entry.get("signing_public_key", "")
                results["vlookup_status_field"] = entry.get("status", "")
                results["vlookup_ukId"] = entry.get("ukId", "")
                results["vlookup_city"] = entry.get("city", "")
                results["vlookup_domain"] = entry.get("domain", "")
                results["vlookup_keys_match"] = (
                    entry.get("signing_public_key") == settings.signing_public_key
                )
                results["VLOOKUP_DIAGNOSIS"] = (
                    f"FOUND — status={entry.get('status')}, "
                    f"keys_match={entry.get('signing_public_key') == settings.signing_public_key}, "
                    f"ukId={entry.get('ukId')}"
                )
        except Exception as je:
            results["vlookup_raw"] = vl_resp.text[:500]
            results["vlookup_json_error"] = str(je)
    except Exception as e:
        results["vlookup_error"] = str(e)

    # ── 2. /v2.0/lookup — broader, for key + status inspection ───────────
    lookup_payload = {
        "subscriber_id": settings.subscriber_id,
        "type": "BPP",
        "domain": settings.domain,
    }
    body_bytes = json.dumps(lookup_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    try:
        auth_header, self_verify, _ = _make_auth(body_bytes)
        results["self_verify"] = self_verify
        resp = _http.post(
            f"{registry_base}/v2.0/lookup",
            data=body_bytes,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
            timeout=15,
        )
        results["registry_status"] = resp.status_code
        try:
            resp_data = resp.json()
            results["registry_response_summary"] = (
                f"{len(resp_data)} entries" if isinstance(resp_data, list) else "dict"
            )
            if isinstance(resp_data, list) and len(resp_data) > 0:
                entry0 = resp_data[0]
                results["registry_full_entry"] = json.dumps(entry0)[:1200]
                results["registry_status_field"] = entry0.get("status")
                results["registry_ukId"] = entry0.get("ukId") or entry0.get("unique_key_id")
                results["registry_city"] = entry0.get("city")
                results["registry_domain"] = entry0.get("domain")
                results["registry_public_key"] = entry0.get("signing_public_key", "")
                results["keys_match"] = (
                    entry0.get("signing_public_key") == settings.signing_public_key
                )
        except Exception:
            results["registry_raw"] = resp.text[:500]
    except Exception as e:
        results["lookup_error"] = str(e)

    # ── 3. Direct gateway test — POST minimal on_search ──────────────────
    try:
        gw_payload = {
            "context": {
                "domain": settings.domain or "ONDC:RET11",
                "country": "IND",
                "city": settings.city or "std:080",
                "action": "on_search",
                "core_version": "1.2.0",
                "bap_id": "pramaan.ondc.org",
                "bap_uri": "https://pre-prod.gcr.ondc.org",
                "bpp_id": settings.subscriber_id,
                "bpp_uri": settings.subscriber_url,
                "transaction_id": str(_uuid.uuid4()),
                "message_id": str(_uuid.uuid4()),
                "timestamp": _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "ttl": "PT30S",
            },
            "message": {"catalog": {}}
        }
        gw_bytes = json.dumps(gw_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        gw_auth, gw_sv, gw_ss = _make_auth(gw_bytes)
        results["gateway_self_verify"] = gw_sv
        results["gateway_signing_string"] = gw_ss
        results["gateway_auth_header"] = gw_auth
        gw_resp = _http.post(
            "https://pre-prod.gcr.ondc.org/on_search",
            data=gw_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": gw_auth,
            },
            timeout=15,
        )
        results["gateway_status"] = gw_resp.status_code
        results["gateway_response"] = gw_resp.text[:1000]
        results["gateway_response_headers"] = dict(gw_resp.headers)
    except Exception as e:
        results["gateway_error"] = str(e)

    return results
