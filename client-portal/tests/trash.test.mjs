import test from 'node:test';
import assert from 'node:assert/strict';
import * as inbox from '../public/inbox.mjs';
import { csv } from '../public/csv.mjs';

const now = new Date('2026-11-02T12:00:00Z');
const rows = [
  { id: '1', name: 'Active caller', status: 'contacted', created_at: '2026-11-01T12:00:00Z', is_trashed: false },
  { id: '2', name: 'Test caller', status: 'new', created_at: '2026-11-01T12:00:00Z', is_trashed: true },
  { id: '3', name: 'Older caller', status: 'closed', created_at: '2026-10-01T12:00:00Z' },
];
test('Inbox and its export exclude trash; Trash contains only removed inquiries', () => {
  assert.equal(typeof inbox.inquiryScope, 'function');
  assert.deepEqual(inbox.inquiryScope(rows).map(r => r.id), ['1', '3']);
  assert.deepEqual(inbox.inquiryScope(rows, 'trash').map(r => r.id), ['2']);
  const exported = csv(inbox.inquiryScope(rows));
  assert.match(exported, /Active caller/);
  assert.doesNotMatch(exported, /Test caller/);
});
test('monthly and all-time summaries omit trash', () => {
  assert.deepEqual(inbox.monthSummary(rows, [['new'], ['contacted'], ['closed']], now), {
    month: 'November', total: 1, counts: { new: 0, contacted: 1, closed: 0 }, allTime: 2,
  });
});
test('trashed new inquiries do not open an empty New filter', () => {
  assert.equal(inbox.defaultFilter(rows), '');
});
test('restoring an inquiry brings back its previous status and count', () => {
  const restored = rows.map(r => r.id === '2' ? { ...r, is_trashed: false } : r);
  assert.equal(inbox.defaultFilter(restored), 'new');
  assert.equal(inbox.monthSummary(restored, [['new']], now).counts.new, 1);
  assert.equal(inbox.inquiryScope(restored, 'trash').length, 0);
});
