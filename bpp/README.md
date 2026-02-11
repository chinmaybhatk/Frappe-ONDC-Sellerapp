# BPP - Buyer Platform Provider (Seller App)

The seller-facing application that connects merchants and service providers to the ONDC network.

## Responsibilities

- Digitize seller catalogs and make them discoverable on the network
- Handle incoming action requests from BAPs (search, select, init, confirm, etc.)
- Respond via callback APIs (on_search, on_select, on_init, on_confirm, etc.)
- Manage inventory, pricing, and order fulfillment
- Integrate with logistics service providers for delivery

## API Endpoints

### Inbound Actions (BAP/Gateway -> BPP)

| API | Source | Description |
|-----|--------|-------------|
| `POST /search` | Gateway (multicast) | Handle product/service search |
| `POST /select` | BAP (direct) | Handle item selection, return quote |
| `POST /init` | BAP (direct) | Handle order initialization |
| `POST /confirm` | BAP (direct) | Handle order confirmation |
| `POST /status` | BAP (direct) | Handle status request |
| `POST /track` | BAP (direct) | Handle tracking request |
| `POST /cancel` | BAP (direct) | Handle cancellation |
| `POST /update` | BAP (direct) | Handle order update |
| `POST /rating` | BAP (direct) | Handle rating submission |
| `POST /support` | BAP (direct) | Handle support request |

### Outbound Callbacks (BPP -> BAP)

| API | Target | Description |
|-----|--------|-------------|
| `POST /on_search` | BAP (via Gateway for search) | Send catalog/search results |
| `POST /on_select` | BAP | Send quote with breakup |
| `POST /on_init` | BAP | Send payment terms |
| `POST /on_confirm` | BAP | Send order confirmation |
| `POST /on_status` | BAP | Send order status |
| `POST /on_track` | BAP | Send tracking details |
| `POST /on_cancel` | BAP | Send cancellation confirmation |
| `POST /on_update` | BAP | Send update confirmation |
| `POST /on_rating` | BAP | Send rating acknowledgment |
| `POST /on_support` | BAP | Send support details |

## Module Structure

```
src/
├── actions/         # Inbound action endpoint handlers
│   ├── search.js
│   ├── select.js
│   ├── init.js
│   ├── confirm.js
│   ├── status.js
│   ├── track.js
│   ├── cancel.js
│   ├── update.js
│   ├── rating.js
│   └── support.js
├── api/             # Outbound callback dispatchers
│   ├── on_search.js
│   ├── on_select.js
│   ├── on_init.js
│   ├── on_confirm.js
│   ├── on_status.js
│   ├── on_track.js
│   ├── on_cancel.js
│   ├── on_update.js
│   ├── on_rating.js
│   └── on_support.js
├── catalog/         # Catalog management
│   ├── catalog-service.js
│   ├── search-indexer.js     # Elasticsearch indexing
│   └── incremental-update.js
├── inventory/       # Inventory management
│   ├── inventory-service.js
│   └── stock-tracker.js
├── services/        # Business logic
│   ├── order-service.js
│   ├── fulfillment-service.js
│   ├── pricing-service.js
│   └── seller-service.js
├── controllers/     # HTTP controllers
├── routes/          # Express routes
├── models/          # BPP-specific models
└── config/          # BPP configuration
```

## Supported Domains

- `ONDC:RET10` - Grocery
- `ONDC:RET11` - Food & Beverage
- `ONDC:RET12` - Fashion
- `ONDC:RET13` - Beauty & Personal Care
- `ONDC:RET14` - Electronics
- `ONDC:RET15` - Home & Decor
- `ONDC:RET16` - Health & Wellness

## Getting Started

```bash
cd bpp
npm install
npm run dev
```

## Environment Variables

```
BPP_ID=seller-app.example.com
BPP_URI=https://seller-app.example.com/ondc
BPP_PORT=3002
REGISTRY_URL=https://staging.registry.ondc.org
SIGNING_PRIVATE_KEY=<ed25519-private-key>
UNIQUE_KEY_ID=<unique-key-id>
MONGODB_URI=mongodb://localhost:27017/ondc-bpp
REDIS_URL=redis://localhost:6379
ELASTICSEARCH_URL=http://localhost:9200
```
