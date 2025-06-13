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

## License

MIT