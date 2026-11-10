import test from 'node:test';
import assert from 'node:assert/strict';

// These are DOM control boundary objects, not a replacement for the action.
// Catches accepting edits during a slow write and re-enabling read-only fields.
test('a pending action locks every control and restores previously disabled controls', async () => {
  const { disableControls } = await import('../public/action-controls.mjs');
  const notes = { disabled: false }, status = { disabled: false }, readonly = { disabled: true };
  const unlock = disableControls([notes, status, readonly]);
  assert.equal(notes.disabled, true);
  assert.equal(status.disabled, true);
  assert.equal(readonly.disabled, true);
  unlock();
  assert.equal(notes.disabled, false);
  assert.equal(status.disabled, false);
  assert.equal(readonly.disabled, true);
});
