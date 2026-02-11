# ONDC Network Participant Onboarding Guide

## Prerequisites

1. Registered business entity in India
2. Domain name with SSL certificate
3. Technical infrastructure (servers, databases)
4. Development team familiar with Beckn protocol

## Step-by-Step Onboarding

### Step 1: Register on ONDC Portal

Visit https://www.ondc.org/ondc-how-to-join/ and register as a Network Participant.

### Step 2: Generate Key Pairs

Generate Ed25519 (signing) and X25519 (encryption) key pairs:

```bash
# Using the shared/crypto/keys.js module (once implemented)
node scripts/generate-keys.js
```

This produces:
- `signing_private_key` - Ed25519 private key (KEEP SECRET)
- `signing_public_key` - Ed25519 public key (share with Registry)
- `encryption_private_key` - X25519 private key (KEEP SECRET)
- `encryption_public_key` - X25519 public key (share with Registry)

### Step 3: Domain Verification

Create `ondc-site-verification.html` at your domain root:

```
https://your-domain.com/ondc-site-verification.html
```

Content: A signed challenge string provided during registration.

### Step 4: Subscribe to Registry

Call the `/subscribe` API to register in the ONDC Registry:

```json
POST https://staging.registry.ondc.org/subscribe
{
  "subscriber_id": "your-domain.com",
  "subscriber_url": "https://your-domain.com/ondc",
  "type": "BAP",
  "domain": "ONDC:RET10",
  "city": ["std:080"],
  "signing_public_key": "<base64-ed25519-public-key>",
  "encr_public_key": "<base64-x25519-public-key>",
  "unique_key_id": "uk-001",
  "valid_from": "2026-01-01T00:00:00Z",
  "valid_until": "2027-01-01T00:00:00Z"
}
```

### Step 5: Implement Required APIs

Implement all APIs required for your role (see role-specific READMEs).

### Step 6: Test on Staging

Use ONDC's Pramaan test bench (https://pramaan.ondc.org) to validate your implementation.

### Step 7: Submit Compliance Logs

Generate and submit compliance logs for review using the log-validation-utility.

### Step 8: Get Production Access

After passing compliance review, get whitelisted for Pre-Production and then Production.

## Environments

| Environment | Registry URL | Purpose |
|-------------|-------------|---------|
| Staging | https://staging.registry.ondc.org | Development |
| Pre-Production | https://preprod.registry.ondc.org | Compliance testing |
| Production | https://prod.registry.ondc.org | Live network |
