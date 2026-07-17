export type StorageMethod = 'fridge' | 'freezer' | 'pantry';

export interface StoragePlan {
  method: StorageMethod;
  temp_c: number;
  duration_days: number;
}

export interface ReceiptItem {
  item_id: number;
  raw_text: string;
  name: string;
  canonical_key: string | null;
  qty: number;
  unit: string;
  unit_price: number;
  line_total: number;
  category: string;
  is_perishable: boolean;
  excluded: boolean;
  storage: StoragePlan | null;
  eat_by_window: { start_days: number; end_days: number } | null;
  confidence: number;
  needs_review: boolean;
}

export interface ReceiptDraft {
  receipt_id: number;
  status: 'draft';
  store_name: string | null;
  purchased_at: string | null;
  overall_confidence: number;
  reconciliation: {
    printed_subtotal: number | null;
    printed_tax: number | null;
    printed_total: number | null;
    computed_items_sum: number;
    status: 'ok' | 'mismatch' | 'unreadable';
    delta: number | null;
  };
  items: ReceiptItem[];
}

