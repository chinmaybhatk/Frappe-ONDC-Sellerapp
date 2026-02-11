/**
 * ONDC/Beckn Protocol Response Builder
 *
 * Builds ACK/NACK responses for incoming API requests.
 *
 * ACK response: { "message": { "ack": { "status": "ACK" } } }
 * NACK response: { "message": { "ack": { "status": "NACK" } }, "error": { ... } }
 *
 * TODO: Implement
 * - buildAck() - Build ACK response
 * - buildNack(errorCode, errorMessage) - Build NACK response with error details
 * - buildError(type, code, message, path) - Build error object
 */

module.exports = {
  // TODO: Implement response builder functions
};
