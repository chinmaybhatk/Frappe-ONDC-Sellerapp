# ONDC Seller App

Frappe app for ONDC sellers to manage products, orders, and integrations with the Open Network for Digital Commerce.

## Features

- ONDC Network Registration and Configuration
- Product Catalog Management  
- Order Management
- Webhook Integration
- Real-time Order Tracking
- Payment Integration
- Inventory Sync
- Multi-domain Support (Retail, F&B, Fashion, etc.)

## Installation

```bash
bench get-app https://github.com/chinmaybhatk/Frappe-ONDC-Sellerapp.git
bench --site site-name install-app ondc_seller_app
```

## Configuration

1. Go to ONDC Settings in your Frappe site
2. Configure your ONDC credentials and keys
3. Select environment (staging/preprod/prod)
4. Set up webhook URL for receiving callbacks

## DocTypes

### ONDC Settings
Single doctype for configuring ONDC integration parameters

### ONDC Product
Manages product catalog with ONDC-specific attributes

### ONDC Order
Handles order lifecycle from placement to fulfillment

### ONDC Webhook Log
Logs all incoming webhooks for debugging and audit

## Testing on Pramaan

## What is Pramaan?

Pramaan is ONDC's compliance testing platform that:
- Sends test API requests to your endpoints
- Validates your responses against ONDC specs
- Generates a compliance report

## Steps to Complete This Task:

### 1. **Deploy Your App First**
Your Frappe app needs to be deployed and accessible via a public URL. Make sure your Frappe Cloud deployment is complete.

### 2. **Go to Pramaan**
Click "Go to Pramaan" button or visit the Pramaan Page directly.

### 3. **Configure Your Endpoints**
In Pramaan, you'll need to provide:
- **Subscriber ID**: Your BPP subscriber ID from ONDC Settings
- **Subscriber URL**: Your public URL (e.g., `https://your-site.frappe.cloud`)
- **Domain**: `ONDC:RET10` (or your registered domain)

### 4. **Run Mandatory Test Flows**
Pramaan will test these flows against your endpoints:
- `/search` → `/on_search`
- `/select` → `/on_select`  
- `/init` → `/on_init`
- `/confirm` → `/on_confirm`
- `/status` → `/on_status`
- `/cancel` → `/on_cancel`
- `/update` → `/on_update`
- `/track` → `/on_track`
- IGM flows (`/issue`, `/issue_status`)
- RSP flows (`/receiver_recon`)

### 5. **Submit Report**
After tests pass, submit the report to ONDC.

---

**Do you want me to:**
1. Help you check if your Frappe Cloud deployment is ready?
2. Guide you through the Pramaan configuration?
3. Check if there are any missing endpoints in your app?
## License

MIT
