# Registry - ONDC Network Registry

The central trust infrastructure and network participant lookup service.

## Responsibilities

- Maintain a registry of all Network Participants (NPs) with their subscriber_id, public keys, callback URLs, and supported domains
- Provide the `/lookup` API for participant discovery and key verification
- Provide the `/subscribe` API for new participant registration
- Enable digital signature verification (Ed25519)
- Manage onboarding and whitelisting of new participants
- Site verification via `ondc-site-verification.html`

## API Endpoints

| API | Description |
|-----|-------------|
| `POST /subscribe` | Register a new Network Participant |
| `POST /lookup` | Look up subscriber details (public key, callback URL) |
| `GET /vlookup` | Verified lookup with additional validation |

## Data Model

### Subscriber

```json
{
  "subscriber_id": "seller-app.example.com",
  "subscriber_url": "https://seller-app.example.com/ondc",
  "type": "BPP",
  "domain": "ONDC:RET10",
  "city": ["std:080", "std:011"],
  "signing_public_key": "<ed25519-public-key>",
  "encr_public_key": "<x25519-public-key>",
  "unique_key_id": "uk-001",
  "valid_from": "2026-01-01T00:00:00Z",
  "valid_until": "2027-01-01T00:00:00Z",
  "status": "SUBSCRIBED"
}
```

## Module Structure

```
src/
├── api/             # Registry API handlers
│   ├── subscribe.js
│   ├── lookup.js
│   └── vlookup.js
├── services/        # Registry services
│   ├── subscriber-service.js
│   ├── verification-service.js  # Domain/site verification
│   └── key-service.js           # Public key management
├── controllers/     # HTTP controllers
├── models/          # Data models
│   ├── subscriber.js
│   └── key-pair.js
└── config/          # Registry configuration
```

## Getting Started

```bash
cd registry
npm install
npm run dev
```

## Environment Variables

```
REGISTRY_PORT=3004
MONGODB_URI=mongodb://localhost:27017/ondc-registry
REDIS_URL=redis://localhost:6379
```
