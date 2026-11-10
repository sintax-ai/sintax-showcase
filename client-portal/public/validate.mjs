// Checks an owner's edits before saving. The database enforces the same rules
// (supabase/migrations/002_owner_corrections.sql); this gives friendly messages first.

export const CAPTURED = ['name', 'phone', 'service', 'preferred_time', 'duration', 'preferences'];
export const STATUSES = [
  ['new', 'New', 'Not contacted yet'],
  ['contacted', 'Contacted', "You've reached out to the caller"],
  ['closed', 'Closed', 'No more follow-up needed'],
];

const LIMITS = { name: 200, preferred_time: 500, duration: 500, preferences: 2000, owner_notes: 10000 };

// US/Canada only, matching the voice agent. Returns +1XXXXXXXXXX or null.
export function normalizePhone(input) {
  const text = String(input ?? '').trim();
  if (!text || /[^\d\s+().-]/.test(text)) return null;
  let digits = text.replace(/\D/g, '');
  if (digits.length === 10) digits = '1' + digits;
  return digits.length === 11 && digits.startsWith('1') ? '+' + digits : null;
}

export function formatPhone(value) {
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(String(value ?? ''));
  return m ? `(${m[1]}) ${m[2]}-${m[3]}` : String(value ?? '');
}

const count = n => n.toLocaleString('en-US');

// values: raw form values. serviceCodes: the allowed menu codes.
// Returns { clean, errors }; errors is null when everything is valid.
export function validateInquiry(values, serviceCodes) {
  const clean = {};
  const errors = {};
  const text = key => String(values[key] ?? '').trim();

  clean.name = text('name');
  if (!clean.name) errors.name = "Enter the caller's name.";
  else if (clean.name.length > LIMITS.name) errors.name = `Keep the name under ${LIMITS.name} characters.`;

  clean.phone = normalizePhone(values.phone);
  if (!clean.phone) errors.phone = 'Enter a 10-digit US or Canadian number, like (202) 555-0142.';

  clean.service = text('service');
  if (!serviceCodes.includes(clean.service)) errors.service = 'Choose a service from the list.';

  for (const [key, what] of [['preferred_time', 'requested time'], ['duration', 'session length']]) {
    clean[key] = text(key);
    if (!clean[key]) errors[key] = `Enter the ${what}. If it isn't settled, write "to discuss".`;
    else if (clean[key].length > LIMITS[key]) errors[key] = `Keep this under ${LIMITS[key]} characters.`;
  }

  clean.preferences = text('preferences');
  if (clean.preferences.length > LIMITS.preferences) errors.preferences = `Keep this under ${count(LIMITS.preferences)} characters.`;

  clean.status = text('status');
  if (!STATUSES.some(([s]) => s === clean.status)) errors.status = 'Choose a follow-up status.';

  // Notes keep their line breaks and spacing; only the length is limited.
  clean.owner_notes = String(values.owner_notes ?? '');
  if (clean.owner_notes.length > LIMITS.owner_notes) errors.owner_notes = `Keep notes under ${count(LIMITS.owner_notes)} characters.`;

  return { clean, errors: Object.keys(errors).length ? errors : null };
}

// Only the fields that actually differ, so a save never rewrites untouched data.
export function changedFields(clean, row) {
  return Object.fromEntries(Object.entries(clean).filter(([k, v]) => (row[k] ?? '') !== v));
}

// Which captured fields the owner has corrected, compared with what the agent heard.
export function corrections(row) {
  if (!row.original) return [];
  return CAPTURED.filter(k => (row.original[k] ?? '') !== (row[k] ?? ''));
}
