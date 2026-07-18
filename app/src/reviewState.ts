import type { ReceiptItem } from './types';

type IdentityUpdate = Partial<Pick<ReceiptItem, 'name' | 'category'>>;

/**
 * Apply a review edit and immediately withdraw advice grounded in the old
 * identity. The server will re-ground the corrected item on confirmation.
 */
export function applyIdentitySafety(
  item: ReceiptItem,
  updates: IdentityUpdate,
): ReceiptItem {
  const identityChanged =
    (updates.name !== undefined && updates.name !== item.name) ||
    (updates.category !== undefined && updates.category !== item.category);
  const updated = { ...item, ...updates };

  if (!identityChanged) return updated;
  return {
    ...updated,
    canonical_key: null,
    storage: null,
    storage_options: null,
    eat_by_window: null,
    shelf_life_source: null,
    needs_review: true,
  };
}
