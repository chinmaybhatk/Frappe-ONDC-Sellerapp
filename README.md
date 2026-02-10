# ONDC Seller App for Frappe/ERPNext

A comprehensive Frappe application for sellers to integrate with the Open Network for Digital Commerce (ONDC). This app provides complete ONDC protocol compliance including transaction flows, issue management (IGM), and settlement reconciliation (RSP).

## Features

- **ONDC Protocol Compliance** - Full Beckn Protocol v1.2.0 implementation
- **Transaction Flows** - search, select, init, confirm, status, track, cancel, update, rating, support
- **IGM (Issue & Grievance Management)** - Integrated with Frappe Helpdesk
- **RSP (Reconciliation & Settlement)** - Integrated with ERPNext Payment Reconciliation
- **Network Observability** - Compliance logging and SLA tracking
- **Ed25519 Signing** - Cryptographic request signing and verification
- **Multi-domain Support** - Retail, F&B, Fashion, Electronics, etc.

---

## Installation

### Prerequisites

- Frappe Framework v15+
- ERPNext v15+ (required for RSP/Payment features)
- Frappe Helpdesk (optional, for IGM integration)

### Install on Frappe Cloud

1. Go to your Frappe Cloud dashboard
2. Navigate to **Apps** → **Add App**
3. Enter the repository URL:
   ```
   https://github.com/chinmaybhatk/Frappe-ONDC-Sellerapp.git
   ```
4. Install the app on your site

### Install on Self-hosted Bench

```bash
# Get the app
bench get-app https://github.com/chinmaybhatk/Frappe-ONDC-Sellerapp.git

# Install on your site
bench --site your-site.local install-app ondc_seller_app

# (Optional) Install Helpdesk for IGM integration
bench get-app helpdesk
bench --site your-site.local install-app helpdesk
```

---

## Configuration

### Step 1: Access ONDC Settings

1. Login to your Frappe site as Administrator
2. Go to **Search Bar** → Type "ONDC Settings"
3. Or navigate via URL: `https://your-site.frappe.cloud/app/ondc-settings`

### Step 2: Configure Basic Settings

| Field | Description | Example |
|-------|-------------|---------|
| **Subscriber ID** | Your unique BPP ID registered with ONDC | `your-company.com` |
| **Subscriber URL** | Your public endpoint URL | `https://your-site.frappe.cloud` |
| **Environment** | ONDC environment | `staging` / `preprod` / `prod` |
| **Domain** | ONDC domain code | `ONDC:RET10` (Grocery) |
| **City Code** | Operating city | `std:080` (Bangalore) |

### Step 3: Configure Cryptographic Keys

ONDC requires Ed25519 keys for request signing:

| Field | Description |
|-------|-------------|
| **Signing Private Key** | Your Ed25519 private key (base64 encoded) |
| **Signing Public Key** | Your Ed25519 public key (base64 encoded) |
| **Unique Key ID** | Key identifier registered with ONDC registry |
| **Encryption Private Key** | X25519 private key for payload encryption |
| **Encryption Public Key** | X25519 public key for payload encryption |

#### Generate Keys (if you don't have them):

```python
from nacl.signing import SigningKey
import base64

# Generate signing keypair
signing_key = SigningKey.generate()
private_key = base64.b64encode(signing_key.encode()).decode()
public_key = base64.b64encode(signing_key.verify_key.encode()).decode()

print(f"Private Key: {private_key}")
print(f"Public Key: {public_key}")
```

### Step 4: Configure Business Details

| Field | Description | Example |
|-------|-------------|---------|
| **Legal Entity Name** | Registered business name | `Your Company Pvt Ltd` |
| **GSTIN** | GST Identification Number | `29AAAAA0000A1Z5` |
| **PAN** | PAN number | `AAAAA0000A` |
| **FSSAI License** | Food license (for F&B) | `12345678901234` |
| **Consumer Care Email** | Support email | `support@your-company.com` |
| **Consumer Care Phone** | Support phone | `+91-9876543210` |

### Step 5: Configure Store/Location Details

| Field | Description |
|-------|-------------|
| **Store Name** | Display name for your store |
| **Store GPS** | Latitude,Longitude of store |
| **Store Address** | Complete store address |
| **Serviceability Radius** | Delivery radius in km |
| **Operating Hours** | Store timings (HH:MM-HH:MM format) |

### Step 6: Configure Payment Settings (for RSP)

| Field | Description |
|-------|-------------|
| **Bank Account** | ERPNext Bank Account for settlements |
| **Receivable Account** | Accounts Receivable ledger |
| **Settlement Bank Name** | Your bank name |
| **Settlement Account No** | Bank account number |
| **Settlement IFSC** | Bank IFSC code |
| **Beneficiary Name** | Account holder name |

---

## API Endpoints

Once deployed, your app exposes these endpoints:

### Transaction APIs (via `/ondc/webhook/<action>`)

| Endpoint | Description |
|----------|-------------|
| `POST /ondc/webhook/search` | Handle catalog search requests |
| `POST /ondc/webhook/select` | Handle item selection |
| `POST /ondc/webhook/init` | Handle order initialization |
| `POST /ondc/webhook/confirm` | Handle order confirmation |
| `POST /ondc/webhook/status` | Handle order status requests |
| `POST /ondc/webhook/track` | Handle tracking requests |
| `POST /ondc/webhook/cancel` | Handle cancellation requests |
| `POST /ondc/webhook/update` | Handle order updates |
| `POST /ondc/webhook/rating` | Handle ratings |
| `POST /ondc/webhook/support` | Handle support requests |

### Registry Endpoint

| Endpoint | Description |
|----------|-------------|
| `POST /on_subscribe` | Registry onboarding callback |

### IGM (Issue & Grievance Management)

| Endpoint | Description |
|----------|-------------|
| `POST /issue` | Receive issue/complaint from buyer |
| `POST /issue_status` | Check issue status |

### RSP (Reconciliation & Settlement)

| Endpoint | Description |
|----------|-------------|
| `POST /receiver_recon` | Settlement reconciliation |

---

## Product Sync

The app supports two sync sources for products. Choose based on your business model:

| Sync Source | Best For | Features |
|-------------|----------|----------|
| **ERPNext Item** | B2B sellers, Inventory-focused | Direct inventory integration, Stock sync |
| **Frappe Webshop** | B2C sellers, E-commerce | Multiple images, Web pricing, Slideshow |
| **Both** | Hybrid businesses | Sync from both sources |

### Configure Sync Source

1. Go to **ONDC Settings**
2. Under **Product Sync Settings**, select **Product Sync Source**:
   - `ERPNext Item` - Sync from ERPNext Item master
   - `Frappe Webshop` - Sync from Website Items
   - `Both` - Enable both sync sources

---

## Option 1: ERPNext Item Sync

Best for B2B sellers using ERPNext for inventory management.

### How It Works

1. **Enable Sync on Item**: Check the "Sync to ONDC" checkbox on any Item
2. **Auto-Create**: When Item is saved, an ONDC Product is automatically created
3. **Auto-Update**: Changes to Item (price, description, image) sync to ONDC Product
4. **Category Mapping**: Item Groups are auto-mapped to ONDC categories

### Setting Up Item Sync

#### Sync Individual Items
1. Open any Item in ERPNext
2. Check **"Sync to ONDC"** checkbox (in the first section)
3. Optionally set **Country of Origin** and **ONDC Category**
4. Save the Item

#### Bulk Sync Existing Items

Run from Frappe Console:
```python
# Enable sync for all items in a group
from ondc_seller_app.utils.bulk_sync import enable_ondc_sync_for_item_group
enable_ondc_sync_for_item_group('Grocery')

# Sync all enabled items
from ondc_seller_app.utils.bulk_sync import sync_all_items_to_ondc
sync_all_items_to_ondc()
```

Or call the API:
```bash
curl -X POST https://your-site.frappe.cloud/api/method/ondc_seller_app.utils.bulk_sync.bulk_sync_items \
  -H "Authorization: token api_key:api_secret" \
  -d '{"item_group": "Grocery"}'
```

### Custom Fields Added to Item

| Field | Description |
|-------|-------------|
| **Sync to ONDC** | Enable/disable ONDC sync for this item |
| **ONDC Product ID** | Auto-generated link to ONDC Product |
| **Country of Origin** | Required for ONDC (defaults to India) |
| **ONDC Category** | Override auto-detected category |
| **Sync Status** | Current sync status (Synced/Failed/Pending) |
| **Last Synced** | Timestamp of last successful sync |

---

## Option 2: Frappe Webshop Sync

Best for B2C sellers with consumer-facing websites using Frappe Webshop.

### How It Works

1. **Enable Sync on Website Item**: Check "Sync to ONDC" checkbox
2. **Rich Media**: Multiple images from Website Item slideshow are synced
3. **Web Pricing**: Uses Webshop pricing rules
4. **Auto-Update**: Changes sync automatically

### Setting Up Webshop Sync

1. Ensure **Frappe Webshop** is installed
2. Go to ONDC Settings → Set **Product Sync Source** to `Frappe Webshop` or `Both`
3. Open any **Website Item**
4. Check **"Sync to ONDC"** checkbox
5. Set **Country of Origin** and optionally **ONDC Category**
6. Save

### Custom Fields Added to Website Item

| Field | Description |
|-------|-------------|
| **Sync to ONDC** | Enable/disable ONDC sync |
| **ONDC Product ID** | Auto-generated link to ONDC Product |
| **Country of Origin** | Required for ONDC (defaults to India) |
| **ONDC Category** | Override auto-detected category |
| **Sync Status** | Current sync status |
| **Last Synced** | Timestamp of last successful sync |

### Webshop Advantages

- **Multiple Images**: Syncs all images from Website Item slideshow
- **Web-specific Pricing**: Uses Webshop pricing rules and discounts
- **SEO Data**: Can leverage web-optimized descriptions
- **Variant Support**: Works with Website Item variants

---

## Category Mapping

Item Groups are automatically mapped to ONDC categories:

| Item Group | ONDC Category |
|------------|---------------|
| Grocery | ONDC:RET10 |
| Food & Beverages | ONDC:RET11 |
| Fashion | ONDC:RET12 |
| Beauty & Personal Care | ONDC:RET13 |
| Electronics | ONDC:RET14 |
| Home & Decor | ONDC:RET15 |
| Health & Wellness | ONDC:RET16 |
| Pharma | ONDC:RET17 |
| Agriculture | ONDC:RET18 |

You can override this by setting the ONDC Category field on the Item or Website Item.

---

## DocTypes

### ONDC Settings
Single DocType for all ONDC configuration. Access via `ONDC Settings` in search.

### ONDC Product
Manages product catalog with ONDC-specific attributes:
- Linked to ERPNext Item (auto-synced)
- ONDC Product ID
- Category mappings
- Statutory requirements (FSSAI, brand owner, etc.)
- Pricing and availability

### ONDC Order
Handles order lifecycle:
- Order status tracking
- Fulfillment state machine (Pending → Packed → Agent-assigned → Out-for-delivery → Delivered)
- Payment status
- Cancellation handling

### ONDC Order Item
Child table for order line items.

### ONDC Webhook Log
Logs all incoming/outgoing API calls for debugging and compliance.

### ONDC Compliance Log
Network observability logging:
- API transaction logs
- IGM transaction logs
- RSP reconciliation logs
- SLA compliance metrics

---

## IGM Integration (Frappe Helpdesk)

When a buyer raises an issue via ONDC:

1. **Issue Received** → Creates HD Ticket in Frappe Helpdesk
2. **Ticket Updated** → Sends `/on_issue_status` callback automatically
3. **Ticket Resolved** → Notifies buyer via ONDC network

### Helpdesk Custom Fields

The app adds these custom fields to HD Ticket:
- `custom_ondc_issue_id` - ONDC Issue ID
- `custom_ondc_order_id` - Related Order ID
- `custom_ondc_category` - Issue category
- `custom_bap_id` - Buyer App ID

If Helpdesk is not installed, issues are logged in ONDC Webhook Log.

---

## RSP Integration (ERPNext)

When settlement reconciliation request arrives:

1. **Reconciliation Request** → Matches with ONDC Orders
2. **Creates Entries** → Payment Entry or Journal Entry in ERPNext
3. **Sends Response** → `/on_receiver_recon` with match status

---

## Testing with Pramaan

### What is Pramaan?

Pramaan is ONDC's automated compliance testing platform that validates your integration.

### Steps to Test

1. **Deploy your app** to a publicly accessible URL

2. **Go to Pramaan**: https://pramaan.ondc.org

3. **Configure your endpoints**:
   - Subscriber ID: Your BPP subscriber ID
   - Subscriber URL: `https://your-site.frappe.cloud`
   - Domain: Your registered domain (e.g., `ONDC:RET10`)

4. **Run mandatory test flows**:
   - All transaction APIs (search through support)
   - IGM flows (issue, issue_status)
   - RSP flows (receiver_recon)

5. **Submit report** to ONDC portal

---

## Troubleshooting

### Common Issues

#### 1. "No module named 'ondc_seller_app.ondc_seller'"
**Cause**: Module structure mismatch
**Fix**: Ensure `modules.txt` contains `ondc_seller` and the folder exists at `ondc_seller_app/ondc_seller/`

#### 2. "Invalid default value for 'created_at'"
**Cause**: Invalid datetime default in DocType JSON
**Fix**: Remove `"default": "__timestamp"` from Datetime fields

#### 3. Signature verification failed
**Cause**: Key mismatch or incorrect key format
**Fix**: Verify keys are base64 encoded Ed25519 keys and registered with ONDC registry

#### 4. Webhook not responding
**Cause**: Route not configured
**Fix**: Check `hooks.py` has correct `website_route_rules`

### Debug Logs

Check logs at:
- **ONDC Webhook Log**: All incoming/outgoing API calls
- **ONDC Compliance Log**: Network observability data
- **Error Log**: `bench --site your-site.local show-error-log`

---

## Development

### Project Structure

```
ondc_seller_app/
├── ondc_seller_app/
│   ├── __init__.py
│   ├── hooks.py              # Frappe hooks and routes
│   ├── modules.txt           # Module definition
│   ├── api/
│   │   ├── auth.py           # Signature verification
│   │   ├── ondc_client.py    # ONDC API client
│   │   ├── ondc_errors.py    # Error codes and responses
│   │   ├── webhook.py        # Webhook handlers
│   │   ├── igm_adapter.py    # IGM/Helpdesk integration
│   │   ├── rsp_adapter.py    # RSP/Payment integration
│   │   └── compliance_log.py # Observability logging
│   ├── ondc_seller/
│   │   ├── doctype/
│   │   │   ├── ondc_settings/
│   │   │   ├── ondc_product/
│   │   │   ├── ondc_order/
│   │   │   ├── ondc_order_item/
│   │   │   ├── ondc_webhook_log/
│   │   │   └── ondc_compliance_log/
│   │   └── report/
│   └── utils/
├── pyproject.toml
└── README.md
```

### Running Tests

```bash
bench --site your-site.local run-tests --app ondc_seller_app
```

---

## ONDC Resources

- [ONDC Protocol Specifications](https://docs.ondc.org)
- [Beckn Protocol](https://beckn.network)
- [ONDC Registry](https://registry.ondc.org)
- [Pramaan Testing Platform](https://pramaan.ondc.org)
- [ONDC Portal](https://portal.ondc.org)

---

## License

MIT License

## Support

For issues and feature requests, please create an issue on GitHub.
