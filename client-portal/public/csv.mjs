import { corrections, formatPhone } from './validate.mjs';

// A cell starting with = + - @ (or a tab/return) can run as a formula in Excel or
// Sheets. Prefixing an apostrophe makes the spreadsheet treat it as plain text.
export function cell(value) {
  let text = String(value ?? '');
  if (/^[\s]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

const LABELS = { name: 'name', phone: 'phone', service: 'service', preferred_time: 'time', duration: 'duration', preferences: 'preferences' };

const pad = n => String(n).padStart(2, '0');
export const localDateTime = iso => {
  const d = new Date(iso);
  return isNaN(d) ? '' : `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export function csv(rows, { serviceLabel = s => s, statusLabel = s => s, formatDate = localDateTime } = {}) {
  const columns = [
    ['Received', r => formatDate(r.created_at)],
    ['Caller name', r => r.name],
    ['Phone', r => formatPhone(r.phone)],
    ['Service', r => serviceLabel(r.service)],
    ['Requested time', r => r.preferred_time],
    ['Duration', r => r.duration],
    ['Preferences', r => r.preferences],
    ['Follow-up status', r => statusLabel(r.status)],
    ['Your notes', r => r.owner_notes],
    ['Corrected by you', r => (corrections(r).length ? 'Yes' : 'No')],
    ['Originally captured (if corrected)', r => corrections(r).map(k => `${LABELS[k]}: ${r.original[k] ?? ''}`).join('; ')],
    ['Request ID', r => r.id],
  ];
  return [columns.map(([h]) => cell(h)).join(','), ...rows.map(r => columns.map(([, get]) => cell(get(r))).join(','))].join('\r\n');
}
