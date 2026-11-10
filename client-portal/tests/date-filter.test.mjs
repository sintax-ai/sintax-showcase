import test from 'node:test';
import assert from 'node:assert/strict';
import * as inbox from '../public/inbox.mjs';

// Fictional records; local constructors make the expected calendar days explicit.
const row = (id, year, month, day, hour = 12, minute = 0) =>
  ({ id, created_at: new Date(year, month - 1, day, hour, minute).toISOString() });
const now = new Date(2026, 9, 8, 12);
const rows = [row('sep', 2026, 9, 30), row('oct', 2026, 10, 1),
  row('sun', 2026, 10, 4), row('mon', 2026, 10, 5, 0),
  row('today', 2026, 10, 8), row('last-minute', 2026, 10, 11, 23, 59),
  row('next-week', 2026, 10, 12, 0), row('next-month', 2026, 11, 1, 0)];
const ids = result => result.rows.map(r => r.id);

test('All time preserves records, including those without a valid timestamp', () => {
  const input = [...rows, { id: 'unknown', created_at: null }];
  assert.deepEqual(inbox.filterByReceivedDate(input, { period: 'all' }, now).rows, input);
});
test('Today and calendar month exclude neighboring periods', () => {
  assert.deepEqual(ids(inbox.filterByReceivedDate(rows, { period: 'today' }, now)), ['today']);
  assert.deepEqual(ids(inbox.filterByReceivedDate(rows, { period: 'month' }, now)),
    ['oct', 'sun', 'mon', 'today', 'last-minute', 'next-week']);
});
test('Week includes Monday midnight through Sunday night, including when today is Sunday', () => {
  for (const date of [now, new Date(2026, 9, 11, 12)]) {
    assert.deepEqual(ids(inbox.filterByReceivedDate(rows, { period: 'week' }, date)),
      ['mon', 'today', 'last-minute']);
  }
});
test('Custom range includes both dates and supports a single day', () => {
  const selection = { period: 'custom', from: '2026-10-05', to: '2026-10-11' };
  assert.deepEqual(ids(inbox.filterByReceivedDate(rows, selection, now)), ['mon', 'today', 'last-minute']);
  assert.deepEqual(ids(inbox.filterByReceivedDate(rows,
    { period: 'custom', from: '2026-10-11', to: '2026-10-11' }, now)), ['last-minute']);
});
test('Incomplete, impossible, and reversed custom dates return an error and no exportable rows', () => {
  for (const [from, to] of [['', ''], ['2026-10-08', ''], ['2026-02-30', '2026-03-01'],
    ['2026-10-09', '2026-10-08']]) {
    const result = inbox.filterByReceivedDate(rows, { period: 'custom', from, to }, now);
    assert.ok(result.error);
    assert.deepEqual(result.rows, []);
  }
});
test('Custom dates span daylight-saving changes without losing the final evening', () => {
  for (const [month, day] of [[3, 8], [11, 1]]) {
    const date = `2026-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const input = [row('start', 2026, month, day, 0), row('end', 2026, month, day, 23, 59),
      row('outside', 2026, month, day + 1, 0)];
    assert.deepEqual(ids(inbox.filterByReceivedDate(input, { period: 'custom', from: date, to: date }, now)),
      ['start', 'end']);
  }
});
test('Calendar week crosses the year boundary correctly', () => {
  const input = [row('before', 2025, 12, 28), row('start', 2025, 12, 29),
    row('end', 2026, 1, 4, 23, 59), row('after', 2026, 1, 5)];
  assert.deepEqual(ids(inbox.filterByReceivedDate(input, { period: 'week' }, new Date(2026, 0, 1))),
    ['start', 'end']);
});
