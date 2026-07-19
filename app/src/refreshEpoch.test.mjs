import assert from 'node:assert/strict';
import test from 'node:test';

import {
  beginEpoch,
  invalidateEpoch,
  isCurrentEpoch,
} from './refreshEpoch.ts';

test('invalidating an epoch prevents an older refresh from writing', () => {
  const epoch = { current: 0 };
  const staleRefresh = beginEpoch(epoch);

  invalidateEpoch(epoch);

  assert.equal(isCurrentEpoch(epoch, staleRefresh), false);
  const postResetRefresh = beginEpoch(epoch);
  assert.equal(isCurrentEpoch(epoch, postResetRefresh), true);
  assert.equal(isCurrentEpoch(epoch, staleRefresh), false);
});
