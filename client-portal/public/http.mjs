class PortalError extends Error {
  constructor(kind, message, detail, code, status) { super(message); this.kind = kind; this.detail = detail; this.code = code; this.status = status; }
}
const MESSAGES = {
  offline: "Can't reach the server. Check your internet connection and try again.",
  expired: 'Your sign-in has expired. Please sign in again.',
  denied: "Your account doesn't have permission to do that.",
  invalid: "Some details aren't in an accepted format.",
  busy: 'Too many attempts. Please wait a minute and try again.',
  setup: 'The portal is waiting for a database update from SINTAX. Please try again later.',
  failed: 'Something went wrong on our side. Please try again.',
};

export function createHTTPCall({url,publishableKey,getSession,fetchImpl=fetch}) {
return async function call(path, { method = 'GET', body, headers = {}, auth = true } = {}) {
  const h = { apikey: publishableKey, 'Content-Type': 'application/json', ...headers };
  const session = getSession();
  if (auth && session) h.Authorization = `Bearer ${session.access_token}`;
  let res, text;
  try {
    res = await fetchImpl(url + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
    text = await res.text();
  } catch { throw new PortalError('offline', MESSAGES.offline); }
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON error page */ }
  if (res.ok) return data;
  const code = data?.code ?? data?.error_code;
  const detail = data?.message ?? data?.msg ?? data?.error_description;
  if (res.status === 401 && code !== '42501') throw new PortalError('expired', MESSAGES.expired, detail, code, res.status);
  if (code === '42501' || res.status === 403) throw new PortalError('denied', MESSAGES.denied, detail, code, res.status);
  if (code === '23514' || code === '22001') throw new PortalError('invalid', MESSAGES.invalid, detail, code, res.status);
  if (code === '42703' || code === 'PGRST204' || code === 'PGRST205') throw new PortalError('setup', MESSAGES.setup, detail, code, res.status);
  if (res.status === 429) throw new PortalError('busy', MESSAGES.busy, detail, code, res.status);
  const safeAuthMessage = code === 'weak_password' ? 'The new password does not meet the account password requirements.'
    : ['email_exists','user_already_exists'].includes(code) ? 'That email address cannot be used for this change.'
    : code === 'reauthentication_needed' ? 'Please sign in again before making this change.' : MESSAGES.failed;
  throw new PortalError(res.status === 400 && path.startsWith('/auth/') ? 'credentials' : 'failed', safeAuthMessage, detail, code, res.status);
}
;
}
