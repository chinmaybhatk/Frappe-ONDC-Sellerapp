# Shared Libraries

Common modules used across all ONDC network roles (BAP, BPP, Gateway, Registry).

## Modules

### `crypto/`
- **signing.js** - Ed25519 digital signature creation (Authorization header)
- **verification.js** - Signature verification for incoming requests
- **keys.js** - Key pair generation, storage, and rotation (Ed25519 + X25519)
- **hashing.js** - BLAKE-512 digest computation for request bodies

### `protocol/`
- **context.js** - Beckn context builder (domain, city, transaction_id, timestamps)
- **request.js** - Outbound API request builder with signing
- **response.js** - ACK/NACK response builder
- **callbacks.js** - Callback URL resolver and dispatcher

### `models/`
- **context.js** - Context schema (domain, action, bap_id, bpp_id, etc.)
- **order.js** - Order model (items, billing, fulfillment, payment)
- **item.js** - Item/product model (descriptor, price, quantity, category)
- **fulfillment.js** - Fulfillment model (type, tracking, agent, vehicle)
- **payment.js** - Payment model (type, status, params)
- **location.js** - Location model (GPS, address, city, area_code)

### `middleware/`
- **auth.js** - Request authentication (signature verification)
- **gateway-auth.js** - X-Gateway-Authorization verification
- **logger.js** - Structured logging middleware
- **error-handler.js** - Centralized error handling
- **rate-limiter.js** - Rate limiting per subscriber

### `validators/`
- **schema-validator.js** - JSON Schema validation engine (Ajv)
- **schemas/** - JSON Schemas for all 20 Beckn APIs

### `utils/`
- **registry-client.js** - Registry /lookup API client with Redis caching
- **http-client.js** - HTTP client with retry, timeout, and circuit breaker
- **id-generator.js** - UUID generators for transaction_id, message_id

### `config/`
- **index.js** - Configuration loader (environment-aware)
- **domains.js** - Domain code mappings (ONDC:RET10, ONDC:RET11, etc.)
- **constants.js** - Network constants, error codes, status codes

## Usage

```javascript
const { sign, verify } = require('@ondc/shared/crypto');
const { buildContext } = require('@ondc/shared/protocol');
const { authMiddleware } = require('@ondc/shared/middleware');
```
