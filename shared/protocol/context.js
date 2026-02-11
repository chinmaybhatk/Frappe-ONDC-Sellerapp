/**
 * Beckn Context Builder
 *
 * Builds the `context` object required in every ONDC API request.
 *
 * Context fields:
 * - domain: e.g., "ONDC:RET10" (Grocery)
 * - action: e.g., "search", "select", "init", "confirm"
 * - country: "IND"
 * - city: e.g., "std:080" (Bangalore)
 * - core_version: "1.2.0"
 * - bap_id, bap_uri: Buyer app subscriber details
 * - bpp_id, bpp_uri: Seller app subscriber details
 * - transaction_id: Unique per transaction (UUID)
 * - message_id: Unique per message (UUID)
 * - timestamp: ISO 8601 format
 * - ttl: Time-to-live for the request
 *
 * TODO: Implement
 * - buildContext(action, domain, city, bapId, bapUri, bppId, bppUri) - Build context object
 * - buildSearchContext(domain, city, bapId, bapUri) - Search-specific context (no bpp_id)
 * - buildCallbackContext(originalContext, callbackAction) - Build callback context from original
 */

module.exports = {
  // TODO: Implement context builder functions
};
