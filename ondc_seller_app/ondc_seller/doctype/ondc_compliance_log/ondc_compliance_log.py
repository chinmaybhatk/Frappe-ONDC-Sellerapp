# Copyright (c) 2024, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ONDCComplianceLog(Document):
    """ONDC Compliance Log for network observability"""

    def before_insert(self):
        """Set timestamp if not provided"""
        if not self.timestamp:
            self.timestamp = frappe.utils.now_datetime()
