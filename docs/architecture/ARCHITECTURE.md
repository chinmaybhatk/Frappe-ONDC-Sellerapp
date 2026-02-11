# ONDC Architecture Decision Records

## ADR-001: Monorepo with Workspaces

**Decision**: Use a monorepo with npm workspaces to manage all ONDC roles (BAP, BPP, Gateway, Registry, Logistics, IGM) in a single repository.

**Rationale**:
- Shared code (crypto, protocol, models) can be easily referenced
- Consistent versioning across all roles
- Simplified CI/CD pipeline
- Easier code review and knowledge sharing

---

## ADR-002: Async Request-Callback Pattern

**Decision**: Implement the Beckn async request-callback pattern using RabbitMQ for message queuing.

**Rationale**:
- ONDC/Beckn protocol is inherently asynchronous
- BAP sends request -> receives ACK -> BPP processes -> BPP sends callback
- RabbitMQ provides reliable message delivery and retry mechanisms
- Redis Streams as a lighter alternative for simpler deployments

---

## ADR-003: Ed25519 for Digital Signatures

**Decision**: Use Ed25519 (EdDSA) for all request signing and verification.

**Rationale**:
- Required by ONDC protocol specification
- Fast signing and verification
- Small key and signature sizes
- Library: libsodium / tweetnacl-js

---

## ADR-004: MongoDB as Primary Database

**Decision**: Use MongoDB as the primary data store for all roles.

**Rationale**:
- Flexible schema suits the varied ONDC data models
- Good support for JSON document storage (Beckn payloads)
- Matches the ONDC reference implementation tech stack
- Redis for caching (registry lookups, session data)

---

## ADR-005: Elasticsearch for Catalog Search

**Decision**: Use Elasticsearch for indexing and searching seller catalogs in the BPP.

**Rationale**:
- Fast full-text search across large product catalogs
- Supports faceted search, filtering, and geolocation queries
- Scales horizontally for high-volume search requests

---

## ADR-006: Domain-Driven Module Organization

**Decision**: Organize BPP code by domain concepts (catalog, inventory, fulfillment) rather than by technical layers.

**Rationale**:
- Better alignment with ONDC domain model
- Easier to add new domains (RET10, RET11, etc.)
- Clear separation of concerns per business capability
