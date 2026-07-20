import assert from 'node:assert/strict';
import test from 'node:test';

import { finishReset, tryBeginReset } from './resetGate.ts';

test('only one reset confirmation can acquire the request gate', () => {
  const gate = { current: false };

  assert.equal(tryBeginReset(gate), true);
  assert.equal(tryBeginReset(gate), false);
  finishReset(gate);
  assert.equal(tryBeginReset(gate), true);
});
