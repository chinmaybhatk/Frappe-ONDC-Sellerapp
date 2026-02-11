# Logistics Service Provider (LSP)

A specialized BPP that provides logistics and delivery services on the ONDC network.

## Responsibilities

- Receive logistics search requests from seller apps
- Provide delivery quotes (pricing, estimated delivery time)
- Manage rider/driver assignment
- Provide real-time tracking and status updates
- Handle proof of delivery (POD)
- Manage return logistics

## Supported Fulfillment Types

| Type | Description |
|------|-------------|
| Delivery | Standard delivery (pickup -> drop) |
| Self-Pickup | Customer picks up from store |
| Reverse QC | Return with quality check |
| Return | Standard return pickup |

## API Endpoints (as BPP in Logistics Domain)

| API | Description |
|-----|-------------|
| `POST /search` | Serviceability check and quote generation |
| `POST /init` | Initialize logistics order |
| `POST /confirm` | Confirm logistics order, assign rider |
| `POST /status` | Provide delivery status |
| `POST /track` | Provide real-time tracking URL |
| `POST /cancel` | Handle delivery cancellation |
| `POST /update` | Handle delivery updates |

## Module Structure

```
src/
├── api/             # Logistics API handlers
├── services/        # Business logic
│   ├── serviceability-service.js  # Check if delivery is possible
│   ├── quote-service.js           # Calculate delivery pricing
│   ├── assignment-service.js      # Rider/driver assignment
│   ├── tracking-service.js        # Real-time tracking
│   └── pod-service.js             # Proof of delivery
├── controllers/     # HTTP controllers
├── models/          # Data models
│   ├── delivery-order.js
│   ├── rider.js
│   └── tracking-event.js
└── config/          # Configuration
```

## Getting Started

```bash
cd logistics
npm install
npm run dev
```
