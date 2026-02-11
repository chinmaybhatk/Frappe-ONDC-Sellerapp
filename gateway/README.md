# Gateway - Beckn Gateway (BG)

The routing infrastructure that enables product/service discovery across the ONDC network.

## Responsibilities

- Receive search requests from BAPs
- Multicast search requests to all relevant BPPs based on domain and city
- Route on_search responses back to the requesting BAP
- Add X-Gateway-Authorization header when forwarding
- Does NOT store transaction data (pass-through only)

**Important**: The Gateway is ONLY involved in the search/discovery phase. All subsequent interactions (select, init, confirm, etc.) happen directly between BAP and BPP (peer-to-peer).

## API Endpoints

| API | Direction | Description |
|-----|-----------|-------------|
| `POST /search` | BAP -> Gateway | Receive search request from BAP |
| `POST /on_search` | BPP -> Gateway -> BAP | Route search results back to BAP |

## Module Structure

```
src/
├── router/          # Core routing logic
│   ├── multicast.js        # Multicast search to eligible BPPs
│   ├── subscriber-filter.js # Filter BPPs by domain/city
│   └── response-router.js  # Route on_search back to BAP
├── services/        # Gateway services
│   ├── registry-service.js  # Registry lookup for subscriber discovery
│   └── health-service.js    # Health check and monitoring
├── controllers/     # HTTP controllers
│   ├── search-controller.js
│   └── on-search-controller.js
└── config/          # Gateway configuration
```

## Flow

```
1. BAP sends /search to Gateway
2. Gateway returns ACK to BAP
3. Gateway looks up Registry for eligible BPPs (matching domain + city)
4. Gateway multicasts /search to all eligible BPPs with X-Gateway-Authorization header
5. Each BPP processes search and sends /on_search back to Gateway
6. Gateway forwards each /on_search to the requesting BAP
```

## Getting Started

```bash
cd gateway
npm install
npm run dev
```

## Environment Variables

```
GATEWAY_ID=gateway.example.com
GATEWAY_URI=https://gateway.example.com
GATEWAY_PORT=3003
REGISTRY_URL=https://staging.registry.ondc.org
SIGNING_PRIVATE_KEY=<ed25519-private-key>
UNIQUE_KEY_ID=<unique-key-id>
REDIS_URL=redis://localhost:6379
```
