/**
 * Auth0 Post Login Action.
 *
 * Attach this Action to the Login flow so the access token issued for the
 * flight API contains the profile claims required by the backend session
 * exchange.
 */
exports.onExecutePostLogin = async (event, api) => {
  const { email, email_verified: emailVerified, name, nickname } = event.user;

  if (typeof email !== 'string' || email.length === 0) return;

  api.accessToken.setCustomClaim('email', email);
  api.accessToken.setCustomClaim('email_verified', emailVerified === true);
  api.accessToken.setCustomClaim('name', name || nickname || email);
};
