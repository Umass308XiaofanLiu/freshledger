export type StorageMethod = 'fridge' | 'freezer' | 'pantry';
export type ShelfLifeSource = 'reference' | 'llm_clamped' | 'default';

export interface StoragePlan {
  method: StorageMethod;
  temp_c: number;
  duration_days: number;
}

export interface StorageOptions {
  fridge_days: number | null;
  freezer_days: number | null;
  pantry_days: number | null;
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
  storage_options: StorageOptions | null;
  eat_by_window: { start_days: number; end_days: number } | null;
  shelf_life_source: ShelfLifeSource | null;
  confidence: number;
  needs_review: boolean;
}

export interface ScanProvenance {
  mode: 'demo' | 'live';
  ai_called: boolean;
  provider: 'openai' | null;
  model: string | null;
  fixture_id: string | null;
}

export interface ReceiptDraft {
  receipt_id: number;
  status: 'draft';
  scan_provenance: ScanProvenance;
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

export interface ConfirmReceiptResponse {
  receipt_id: number;
  status: 'confirmed';
  pantry_items_created: number;
  ledger_total: number;
  expiring_soon: Array<{ pantry_item_id: number; name: string; days_left: number }>;
}

export type Freshness = 'expired' | 'urgent' | 'soon' | 'fresh';

export interface PantryItem {
  pantry_item_id: number;
  name: string;
  canonical_key: string | null;
  category: string;
  qty_initial: number;
  qty_remaining: number;
  unit: string;
  unit_price: number;
  storage: StoragePlan;
  purchased_at: string;
  best_by: string;
  safe_until: string;
  days_left: number;
  freshness: Freshness;
  freeze_rescue: { possible: boolean; freezer_days: number | null };
}

export interface PantryResponse {
  items: PantryItem[];
  counts: Record<Freshness, number>;
  value_in_stock: number;
}

export interface SpoilPantryResponse {
  status: 'active' | 'spoiled';
  qty_remaining: number;
  waste_event: {
    id: number;
    cost_lost: number;
    occurred_at: string;
  };
  waste_total_to_date: number;
}

export interface DemoGeneration {
  mode: 'demo';
  ai_called: false;
  method: 'deterministic';
}

export interface DemoSeedResponse {
  seeded: true;
  receipts: number;
  pantry_items: number;
  waste_events: number;
  generation: DemoGeneration;
}

export interface MealUse {
  pantry_item_id: number;
  name: string;
  days_left: number;
}

export interface MealSuggestion {
  name: string;
  label: 'Deterministic demo suggestion';
  uses: MealUse[];
  why_now: string;
  steps: string[];
  time_minutes: number;
  safety_note: string;
}

export interface MealsResponse {
  date: string;
  cached: boolean;
  generation: DemoGeneration;
  meals: MealSuggestion[];
}

export interface MealConsumeResponse {
  consumed: Array<{
    pantry_item_id: number;
    qty_remaining: number;
    status: 'active' | 'eaten';
  }>;
  generation: DemoGeneration;
}

export interface InsightsResponse {
  period: { from: string; to: string };
  totals: {
    spent: number;
    food_spent: number;
    wasted: number;
    waste_rate: number;
    receipt_count: number;
  };
  by_category: Array<{ category: string; spent: number }>;
  waste_events: Array<{ name: string; occurred_at: string; cost_lost: number }>;
  advice: Array<{
    kind: 'buy_less' | 'buy_more' | 'stop_buying' | 'well_bought';
    canonical_key: string | null;
    text: string;
    label: 'Deterministic demo advice';
  }>;
  generation: DemoGeneration;
}
