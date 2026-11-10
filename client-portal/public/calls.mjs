// Calls and customer requests are separate records, joined only by explicit IDs.
export function linkedRequests(call, rows) {
  return rows.filter(r => r.business_id === call.business_id && r.call_id === call.id);
}
export function recordingState(call, now = Date.now()) {
  if (call.recording_expires_at && new Date(call.recording_expires_at).getTime() <= now) return 'expired';
  if (call.recording_status === 'available' && call.recording_path) return 'available';
  // Interrupted workers can leave pending rows. Do not promise endless processing.
  if (call.recording_status === 'pending' && now - new Date(call.started_at).getTime() < 6 * 3600000) return 'pending';
  return 'unavailable';
}
export function durationLabel(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return 'Duration unavailable';
  const n = Math.max(0, Math.round(seconds));
  return n < 60 ? `${n}s` : `${Math.floor(n / 60)}m ${n % 60}s`;
}
export function callMatches(call, requests, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const linked = linkedRequests(call, requests);
  return [call.session_id, call.room_id, call.mode, ...linked.flatMap(r => [r.name, r.phone, r.service])]
    .some(value => String(value ?? '').toLowerCase().includes(q));
}
export function recordingURL(base, signed) {
  if (typeof signed !== 'string' || !signed.startsWith('/object/sign/call-recordings/')) throw new Error('Invalid playback link.');
  const origin = new URL(base).origin;
  const url = new URL(`/storage/v1${signed}`, origin);
  if (url.origin !== origin || !url.pathname.startsWith('/storage/v1/object/sign/call-recordings/')) throw new Error('Invalid playback link.');
  return url.href;
}
