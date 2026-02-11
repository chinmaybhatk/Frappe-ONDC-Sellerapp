# ONDC Project Plan - All Roles Implementation

## Overview

This project implements all key roles of the **ONDC (Open Network for Digital Commerce)** ecosystem built on the **Beckn Protocol**. The architecture supports a decentralized, interoperable digital commerce network where any Buyer App can discover and transact with any Seller App.

---

## Network Roles

| Role | Code | Description |
|------|------|-------------|
| **Buyer App** | BAP (Buyer Application Platform) | Consumer-facing app for discovery, ordering, and post-order flows |
| **Seller App** | BPP (Buyer Platform Provider) | Seller-facing app for catalog management, order fulfillment |
| **Gateway** | BG (Beckn Gateway) | Routing infrastructure for search/discovery |
| **Registry** | Registry | Central trust infrastructure and NP lookup service |
| **Logistics** | LSP (Logistics Service Provider) | Specialized BPP for delivery/logistics services |
| **IGM** | IGM (Issue & Grievance Management) | Complaint handling, returns, and refund disputes |

---

## Supported Domains

| Domain Code | Category |
|-------------|----------|
| `ONDC:RET10` | Grocery |
| `ONDC:RET11` | Food & Beverage |
| `ONDC:RET12` | Fashion |
| `ONDC:RET13` | Beauty & Personal Care |
| `ONDC:RET14` | Electronics |
| `ONDC:RET15` | Home & Decor |
| `ONDC:RET16` | Health & Wellness |
| `ONDC:RET17` | Agriculture |
| `ONDC:RET18` | Toys & Games |
| `ONDC:RET19` | Miscellaneous Retail |
| `ONDC:LOG` | Logistics |
| `ONDC:FIS` | Financial Services |
| `ONDC:MBL` | Mobility |
| `ONDC:SRV` | Services |

---

## API Matrix (Beckn Protocol)

### Order Lifecycle

| Phase | BAP Sends (Action) | BPP Responds (Callback) | Description |
|-------|--------------------|-----------------------|-------------|
| Discovery | `search` | `on_search` | Find products/services by intent |
| Selection | `select` | `on_select` | Select items, get quote with breakup |
| Initialization | `init` | `on_init` | Provide billing/shipping, get payment terms |
| Confirmation | `confirm` | `on_confirm` | Place order, process payment |
| Status | `status` | `on_status` | Check order status |
| Tracking | `track` | `on_track` | Get real-time tracking info |
| Cancellation | `cancel` | `on_cancel` | Cancel order with reason code |
| Update | `update` | `on_update` | Modify order details, returns |
| Rating | `rating` | `on_rating` | Rate seller/product/fulfillment |
| Support | `support` | `on_support` | Request customer support |

### Communication Pattern

```
BAP ──search──> Gateway ──search──> BPP (multicast)
BAP <──on_search── Gateway <──on_search── BPP

BAP ──select/init/confirm/status/track/cancel/update/rating/support──> BPP (direct P2P)
BAP <──on_select/on_init/on_confirm/on_status/on_track/on_cancel/on_update/on_rating/on_support── BPP
```

**Note**: Gateway is only involved in the `search` phase. All subsequent calls are direct peer-to-peer between BAP and BPP.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Runtime | Node.js v18+ |
| Framework | Express.js / Fastify |
| Database | MongoDB (primary), Redis (cache) |
| Message Queue | RabbitMQ / Redis Streams |
| Search Engine | Elasticsearch (catalog search) |
| Cryptography | Ed25519 (signing), X25519 (encryption), BLAKE-512 (hashing) |
| Containerization | Docker, Docker Compose |
| CI/CD | GitHub Actions |
| Monitoring | Prometheus, Grafana |
| API Docs | OpenAPI / Swagger |

---

## Project Structure

```
ondc/
├── shared/                    # Shared libraries across all roles
│   ├── crypto/                # Ed25519 signing, verification, key management
│   ├── protocol/              # Beckn protocol request/response builders
│   ├── models/                # Common data models (Context, Order, Item, etc.)
│   ├── utils/                 # Utility functions
│   ├── config/                # Shared configuration
│   ├── middleware/             # Auth, logging, error handling middleware
│   └── validators/            # Schema validators (JSON Schema / Ajv)
├── bap/                       # Buyer Application Platform
│   ├── src/
│   │   ├── api/               # Outbound action API callers
│   │   ├── callbacks/         # Inbound on_* callback handlers
│   │   ├── services/          # Business logic services
│   │   ├── controllers/       # HTTP controllers
│   │   ├── routes/            # Express route definitions
│   │   ├── models/            # BAP-specific data models
│   │   └── config/            # BAP configuration
│   └── tests/
├── bpp/                       # Buyer Platform Provider (Seller App)
│   ├── src/
│   │   ├── api/               # Outbound on_* callback dispatchers
│   │   ├── actions/           # Inbound action endpoint handlers
│   │   ├── services/          # Business logic services
│   │   ├── controllers/       # HTTP controllers
│   │   ├── routes/            # Express route definitions
│   │   ├── catalog/           # Catalog management
│   │   ├── inventory/         # Inventory management
│   │   ├── models/            # BPP-specific data models
│   │   └── config/            # BPP configuration
│   └── tests/
├── gateway/                   # Beckn Gateway
│   ├── src/
│   │   ├── router/            # Search request multicast/routing
│   │   ├── services/          # Gateway services
│   │   ├── controllers/       # HTTP controllers
│   │   └── config/            # Gateway configuration
│   └── tests/
├── registry/                  # ONDC Registry
│   ├── src/
│   │   ├── api/               # Lookup, subscribe APIs
│   │   ├── services/          # Registry services
│   │   ├── controllers/       # HTTP controllers
│   │   ├── models/            # Subscriber models
│   │   └── config/            # Registry configuration
│   └── tests/
├── logistics/                 # Logistics Service Provider
│   ├── src/
│   │   ├── api/               # Logistics APIs
│   │   ├── services/          # Logistics business logic
│   │   ├── controllers/       # HTTP controllers
│   │   ├── models/            # Logistics data models
│   │   └── config/            # LSP configuration
│   └── tests/
├── igm/                       # Issue & Grievance Management
│   ├── src/
│   │   ├── api/               # IGM APIs (issue, issue_status, on_issue, on_issue_status)
│   │   ├── services/          # Grievance handling logic
│   │   ├── controllers/       # HTTP controllers
│   │   ├── models/            # IGM data models
│   │   └── config/            # IGM configuration
│   └── tests/
├── docs/                      # Documentation
│   ├── architecture/          # Architecture decision records
│   ├── api-specs/             # OpenAPI specifications
│   ├── onboarding/            # Onboarding guides
│   └── domains/               # Domain-specific documentation
├── scripts/                   # Build, deploy, and utility scripts
├── docker/                    # Docker files
├── config/                    # Environment-specific configs
│   ├── staging/
│   ├── pre-production/
│   └── production/
├── PROJECT_PLAN.md            # This file
├── package.json
├── docker-compose.yml
└── .gitignore
```

---

## Implementation Phases

### Phase 1: Protocol Foundation (Weeks 1-3)

**Goal**: Build the shared protocol layer that all roles depend on.

| # | Task | Priority | Effort |
|---|------|----------|--------|
| 1.1 | Ed25519 key pair generation and management | HIGH | 3d |
| 1.2 | Request signing (Authorization header) | HIGH | 3d |
| 1.3 | Signature verification | HIGH | 2d |
| 1.4 | Registry lookup client with Redis caching | HIGH | 3d |
| 1.5 | Beckn context builder (domain, city, transaction_id, etc.) | HIGH | 2d |
| 1.6 | Async request-callback framework | HIGH | 3d |
| 1.7 | ACK/NACK response handler | HIGH | 1d |
| 1.8 | JSON Schema validators for all 20 APIs | MEDIUM | 5d |
| 1.9 | Error handling middleware | MEDIUM | 2d |
| 1.10 | Logging and monitoring setup | MEDIUM | 2d |

**Deliverables**:
- `shared/crypto/` - Complete signing/verification module
- `shared/protocol/` - Request builders for all 20 APIs
- `shared/validators/` - Schema validation for all API payloads
- `shared/middleware/` - Auth, logging, error handling middleware

---

### Phase 2: BAP - Buyer Application Platform (Weeks 4-8)

**Goal**: Build a fully functional Buyer App capable of the complete order lifecycle.

| # | Task | Priority | Effort |
|---|------|----------|--------|
| 2.1 | Search API caller + on_search callback handler | HIGH | 3d |
| 2.2 | Select API caller + on_select callback handler | HIGH | 2d |
| 2.3 | Init API caller + on_init callback handler | HIGH | 2d |
| 2.4 | Confirm API caller + on_confirm callback handler | HIGH | 3d |
| 2.5 | Status API caller + on_status callback handler | HIGH | 2d |
| 2.6 | Track API caller + on_track callback handler | MEDIUM | 1d |
| 2.7 | Cancel API caller + on_cancel callback handler | MEDIUM | 2d |
| 2.8 | Update API caller + on_update callback handler | MEDIUM | 2d |
| 2.9 | Rating API caller + on_rating callback handler | LOW | 1d |
| 2.10 | Support API caller + on_support callback handler | LOW | 1d |
| 2.11 | Payment gateway integration (Razorpay/Juspay) | HIGH | 5d |
| 2.12 | Order management service | HIGH | 3d |
| 2.13 | User authentication and profile management | HIGH | 3d |
| 2.14 | Cart and checkout service | HIGH | 3d |
| 2.15 | BAP unit and integration tests | HIGH | 5d |

**Deliverables**:
- Complete BAP server with all 10 action APIs and 10 callback handlers
- Payment integration
- Order management
- Test suite with >80% coverage

---

### Phase 3: BPP - Seller Application Platform (Weeks 9-14)

**Goal**: Build a fully functional Seller App with catalog and order management.

| # | Task | Priority | Effort |
|---|------|----------|--------|
| 3.1 | Search action handler + on_search callback dispatcher | HIGH | 4d |
| 3.2 | Select action handler + on_select callback dispatcher | HIGH | 2d |
| 3.3 | Init action handler + on_init callback dispatcher | HIGH | 2d |
| 3.4 | Confirm action handler + on_confirm callback dispatcher | HIGH | 3d |
| 3.5 | Status action handler + on_status callback dispatcher | HIGH | 2d |
| 3.6 | Track action handler + on_track callback dispatcher | MEDIUM | 2d |
| 3.7 | Cancel action handler + on_cancel callback dispatcher | MEDIUM | 2d |
| 3.8 | Update action handler + on_update callback dispatcher | MEDIUM | 2d |
| 3.9 | Rating action handler + on_rating callback dispatcher | LOW | 1d |
| 3.10 | Support action handler + on_support callback dispatcher | LOW | 1d |
| 3.11 | Catalog management service (CRUD, incremental updates) | HIGH | 5d |
| 3.12 | Inventory management service | HIGH | 3d |
| 3.13 | Order fulfillment service | HIGH | 3d |
| 3.14 | Seller onboarding and dashboard | MEDIUM | 5d |
| 3.15 | Multi-domain support (RET10-RET19) | MEDIUM | 5d |
| 3.16 | BPP unit and integration tests | HIGH | 5d |

**Deliverables**:
- Complete BPP server with all 10 action handlers and 10 callback dispatchers
- Catalog management with Elasticsearch indexing
- Inventory tracking
- Seller dashboard
- Test suite with >80% coverage

---

### Phase 4: Gateway (Weeks 15-17)

**Goal**: Build the search routing and discovery infrastructure.

| # | Task | Priority | Effort |
|---|------|----------|--------|
| 4.1 | Search request receiver and parser | HIGH | 2d |
| 4.2 | Subscriber filtering by domain and city | HIGH | 3d |
| 4.3 | Search request multicast to eligible BPPs | HIGH | 3d |
| 4.4 | Response aggregation from BPPs | HIGH | 3d |
| 4.5 | on_search routing back to requesting BAP | HIGH | 2d |
| 4.6 | X-Gateway-Authorization header management | HIGH | 1d |
| 4.7 | Rate limiting and throttling | MEDIUM | 2d |
| 4.8 | Health checks and monitoring | MEDIUM | 2d |
| 4.9 | Gateway unit and integration tests | HIGH | 3d |

**Deliverables**:
- Functional gateway with multicast search routing
- Domain/city-based subscriber filtering
- Rate limiting and monitoring

---

### Phase 5: Registry (Weeks 18-20)

**Goal**: Build the trust infrastructure and subscriber management.

| # | Task | Priority | Effort |
|---|------|----------|--------|
| 5.1 | Subscriber registration (`/subscribe` API) | HIGH | 3d |
| 5.2 | Subscriber lookup (`/lookup` API) | HIGH | 2d |
| 5.3 | Public key management and rotation | HIGH | 3d |
| 5.4 | Domain verification (ondc-site-verification.html) | HIGH | 2d |
| 5.5 | OCSP validation for SSL certificates | MEDIUM | 2d |
| 5.6 | Subscriber status management (active/inactive) | MEDIUM | 2d |
| 5.7 | Admin dashboard for NP management | MEDIUM | 3d |
| 5.8 | Registry unit and integration tests | HIGH | 3d |

**Deliverables**:
- Complete registry with subscribe/lookup APIs
- Key management and domain verification
- Admin dashboard

---

### Phase 6: Logistics Service Provider (Weeks 21-24)

**Goal**: Build logistics/delivery integration as a specialized BPP.

| # | Task | Priority | Effort |
|---|------|----------|--------|
| 6.1 | Logistics search (serviceability check) | HIGH | 3d |
| 6.2 | Quote generation (pricing, delivery time) | HIGH | 3d |
| 6.3 | Order confirmation and rider assignment | HIGH | 3d |
| 6.4 | Real-time tracking service | HIGH | 5d |
| 6.5 | Status update webhooks | MEDIUM | 2d |
| 6.6 | Proof of delivery (POD) management | MEDIUM | 2d |
| 6.7 | Cancellation and return logistics | MEDIUM | 3d |
| 6.8 | Logistics unit and integration tests | HIGH | 3d |

**Deliverables**:
- Complete logistics BPP
- Real-time tracking
- Quote engine

---

### Phase 7: IGM & Ancillary Services (Weeks 25-28)

**Goal**: Build issue management, reconciliation, and compliance tooling.

| # | Task | Priority | Effort |
|---|------|----------|--------|
| 7.1 | Issue creation (`/issue` API) | HIGH | 3d |
| 7.2 | Issue status tracking (`/issue_status` API) | HIGH | 2d |
| 7.3 | Issue resolution workflow | HIGH | 3d |
| 7.4 | Escalation management | MEDIUM | 2d |
| 7.5 | Payment reconciliation service (RSP) | MEDIUM | 5d |
| 7.6 | Compliance log generation | HIGH | 3d |
| 7.7 | Log validation utility integration | HIGH | 2d |
| 7.8 | Pramaan test bench integration | HIGH | 3d |

**Deliverables**:
- Complete IGM implementation
- Payment reconciliation
- Compliance tooling

---

### Phase 8: Testing, Compliance & Deployment (Weeks 29-32)

**Goal**: End-to-end testing, ONDC compliance, and production deployment.

| # | Task | Priority | Effort |
|---|------|----------|--------|
| 8.1 | End-to-end integration tests (all roles) | HIGH | 5d |
| 8.2 | ONDC Staging environment testing | HIGH | 5d |
| 8.3 | Compliance log submission and review | HIGH | 3d |
| 8.4 | Pre-production testing | HIGH | 5d |
| 8.5 | Docker Compose setup for local development | HIGH | 2d |
| 8.6 | Kubernetes deployment manifests | MEDIUM | 3d |
| 8.7 | CI/CD pipeline setup (GitHub Actions) | HIGH | 3d |
| 8.8 | Production deployment and monitoring | HIGH | 5d |

**Deliverables**:
- Full E2E test suite
- ONDC compliance certification
- Production-ready deployment

---

## Environment Configuration

| Environment | Purpose | Registry URL |
|-------------|---------|-------------|
| **Staging** | Development and initial testing | `https://staging.registry.ondc.org` |
| **Pre-Production** | Compliance testing | `https://preprod.registry.ondc.org` |
| **Production** | Live network | `https://prod.registry.ondc.org` |

---

## Key Dependencies and References

| Resource | URL |
|----------|-----|
| ONDC Protocol Specs | https://github.com/ONDC-Official/ONDC-Protocol-Specs |
| Beckn Protocol Docs | https://developers.becknprotocol.io |
| ONDC Developer Docs | https://github.com/ONDC-Official/developer-docs |
| ONDC Retail Specs | https://github.com/ONDC-Official/ONDC-RET-Specifications |
| Log Validation Utility | https://github.com/ONDC-Official/log-validation-utility |
| Pramaan Test Bench | https://pramaan.ondc.org |
| ONDC Seller App SDK | https://github.com/ONDC-Official/seller-app-sdk |

---

## Team Roles Needed

| Role | Responsibility | Headcount |
|------|---------------|-----------|
| Backend Engineer | API development, protocol implementation | 3-4 |
| Frontend Engineer | Buyer/Seller dashboards, mobile apps | 2-3 |
| DevOps Engineer | CI/CD, Docker, Kubernetes, monitoring | 1-2 |
| QA Engineer | Testing, compliance, Pramaan certification | 1-2 |
| Product Manager | Requirements, domain expertise, ONDC liaison | 1 |
| Tech Lead | Architecture, code review, technical decisions | 1 |

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| ONDC spec changes during development | HIGH | Pin spec version, implement adapter pattern |
| Registry downtime | HIGH | Cache registry lookups in Redis with TTL |
| Payment integration complexity | MEDIUM | Use Juspay/Razorpay ONDC-certified gateways |
| Compliance log rejection | MEDIUM | Use log-validation-utility early in development |
| Multi-domain complexity | MEDIUM | Start with one domain (RET10), expand incrementally |
| Ed25519 key rotation | LOW | Implement automated key rotation with overlap period |

---

*Last Updated: 2026-02-11*
*Version: 1.0.0*
