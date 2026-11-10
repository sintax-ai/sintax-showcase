import test from 'node:test';
import assert from 'node:assert/strict';
import { Selection, moveSelected } from '../public/bulk-selection.mjs';

test('selection toggles matching results, prunes stale rows, and clears when scope changes', () => {
  const selection = new Selection();
  selection.setScope('business-a/inbox/all');
  selection.toggle('a', true);
  selection.toggle('b', true);
  selection.toggle('a', false);
  assert.deepEqual([...selection.ids], ['b']);
  selection.selectAll([{ id: 'a' }, { id: 'c' }]);
  assert.deepEqual([...selection.ids], ['a', 'c']);
  selection.prune([{ id: 'c' }]);
  assert.deepEqual([...selection.ids], ['c']);
  selection.setScope('business-a/inbox/all');
  assert.equal(selection.ids.size, 1);
  selection.setScope('business-b/inbox/all');
  assert.equal(selection.ids.size, 0);
  selection.toggle('c', true);
  selection.clear();
  assert.equal(selection.ids.size, 0);
});

test('bulk updates guard business and version, bound concurrency, and distinguish conflicts and unknown outcomes', async () => {
  const rows = ['ok', 'conflict', 'network', 'foreign'].map(id => ({ id, updated_at: '2026-11-02T12:00:00+00:00', business_id: id === 'foreign' ? 'other' : 'business&1', is_trashed: false }));
  let active = 0, peak = 0;
  const result = await moveSelected(rows, {
    businessId: 'business&1', removing: true, columns: 'id,is_trashed', concurrency: 2,
    request: async (path, options) => {
      const query = new URL(path, 'https://example.test').searchParams;
      assert.equal(query.get('business_id'), 'eq.business&1');
      assert.equal(query.get('updated_at'), 'eq.2026-11-02T12:00:00+00:00');
      assert.equal(options.method, 'PATCH');
      assert.deepEqual(options.body, { is_trashed: true });
      assert.equal(options.headers.Prefer, 'return=representation');
      active++; peak = Math.max(peak, active);
      await new Promise(resolve => setTimeout(resolve, 5));
      active--;
      if (query.get('id') === 'eq.network') throw new Error('Connection lost');
      if (query.get('id') === 'eq.conflict') return [];
      assert.equal(query.get('id'), 'eq.ok');
      return [{ id: 'ok', is_trashed: true }];
    },
  });
  assert.equal(peak, 2);
  assert.deepEqual(result.updated, [{ id: 'ok', is_trashed: true }]);
  assert.deepEqual(result.conflicts.map(row => row.id), ['conflict']);
  assert.deepEqual(result.failed.map(({ row }) => row.id).sort(), ['foreign', 'network']);
  assert.equal(rows[0].is_trashed, false, 'input rows do not change before server confirmation');
});

test('restore uses the same version guard and malformed replies never count as success', async () => {
  const rows = [{ id: 'a', business_id: 'business', updated_at: 'version' }];
  const result = await moveSelected(rows, {
    businessId: 'business', removing: false, columns: 'id',
    request: async (_path, options) => {
      assert.deepEqual(options.body, { is_trashed: false });
      return [{ id: 'wrong-row' }];
    },
  });
  assert.equal(result.updated.length, 0);
  assert.equal(result.failed.length, 1);
});
