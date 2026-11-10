// Selection belongs to one business, view, and filter combination.
export class Selection {
  ids = new Set();
  scope = null;
  setScope(scope) {
    if (this.scope !== scope) this.clear();
    this.scope = scope;
  }
  toggle(id, selected) { selected ? this.ids.add(id) : this.ids.delete(id); }
  selectAll(rows) { this.ids = new Set(rows.map(row => row.id)); }
  prune(rows) {
    const available = new Set(rows.map(row => row.id));
    for (const id of this.ids) if (!available.has(id)) this.ids.delete(id);
  }
  clear() { this.ids.clear(); }
}

// A failed response can mean an unconfirmed write. Only returned rows count as
// successful; callers refresh before allowing the owner to try again.
export async function moveSelected(rows, { businessId, removing, request, columns, table = 'inquiries', concurrency = 4 }) {
  if (!['inquiries', 'calls'].includes(table)) throw new Error('Invalid record type.');
  const result = { updated: [], conflicts: [], failed: [] };
  let next = 0;
  async function worker() {
    while (next < rows.length) {
      const row = rows[next++];
      try {
        if (row.business_id !== businessId || !row.id || !row.updated_at) throw new Error('Request scope or version is unavailable.');
        const updated = await request(
          `/rest/v1/${table}?business_id=eq.${encodeURIComponent(businessId)}&id=eq.${encodeURIComponent(row.id)}&updated_at=eq.${encodeURIComponent(row.updated_at)}&select=${columns}`,
          { method: 'PATCH', headers: { Prefer: 'return=representation' }, body: { is_trashed: removing } });
        if (Array.isArray(updated) && updated.length === 1 && updated[0].id === row.id && updated[0].is_trashed === removing) result.updated.push(updated[0]);
        else if (Array.isArray(updated) && !updated.length) result.conflicts.push(row);
        else throw new Error('The server did not confirm this change.');
      } catch (error) { result.failed.push({ row, error }); }
    }
  }
  await Promise.all(Array.from({ length: Math.min(rows.length, Math.max(1, Math.floor(concurrency) || 1)) }, worker));
  return result;
}
