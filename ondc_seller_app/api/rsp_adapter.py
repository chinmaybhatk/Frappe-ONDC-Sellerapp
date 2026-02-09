"""
ONDC RSP (Reconciliation & Settlement Protocol) Adapter

Bridges ERPNext Payment Reconciliation with ONDC RSP Protocol.
Handles settlement reconciliation between BAP, BPP, and LSP.

RSP Endpoints:
- /receiver_recon - Receive reconciliation request from collector
- /on_receiver_recon - Send reconciliation response

Reference: ONDC RSP Specification v1.0
"""

import frappe
from frappe import _
import json
from datetime import datetime
from typing import Optional
from .ondc_client import ONDCClient
from .ondc_errors import build_ack_response, build_nack_response, OndcErrorCode


class RSPAdapter:
    """Adapter for ONDC Reconciliation & Settlement Protocol"""

    # ONDC settlement status mapping
    SETTLEMENT_STATUS = {
        "PAID": "Paid",
        "NOT-PAID": "Unpaid",
        "PENDING": "Pending",
        "ADJUSTED": "Adjusted",
        "REFUND": "Refund"
    }

    # ONDC settlement type mapping
    SETTLEMENT_TYPE = {
        "ORDER": "Order Payment",
        "REFUND": "Refund",
        "WITHHOLDING": "TDS/TCS",
        "INCENTIVE": "Incentive",
        "PENALTY": "Penalty"
    }

    def __init__(self):
        self.client = ONDCClient()
        self.settings = frappe.get_single("ONDC Settings")

    def handle_receiver_recon(self, payload: dict) -> dict:
        """
        Handle incoming /receiver_recon request from BAP/Collector
        Creates Payment Reconciliation entries in ERPNext

        Args:
            payload: ONDC receiver_recon request payload

        Returns:
            ACK response, actual reconciliation done async
        """
        try:
            context = payload.get("context", {})
            message = payload.get("message", {})

            # Validate context
            if context.get("action") != "receiver_recon":
                return build_nack_response(
                    context,
                    OndcErrorCode.INVALID_REQUEST,
                    "Invalid action for receiver_recon endpoint"
                )

            # Store context for callback
            recon_request = message.get("recon_request", {})

            # Queue async processing
            frappe.enqueue(
                "ondc_seller_app.api.rsp_adapter.process_reconciliation",
                queue="default",
                timeout=300,
                context=context,
                recon_request=recon_request
            )

            return build_ack_response(context)

        except Exception as e:
            frappe.log_error(f"RSP receiver_recon error: {str(e)}", "ONDC RSP")
            return build_nack_response(
                payload.get("context", {}),
                OndcErrorCode.INTERNAL_ERROR,
                str(e)
            )

    def process_reconciliation(self, context: dict, recon_request: dict):
        """
        Process reconciliation request and create ERPNext entries
        Called asynchronously after ACK
        """
        try:
            settlement_id = recon_request.get("settlement_id")
            orders = recon_request.get("orders", [])

            reconciliation_results = []

            for order_data in orders:
                result = self._reconcile_order(order_data, settlement_id)
                reconciliation_results.append(result)

            # Send on_receiver_recon callback
            self._send_recon_response(context, settlement_id, reconciliation_results)

        except Exception as e:
            frappe.log_error(f"RSP reconciliation processing error: {str(e)}", "ONDC RSP")
            # Send error callback
            self._send_recon_error(context, str(e))

    def _reconcile_order(self, order_data: dict, settlement_id: str) -> dict:
        """
        Reconcile a single order's settlement

        Args:
            order_data: Order settlement data from ONDC
            settlement_id: Settlement batch ID

        Returns:
            Reconciliation result for this order
        """
        order_id = order_data.get("id")
        settlements = order_data.get("settlements", [])

        result = {
            "id": order_id,
            "settlement_id": settlement_id,
            "recon_status": "01",  # 01 = Matched
            "diff_amount": {"currency": "INR", "value": "0"},
            "message": {"code": "SUCCESS", "short_desc": "Reconciliation successful"}
        }

        try:
            # Find ONDC Order
            ondc_order = frappe.get_doc("ONDC Order", {"ondc_order_id": order_id})

            # Get expected amounts from order
            expected_total = float(ondc_order.total_amount or 0)

            # Sum up settlements from ONDC
            actual_total = sum(
                float(s.get("amount", {}).get("value", 0))
                for s in settlements
                if s.get("type") == "ORDER"
            )

            # Calculate difference
            diff = expected_total - actual_total

            if abs(diff) > 0.01:  # Allow 1 paisa tolerance
                result["recon_status"] = "02"  # 02 = Difference found
                result["diff_amount"]["value"] = str(abs(diff))
                result["message"] = {
                    "code": "DIFF_FOUND",
                    "short_desc": f"Amount difference of {abs(diff):.2f} found"
                }

            # Create/Update Payment Entry or Journal Entry for reconciliation
            self._create_reconciliation_entry(ondc_order, settlements, settlement_id, diff)

            # Log compliance
            self._log_recon_compliance(order_id, settlement_id, result)

        except frappe.DoesNotExistError:
            result["recon_status"] = "03"  # 03 = Order not found
            result["message"] = {
                "code": "ORDER_NOT_FOUND",
                "short_desc": f"Order {order_id} not found in system"
            }
        except Exception as e:
            result["recon_status"] = "04"  # 04 = Error
            result["message"] = {
                "code": "PROCESSING_ERROR",
                "short_desc": str(e)
            }

        return result

    def _create_reconciliation_entry(self, ondc_order, settlements: list,
                                     settlement_id: str, diff_amount: float):
        """
        Create ERPNext reconciliation entries
        Uses Payment Reconciliation or Journal Entry based on setup
        """
        # Check if Payment Reconciliation Tool is available (ERPNext)
        if frappe.db.exists("DocType", "Payment Reconciliation"):
            self._use_payment_reconciliation(ondc_order, settlements, settlement_id)
        else:
            # Fallback to Journal Entry
            self._create_journal_entry(ondc_order, settlements, settlement_id, diff_amount)

    def _use_payment_reconciliation(self, ondc_order, settlements: list, settlement_id: str):
        """
        Use ERPNext Payment Reconciliation Tool
        """
        for settlement in settlements:
            settlement_type = settlement.get("type", "ORDER")
            amount = float(settlement.get("amount", {}).get("value", 0))
            utr = settlement.get("payment_ref_no", "")

            # Check if payment entry already exists
            existing = frappe.db.exists("Payment Entry", {
                "reference_no": utr,
                "docstatus": ["!=", 2]
            })

            if existing:
                continue

            # Create Payment Entry for received settlement
            if settlement_type == "ORDER" and amount > 0:
                pe = frappe.get_doc({
                    "doctype": "Payment Entry",
                    "payment_type": "Receive",
                    "party_type": "Customer",
                    "party": ondc_order.customer,
                    "paid_amount": amount,
                    "received_amount": amount,
                    "reference_no": utr,
                    "reference_date": frappe.utils.today(),
                    "remarks": f"ONDC Settlement: {settlement_id}",
                    "custom_ondc_order": ondc_order.name,
                    "custom_ondc_settlement_id": settlement_id
                })

                # Add reference to Sales Invoice if exists
                if ondc_order.sales_invoice:
                    pe.append("references", {
                        "reference_doctype": "Sales Invoice",
                        "reference_name": ondc_order.sales_invoice,
                        "allocated_amount": amount
                    })

                pe.insert(ignore_permissions=True)

            elif settlement_type == "REFUND" and amount > 0:
                # Handle refund entry
                pe = frappe.get_doc({
                    "doctype": "Payment Entry",
                    "payment_type": "Pay",
                    "party_type": "Customer",
                    "party": ondc_order.customer,
                    "paid_amount": amount,
                    "received_amount": amount,
                    "reference_no": utr,
                    "reference_date": frappe.utils.today(),
                    "remarks": f"ONDC Refund: {settlement_id}",
                    "custom_ondc_order": ondc_order.name,
                    "custom_ondc_settlement_id": settlement_id
                })
                pe.insert(ignore_permissions=True)

    def _create_journal_entry(self, ondc_order, settlements: list,
                              settlement_id: str, diff_amount: float):
        """
        Fallback: Create Journal Entry for reconciliation
        """
        accounts = []
        total_credit = 0
        total_debit = 0

        # Get default accounts from settings
        bank_account = self.settings.bank_account or "Bank - Company"
        receivable_account = self.settings.receivable_account or "Debtors - Company"

        for settlement in settlements:
            amount = float(settlement.get("amount", {}).get("value", 0))
            settlement_type = settlement.get("type", "ORDER")

            if settlement_type == "ORDER":
                # Debit Bank, Credit Receivable
                accounts.append({
                    "account": bank_account,
                    "debit_in_account_currency": amount,
                    "reference_type": "ONDC Order",
                    "reference_name": ondc_order.name
                })
                total_debit += amount

            elif settlement_type == "REFUND":
                # Debit Receivable, Credit Bank
                accounts.append({
                    "account": receivable_account,
                    "debit_in_account_currency": amount,
                    "reference_type": "ONDC Order",
                    "reference_name": ondc_order.name
                })
                total_debit += amount

        # Balance entry
        accounts.append({
            "account": receivable_account if total_debit > total_credit else bank_account,
            "credit_in_account_currency": abs(total_debit - total_credit)
        })

        if accounts:
            je = frappe.get_doc({
                "doctype": "Journal Entry",
                "voucher_type": "Journal Entry",
                "posting_date": frappe.utils.today(),
                "accounts": accounts,
                "user_remark": f"ONDC Settlement Reconciliation: {settlement_id}",
                "custom_ondc_settlement_id": settlement_id
            })
            je.insert(ignore_permissions=True)

    def _send_recon_response(self, context: dict, settlement_id: str, results: list):
        """
        Send /on_receiver_recon callback to BAP
        """
        callback_payload = {
            "context": self.client.build_context("on_receiver_recon", context),
            "message": {
                "recon_response": {
                    "settlement_id": settlement_id,
                    "orders": results,
                    "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
                }
            }
        }

        # Get BAP URI from context
        bap_uri = context.get("bap_uri", "")
        if bap_uri:
            endpoint = f"{bap_uri.rstrip('/')}/on_receiver_recon"
            self.client.send_request(endpoint, callback_payload)

    def _send_recon_error(self, context: dict, error_message: str):
        """
        Send error callback for reconciliation failure
        """
        callback_payload = {
            "context": self.client.build_context("on_receiver_recon", context),
            "error": {
                "type": "DOMAIN-ERROR",
                "code": "50001",
                "message": error_message
            }
        }

        bap_uri = context.get("bap_uri", "")
        if bap_uri:
            endpoint = f"{bap_uri.rstrip('/')}/on_receiver_recon"
            self.client.send_request(endpoint, callback_payload)

    def _log_recon_compliance(self, order_id: str, settlement_id: str, result: dict):
        """
        Log reconciliation for ONDC compliance
        """
        try:
            if frappe.db.exists("DocType", "ONDC Compliance Log"):
                frappe.get_doc({
                    "doctype": "ONDC Compliance Log",
                    "log_type": "RSP",
                    "action": "receiver_recon",
                    "order_id": order_id,
                    "settlement_id": settlement_id,
                    "status": "Success" if result.get("recon_status") == "01" else "Failed",
                    "response_data": json.dumps(result),
                    "timestamp": frappe.utils.now_datetime()
                }).insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(f"RSP compliance logging error: {str(e)}", "ONDC RSP")

    def generate_settlement_report(self, from_date: str, to_date: str) -> dict:
        """
        Generate settlement report for ONDC compliance

        Args:
            from_date: Report start date
            to_date: Report end date

        Returns:
            Settlement summary report
        """
        # Query all ONDC orders in date range
        orders = frappe.get_all(
            "ONDC Order",
            filters={
                "creation": ["between", [from_date, to_date]],
                "status": ["in", ["Delivered", "Completed"]]
            },
            fields=["name", "ondc_order_id", "total_amount", "payment_status",
                    "bap_id", "creation"]
        )

        # Group by BAP
        bap_summary = {}
        for order in orders:
            bap_id = order.get("bap_id", "Unknown")
            if bap_id not in bap_summary:
                bap_summary[bap_id] = {
                    "order_count": 0,
                    "total_value": 0,
                    "settled_value": 0,
                    "pending_value": 0
                }

            bap_summary[bap_id]["order_count"] += 1
            amount = float(order.get("total_amount", 0))
            bap_summary[bap_id]["total_value"] += amount

            if order.get("payment_status") == "Paid":
                bap_summary[bap_id]["settled_value"] += amount
            else:
                bap_summary[bap_id]["pending_value"] += amount

        return {
            "report_period": {"from": from_date, "to": to_date},
            "generated_at": frappe.utils.now_datetime().isoformat(),
            "summary": {
                "total_orders": len(orders),
                "total_value": sum(float(o.get("total_amount", 0)) for o in orders),
                "by_bap": bap_summary
            }
        }


# Standalone functions for frappe.enqueue
def process_reconciliation(context: dict, recon_request: dict):
    """Process reconciliation request asynchronously"""
    adapter = RSPAdapter()
    adapter.process_reconciliation(context, recon_request)


# API endpoint handlers
@frappe.whitelist(allow_guest=True)
def receiver_recon():
    """
    Handle /receiver_recon API endpoint
    """
    try:
        payload = json.loads(frappe.request.data)
        adapter = RSPAdapter()
        response = adapter.handle_receiver_recon(payload)
        return response
    except Exception as e:
        frappe.log_error(f"RSP endpoint error: {str(e)}", "ONDC RSP")
        return {"error": str(e)}
