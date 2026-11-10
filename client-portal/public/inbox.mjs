// Organising the request list for an owner: grouped by day, opened on what still
// needs a callback, with this month's numbers at the top, and the call's spoken
// turns for one request. Pure functions; app.js does the rendering.

const DAY_MS = 86400000;
const startOfDay = d => new Date(d.getFullYear(), d.getMonth(), d.getDate());

// Trash is independent of follow-up status; restoration keeps that status.
export function inquiryScope(rows, view = 'inbox') {
  return rows.filter(row => (row.is_trashed === true) === (view === 'trash'));
}

// Received-date filters use the viewer's local calendar, like the day headings.
// Exclusive next-midnight boundaries include the whole final day, even across DST.
export function filterByReceivedDate(rows, { period = 'all', from = '', to = '' } = {}, now = new Date()) {
  if (period === 'all') return { rows, error: '', label: 'All time' };
  let start = startOfDay(now), end = new Date(start), label;
  if (period === 'today') {
    end.setDate(end.getDate() + 1);
    label = 'Today';
  } else if (period === 'week') {
    start.setDate(start.getDate() - (start.getDay() + 6) % 7);
    end = new Date(start);
    end.setDate(end.getDate() + 7);
    label = 'This week (Mon–Sun)';
  } else if (period === 'month') {
    start = new Date(now.getFullYear(), now.getMonth(), 1);
    end = new Date(now.getFullYear(), now.getMonth() + 1, 1);
    label = 'This month';
  } else if (period === 'custom') {
    const parse = value => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
      const [y, m, d] = value.split('-').map(Number);
      const date = new Date(y, m - 1, d);
      return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? date : null;
    };
    start = parse(from); end = parse(to);
    if (!start || !end) return { rows: [], error: 'Choose a valid start and end date.', label: 'Custom dates' };
    if (start > end) return { rows: [], error: 'End date must be on or after start date.', label: 'Custom dates' };
    label = `${start.toLocaleDateString()} – ${end.toLocaleDateString()}`;
    end.setDate(end.getDate() + 1);
  } else {
    return { rows: [], error: 'Choose a received-date period.', label: '' };
  }
  return {
    rows: rows.filter(row => {
      const received = row.created_at ? new Date(row.created_at) : null;
      return received !== null && received >= start && received < end;
    }),
    error: '', label,
  };
}

// "Today", "Yesterday", "Saturday, Sep 20" within the week, then "September 12" (with the year if not this year).
export function dayLabel(iso, now = new Date()) {
  const d = new Date(iso);
  if (isNaN(d)) return 'Unknown date';
  const days = Math.round((startOfDay(now) - startOfDay(d)) / DAY_MS);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days > 1 && days < 7) return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) });
}

// Rows arrive newest first; keep that order and cut it into days.
export function groupByDay(rows, now = new Date()) {
  const groups = [];
  for (const row of rows) {
    const d = new Date(row.created_at);
    const key = isNaN(d) ? 'unknown' : startOfDay(d).getTime();
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.rows.push(row);
    else groups.push({ key, label: dayLabel(row.created_at, now), rows: [row] });
  }
  return groups;
}

// This calendar month's requests by follow-up status, plus the all-time total.
export function monthSummary(rows, statuses, now = new Date()) {
  rows = inquiryScope(rows);
  const thisMonth = rows.filter(r => {
    const d = new Date(r.created_at);
    return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
  });
  const counts = Object.fromEntries(statuses.map(([code]) => [code, thisMonth.filter(r => r.status === code).length]));
  return { month: now.toLocaleDateString(undefined, { month: 'long' }), total: thisMonth.length, counts, allTime: rows.length };
}

export function summaryText({ month, total, counts, allTime }, statuses) {
  if (!allTime) return '';
  const parts = statuses.filter(([code]) => counts[code]).map(([code, label]) => `${counts[code]} ${label.toLowerCase()}`);
  const head = `${month}: ${total} request${total === 1 ? '' : 's'}`;
  const tail = allTime > total ? ` · ${allTime} all time` : '';
  return parts.length ? `${head} · ${parts.join(' · ')}${tail}` : head + tail;
}

// Open on what needs a callback; fall back to everything when nothing is new.
export function defaultFilter(rows) {
  return inquiryScope(rows).some(r => r.status === 'new') ? 'new' : '';
}

// The call as the owner should read it. Tolerates missing or odd data: the
// database only guarantees `turns` is a list.
export function transcriptTurns(row) {
  const turns = row?.transcript?.turns;
  if (!Array.isArray(turns)) return [];
  return turns
    .filter(t => t && typeof t.text === 'string' && t.text.trim())
    .map(t => ({ speaker: t.speaker === 'caller' ? 'Caller' : 'Receptionist', text: t.text.trim() }));
}
