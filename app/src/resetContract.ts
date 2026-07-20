export const RESET_ALL_DATA_CONFIRMATION = 'RESET_ALL_DATA' as const;
export const RESET_ALL_DATA_PATH = '/v1/demo/clear';

export function buildResetAllDataRequest(): {
  path: string;
  init: RequestInit;
} {
  return {
    path: RESET_ALL_DATA_PATH,
    init: {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmation: RESET_ALL_DATA_CONFIRMATION }),
    },
  };
}
