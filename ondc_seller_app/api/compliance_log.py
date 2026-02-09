"""
ONDC Compliance Log Module

Provides network observability and compliance logging for ONDC requirements.
Tracks all API transactions, errors, and settlement activities.

ONDC requires sellers to maintain logs for:
- All API request/response pairs
- Error tracking and resolution
- Settlement reconciliation records
- SLA compliance metrics

Reference: ONDC Network Observability Guidelines
"""

import frappe
from frappe import _
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List


class ComplianceLogger:
    """
    Central logging system for ONDC compliance
    All API transactions are logged for audit and observability
    """

    LOG_TYPES = {
        "API": "API Transaction",
        "IGM": "Issue & Grievance",
        "RSP": "Settlement Reconciliation",
        "ERROR": "Error Log",
        "CATALOG": "Catalog Update",
        "ORDER": "Order Lifecycle"
    }

    def __init__(self):
        self.settings = frappe.get_single("ONDC Settings") if frappe.db.exists("ONDC Settings") else None

    def log_api_transaction(self, action: str, request_data: dict,
                           response_data: dict, status: str = "Success",
                           latency_ms: int = 0, error_message: str = None):
        """
        Log an API transaction for ONDC compliance

        Args:
            action: ONDC action (search, select, init, confirm, etc.)
            request_data: Incoming request payload
            response_data: Outgoing response payload
            status: Success/Failed/Pending
            latency_ms: Response time in milliseconds
            error_message: Error details if failed
        """
        try:
            context = request_data.get("context", {})

            log_entry = {
                "doctype": "ONDC Compliance Log",
                "log_type": "API",
                "action": action,
                "transaction_id": context.get("transaction_id"),
                "message_id": context.get("message_id"),
                "bap_id": context.get("bap_id"),
                "bpp_id": context.get("bpp_id"),
                "domain": context.get("domain"),
                "status": status,
                "latency_ms": latency_ms,
                "request_data": json.dumps(request_data, indent=2)[:65000],  # Limit size
                "response_data": json.dumps(response_data, indent=2)[:65000],
                "error_message": error_message,
                "timestamp": frappe.utils.now_datetime(),
                "ip_address": frappe.local.request_ip if hasattr(frappe.local, 'request_ip') else None
            }

            # Extract order ID if present
            message = request_data.get("message", {})
            if "order" in message:
                log_entry["order_id"] = message["order"].get("id")
            elif "order_id" in message:
                log_entry["order_id"] = message["order_id"]

            frappe.get_doc(log_entry).insert(ignore_permissions=True)

        except Exception as e:
            # Don't let logging errors break the main flow
            frappe.log_error(f"Compliance logging error: {str(e)}", "ONDC Compliance")

    def log_igm_transaction(self, issue_id: str, action: str,
                           request_data: dict, response_data: dict,
                           ticket_id: str = None, status: str = "Success"):
        """
        Log IGM (Issue & Grievance) transaction

        Args:
            issue_id: ONDC issue ID
            action: IGM action (issue, on_issue, issue_status, on_issue_status)
            request_data: Request payload
            response_data: Response payload
            ticket_id: Frappe Helpdesk ticket ID
            status: Transaction status
        """
        try:
            context = request_data.get("context", {})

            frappe.get_doc({
                "doctype": "ONDC Compliance Log",
                "log_type": "IGM",
                "action": action,
                "issue_id": issue_id,
                "ticket_id": ticket_id,
                "transaction_id": context.get("transaction_id"),
                "bap_id": context.get("bap_id"),
                "status": status,
                "request_data": json.dumps(request_data, indent=2)[:65000],
                "response_data": json.dumps(response_data, indent=2)[:65000],
                "timestamp": frappe.utils.now_datetime()
            }).insert(ignore_permissions=True)

        except Exception as e:
            frappe.log_error(f"IGM logging error: {str(e)}", "ONDC Compliance")

    def log_rsp_transaction(self, settlement_id: str, action: str,
                           order_id: str = None, amount: float = 0,
                           recon_status: str = None, status: str = "Success"):
        """
        Log RSP (Settlement) transaction

        Args:
            settlement_id: ONDC settlement ID
            action: RSP action (receiver_recon, on_receiver_recon)
            order_id: Related order ID
            amount: Settlement amount
            recon_status: Reconciliation status code
            status: Transaction status
        """
        try:
            frappe.get_doc({
                "doctype": "ONDC Compliance Log",
                "log_type": "RSP",
                "action": action,
                "settlement_id": settlement_id,
                "order_id": order_id,
                "amount": amount,
                "recon_status": recon_status,
                "status": status,
                "timestamp": frappe.utils.now_datetime()
            }).insert(ignore_permissions=True)

        except Exception as e:
            frappe.log_error(f"RSP logging error: {str(e)}", "ONDC Compliance")

    def log_error(self, error_code: str, error_message: str,
                 action: str = None, context: dict = None):
        """
        Log ONDC protocol errors

        Args:
            error_code: ONDC error code
            error_message: Error description
            action: Action that caused the error
            context: Request context
        """
        try:
            frappe.get_doc({
                "doctype": "ONDC Compliance Log",
                "log_type": "ERROR",
                "action": action,
                "error_code": error_code,
                "error_message": error_message,
                "transaction_id": context.get("transaction_id") if context else None,
                "bap_id": context.get("bap_id") if context else None,
                "status": "Failed",
                "timestamp": frappe.utils.now_datetime()
            }).insert(ignore_permissions=True)

        except Exception as e:
            frappe.log_error(f"Error logging error: {str(e)}", "ONDC Compliance")

    def log_order_lifecycle(self, order_id: str, from_state: str,
                           to_state: str, triggered_by: str = "system"):
        """
        Log order state transitions for audit trail

        Args:
            order_id: ONDC order ID
            from_state: Previous fulfillment state
            to_state: New fulfillment state
            triggered_by: Who triggered the transition
        """
        try:
            frappe.get_doc({
                "doctype": "ONDC Compliance Log",
                "log_type": "ORDER",
                "action": "state_transition",
                "order_id": order_id,
                "from_state": from_state,
                "to_state": to_state,
                "triggered_by": triggered_by,
                "status": "Success",
                "timestamp": frappe.utils.now_datetime()
            }).insert(ignore_permissions=True)

        except Exception as e:
            frappe.log_error(f"Order lifecycle logging error: {str(e)}", "ONDC Compliance")


def get_compliance_metrics(from_date: str = None, to_date: str = None) -> dict:
    """
    Generate compliance metrics for ONDC network observability

    Args:
        from_date: Start date (defaults to last 30 days)
        to_date: End date (defaults to today)

    Returns:
        Compliance metrics summary
    """
    if not from_date:
        from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not to_date:
        to_date = datetime.now().strftime("%Y-%m-%d")

    metrics = {
        "period": {"from": from_date, "to": to_date},
        "generated_at": datetime.now().isoformat(),
        "api_metrics": {},
        "igm_metrics": {},
        "rsp_metrics": {},
        "error_metrics": {}
    }

    # Check if DocType exists
    if not frappe.db.exists("DocType", "ONDC Compliance Log"):
        return {"error": "ONDC Compliance Log DocType not found"}

    # API Transaction Metrics
    api_logs = frappe.get_all(
        "ONDC Compliance Log",
        filters={
            "log_type": "API",
            "timestamp": ["between", [from_date, to_date]]
        },
        fields=["action", "status", "latency_ms"]
    )

    if api_logs:
        metrics["api_metrics"] = {
            "total_transactions": len(api_logs),
            "successful": len([l for l in api_logs if l.status == "Success"]),
            "failed": len([l for l in api_logs if l.status == "Failed"]),
            "avg_latency_ms": sum(l.latency_ms or 0 for l in api_logs) / len(api_logs),
            "by_action": {}
        }

        # Group by action
        for log in api_logs:
            action = log.action or "unknown"
            if action not in metrics["api_metrics"]["by_action"]:
                metrics["api_metrics"]["by_action"][action] = {"total": 0, "success": 0, "failed": 0}
            metrics["api_metrics"]["by_action"][action]["total"] += 1
            if log.status == "Success":
                metrics["api_metrics"]["by_action"][action]["success"] += 1
            else:
                metrics["api_metrics"]["by_action"][action]["failed"] += 1

    # IGM Metrics
    igm_logs = frappe.get_all(
        "ONDC Compliance Log",
        filters={
            "log_type": "IGM",
            "timestamp": ["between", [from_date, to_date]]
        },
        fields=["action", "status", "issue_id"]
    )

    if igm_logs:
        unique_issues = set(l.issue_id for l in igm_logs if l.issue_id)
        metrics["igm_metrics"] = {
            "total_transactions": len(igm_logs),
            "unique_issues": len(unique_issues),
            "resolved": len([l for l in igm_logs if l.status == "Resolved"]),
            "pending": len([l for l in igm_logs if l.status == "Pending"])
        }

    # RSP Metrics
    rsp_logs = frappe.get_all(
        "ONDC Compliance Log",
        filters={
            "log_type": "RSP",
            "timestamp": ["between", [from_date, to_date]]
        },
        fields=["action", "status", "amount", "recon_status"]
    )

    if rsp_logs:
        metrics["rsp_metrics"] = {
            "total_reconciliations": len(rsp_logs),
            "matched": len([l for l in rsp_logs if l.recon_status == "01"]),
            "mismatched": len([l for l in rsp_logs if l.recon_status == "02"]),
            "total_amount": sum(float(l.amount or 0) for l in rsp_logs)
        }

    # Error Metrics
    error_logs = frappe.get_all(
        "ONDC Compliance Log",
        filters={
            "log_type": "ERROR",
            "timestamp": ["between", [from_date, to_date]]
        },
        fields=["error_code", "action"]
    )

    if error_logs:
        error_by_code = {}
        for log in error_logs:
            code = log.error_code or "unknown"
            error_by_code[code] = error_by_code.get(code, 0) + 1

        metrics["error_metrics"] = {
            "total_errors": len(error_logs),
            "by_code": error_by_code
        }

    return metrics


def get_sla_compliance(from_date: str = None, to_date: str = None) -> dict:
    """
    Calculate SLA compliance for ONDC requirements

    ONDC SLA requirements:
    - API response time: < 30 seconds
    - IGM first response: < 24 hours
    - IGM resolution: < 48 hours for most categories
    """
    if not from_date:
        from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not to_date:
        to_date = datetime.now().strftime("%Y-%m-%d")

    sla_report = {
        "period": {"from": from_date, "to": to_date},
        "api_sla": {
            "target_ms": 30000,  # 30 seconds
            "compliant": 0,
            "breached": 0,
            "compliance_rate": 0
        },
        "igm_sla": {
            "first_response_target_hours": 24,
            "resolution_target_hours": 48,
            "first_response_compliant": 0,
            "first_response_breached": 0,
            "resolution_compliant": 0,
            "resolution_breached": 0
        }
    }

    if not frappe.db.exists("DocType", "ONDC Compliance Log"):
        return sla_report

    # API SLA
    api_logs = frappe.get_all(
        "ONDC Compliance Log",
        filters={
            "log_type": "API",
            "timestamp": ["between", [from_date, to_date]]
        },
        fields=["latency_ms"]
    )

    if api_logs:
        compliant = len([l for l in api_logs if (l.latency_ms or 0) <= 30000])
        breached = len(api_logs) - compliant
        sla_report["api_sla"]["compliant"] = compliant
        sla_report["api_sla"]["breached"] = breached
        sla_report["api_sla"]["compliance_rate"] = round(compliant / len(api_logs) * 100, 2)

    # IGM SLA - would need ticket timestamps from Helpdesk
    # This is a simplified version
    if frappe.db.exists("DocType", "HD Ticket"):
        tickets = frappe.get_all(
            "HD Ticket",
            filters={
                "custom_ondc_issue_id": ["is", "set"],
                "creation": ["between", [from_date, to_date]]
            },
            fields=["creation", "first_responded_on", "resolution_date"]
        )

        for ticket in tickets:
            if ticket.first_responded_on:
                response_time = (ticket.first_responded_on - ticket.creation).total_seconds() / 3600
                if response_time <= 24:
                    sla_report["igm_sla"]["first_response_compliant"] += 1
                else:
                    sla_report["igm_sla"]["first_response_breached"] += 1

            if ticket.resolution_date:
                resolution_time = (ticket.resolution_date - ticket.creation).total_seconds() / 3600
                if resolution_time <= 48:
                    sla_report["igm_sla"]["resolution_compliant"] += 1
                else:
                    sla_report["igm_sla"]["resolution_breached"] += 1

    return sla_report


def cleanup_old_logs(days_to_keep: int = 90):
    """
    Clean up old compliance logs (ONDC requires 90-day retention minimum)

    Args:
        days_to_keep: Number of days to retain logs (minimum 90)
    """
    if days_to_keep < 90:
        frappe.throw(_("ONDC requires minimum 90-day log retention"))

    cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")

    deleted = frappe.db.delete(
        "ONDC Compliance Log",
        filters={"timestamp": ["<", cutoff_date]}
    )

    frappe.db.commit()

    return {"deleted_count": deleted, "cutoff_date": cutoff_date}


# API Endpoints
@frappe.whitelist()
def get_metrics(from_date: str = None, to_date: str = None):
    """API endpoint for compliance metrics"""
    return get_compliance_metrics(from_date, to_date)


@frappe.whitelist()
def get_sla_report(from_date: str = None, to_date: str = None):
    """API endpoint for SLA compliance report"""
    return get_sla_compliance(from_date, to_date)
