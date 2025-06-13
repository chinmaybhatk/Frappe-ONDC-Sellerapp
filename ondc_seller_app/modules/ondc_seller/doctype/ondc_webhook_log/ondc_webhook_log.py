import frappe
from frappe.model.document import Document
import json

class ONDCWebhookLog(Document):
    def validate(self):
        # Pretty format JSON fields
        if self.request_body and isinstance(self.request_body, dict):
            self.request_body = json.dumps(self.request_body, indent=2)
        if self.response_body and isinstance(self.response_body, dict):
            self.response_body = json.dumps(self.response_body, indent=2)