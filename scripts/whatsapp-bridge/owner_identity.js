/**
 * Pure identity gate for the two-number WhatsApp bot design.
 *
 * Owner elevation is narrower than ordinary allowlist access: only an explicitly
 * configured owner identity can be role-authorized, and wildcard ownership fails closed.
 */
export function isConfiguredOwnerDm({
  fromMe = false,
  isGroup = false,
  senderId,
  ownerUsers,
  matchesUser,
}) {
  if (fromMe || isGroup || !senderId) return false;
  if (!ownerUsers || ownerUsers.size === 0 || ownerUsers.has('*')) return false;
  if (typeof matchesUser !== 'function') return false;
  return Boolean(matchesUser(senderId, ownerUsers));
}
