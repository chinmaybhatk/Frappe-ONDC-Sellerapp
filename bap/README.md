# BAP - Buyer Application Platform

The consumer-facing application that connects buyers to the ONDC network.

## Responsibilities

- Provide user interface for product discovery, ordering, and post-order management
- Send action requests (search, select, init, confirm, etc.) to the network
- Handle callback responses (on_search, on_select, on_init, on_confirm, etc.)
- Customer support, order tracking, payment collection

## API Endpoints

### Outbound Actions (BAP -> Gateway/BPP)

| API | Target | Description |
|-----|--------|-------------|
| `POST /search` | Gateway | Discover products/services |
| `POST /select` | BPP (direct) | Select items, get quote |
| `POST /init` | BPP (direct) | Initialize order |
| `POST /confirm` | BPP (direct) | Confirm order |
| `POST /status` | BPP (direct) | Check order status |
| `POST /track` | BPP (direct) | Get tracking info |
| `POST /cancel` | BPP (direct) | Cancel order |
| `POST /update` | BPP (direct) | Update order |
| `POST /rating` | BPP (direct) | Rate experience |
| `POST /support` | BPP (direct) | Get support info |

### Inbound Callbacks (BPP -> BAP)

| API | Source | Description |
|-----|--------|-------------|
| `POST /on_search` | BPP (via Gateway) | Receive search results |
| `POST /on_select` | BPP | Receive quote |
| `POST /on_init` | BPP | Receive order initialization details |
| `POST /on_confirm` | BPP | Receive order confirmation |
| `POST /on_status` | BPP | Receive order status |
| `POST /on_track` | BPP | Receive tracking info |
| `POST /on_cancel` | BPP | Receive cancellation confirmation |
| `POST /on_update` | BPP | Receive update confirmation |
| `POST /on_rating` | BPP | Receive rating acknowledgment |
| `POST /on_support` | BPP | Receive support details |

## Module Structure

```
src/
├── api/             # Outbound action request builders
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
├── callbacks/       # Inbound callback handlers
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
├── services/        # Business logic
│   ├── order-service.js
│   ├── cart-service.js
│   ├── payment-service.js
│   └── user-service.js
├── controllers/     # HTTP controllers
├── routes/          # Express routes
├── models/          # BAP-specific models
└── config/          # BAP configuration
```

## Getting Started

```bash
cd bap
npm install
npm run dev
```

## Environment Variables

```
BAP_ID=buyer-app.example.com
BAP_URI=https://buyer-app.example.com/ondc
BAP_PORT=3001
REGISTRY_URL=https://staging.registry.ondc.org
SIGNING_PRIVATE_KEY=<ed25519-private-key>
UNIQUE_KEY_ID=<unique-key-id>
MONGODB_URI=mongodb://localhost:27017/ondc-bap
REDIS_URL=redis://localhost:6379
```
