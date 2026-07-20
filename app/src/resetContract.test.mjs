import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildResetAllDataRequest,
  RESET_ALL_DATA_CONFIRMATION,
  RESET_ALL_DATA_PATH,
} from './resetContract.ts';

test('reset request uses the destructive endpoint and exact confirmation sentinel', () => {
  const { path, init } = buildResetAllDataRequest();

  assert.equal(path, RESET_ALL_DATA_PATH);
  assert.equal(path, '/v1/demo/clear');
  assert.equal(init.method, 'POST');
  assert.deepEqual(init.headers, { 'Content-Type': 'application/json' });
  assert.deepEqual(JSON.parse(String(init.body)), {
    confirmation: RESET_ALL_DATA_CONFIRMATION,
  });
});
