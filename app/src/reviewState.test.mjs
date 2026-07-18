import assert from 'node:assert/strict';
import test from 'node:test';

import { applyIdentitySafety } from './reviewState.ts';

const groundedItem = {
  item_id: 1,
  raw_text: 'RICE 3.99',
  name: 'Rice',
  canonical_key: 'white_rice',
  qty: 1,
  unit: 'each',
  unit_price: 3.99,
  line_total: 3.99,
  category: 'pantry_staple',
  is_perishable: false,
  excluded: false,
  storage: { method: 'pantry', temp_c: 20, duration_days: 365 },
  storage_options: { fridge_days: null, freezer_days: 365, pantry_days: 365 },
  eat_by_window: { start_days: 270, end_days: 365 },
  shelf_life_source: 'reference',
  confidence: 0.98,
  needs_review: false,
};

for (const updates of [{ name: 'Rice pudding' }, { category: 'dairy' }]) {
  test(`identity edit ${JSON.stringify(updates)} hides stale advice`, () => {
    const updated = applyIdentitySafety(groundedItem, updates);
    assert.equal(updated.canonical_key, null);
    assert.equal(updated.storage, null);
    assert.equal(updated.storage_options, null);
    assert.equal(updated.eat_by_window, null);
    assert.equal(updated.shelf_life_source, null);
    assert.equal(updated.needs_review, true);
  });
}

test('an unchanged identity preserves grounded advice', () => {
  const updated = applyIdentitySafety(groundedItem, { name: groundedItem.name });
  assert.deepEqual(updated.storage, groundedItem.storage);
  assert.equal(updated.shelf_life_source, 'reference');
});
