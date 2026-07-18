from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoragePlan(StrictModel):
    method: Literal["fridge", "freezer", "pantry"]
    temp_c: float
    duration_days: int


class EatByWindow(StrictModel):
    start_days: int
    end_days: int


class ReceiptLineItem(StrictModel):
    raw_text: str
    name: str
    canonical_key: str | None
    qty: float
    unit: Literal[
        "each",
        "lb",
        "oz",
        "kg",
        "g",
        "gallon",
        "liter",
        "pack",
        "bunch",
        "dozen",
    ]
    unit_price: float
    line_total: float
    category: Literal[
        "produce",
        "dairy",
        "meat",
        "seafood",
        "bakery",
        "frozen",
        "deli",
        "beverage",
        "pantry_staple",
        "non_food",
        "unknown",
    ]
    is_perishable: bool
    storage: StoragePlan | None
    eat_by_window: EatByWindow | None
    confidence: float = Field(ge=0, le=1)
    needs_review: bool


class ReceiptParse(StrictModel):
    store_name: str | None
    purchased_at: str | None
    subtotal: float | None
    tax: float | None
    total: float | None
    overall_confidence: float = Field(ge=0, le=1)
    image_quality_issue: Literal[
        "blurry", "dark", "cropped", "glare", "not_a_receipt"
    ] | None
    items: list[ReceiptLineItem]


class StorageOptions(StrictModel):
    fridge_days: int | None
    freezer_days: int | None
    pantry_days: int | None


class ReceiptDraftItem(ReceiptLineItem):
    item_id: int
    excluded: bool
    storage_options: StorageOptions | None
    shelf_life_source: Literal["reference", "llm_clamped", "default"] | None


class Reconciliation(StrictModel):
    printed_subtotal: float | None
    printed_tax: float | None
    printed_total: float | None
    computed_items_sum: float
    status: Literal["ok", "mismatch", "unreadable"]
    delta: float | None


class ScanProvenance(StrictModel):
    mode: Literal["demo", "live"]
    ai_called: bool
    provider: Literal["openai", "rapidocr"] | None
    model: str | None
    fixture_id: str | None


class ReceiptDraft(StrictModel):
    receipt_id: int
    status: Literal["draft"] = "draft"
    scan_provenance: ScanProvenance
    store_name: str | None
    purchased_at: str | None
    overall_confidence: float = Field(ge=0, le=1)
    reconciliation: Reconciliation
    items: list[ReceiptDraftItem]


class ConfirmReceiptItem(StrictModel):
    item_id: int
    name: str | None = None
    qty: float | None = Field(default=None, gt=0, le=100)
    unit: Literal[
        "each",
        "lb",
        "oz",
        "kg",
        "g",
        "gallon",
        "liter",
        "pack",
        "bunch",
        "dozen",
    ] | None = None
    unit_price: float | None = Field(default=None, ge=-500, le=500)
    category: Literal[
        "produce",
        "dairy",
        "meat",
        "seafood",
        "bakery",
        "frozen",
        "deli",
        "beverage",
        "pantry_staple",
        "non_food",
        "unknown",
    ] | None = None
    excluded: bool | None = None
    storage_method_override: Literal["fridge", "freezer", "pantry"] | None = None


class ConfirmReceiptRequest(StrictModel):
    store_name: str | None = None
    purchased_at: str | None = None
    items: list[ConfirmReceiptItem]


class ConfirmReceiptResponse(StrictModel):
    receipt_id: int
    status: Literal["confirmed"] = "confirmed"
    pantry_items_created: int
    ledger_total: float
    expiring_soon: list[dict[str, object]]


class PantryStorage(StrictModel):
    method: Literal["fridge", "freezer", "pantry"]
    temp_c: float | None
    duration_days: int


class FreezeRescue(StrictModel):
    possible: bool
    freezer_days: int | None


class PantryItemResponse(StrictModel):
    pantry_item_id: int
    name: str
    canonical_key: str | None
    category: str
    qty_initial: float
    qty_remaining: float
    unit: str
    unit_price: float
    storage: PantryStorage
    purchased_at: str
    best_by: str
    safe_until: str
    days_left: int
    freshness: Literal["expired", "urgent", "soon", "fresh"]
    freeze_rescue: FreezeRescue


class PantryResponse(StrictModel):
    items: list[PantryItemResponse]
    counts: dict[str, int]
    value_in_stock: float


class PortionRequest(StrictModel):
    portion: float = Field(default=1.0, gt=0, le=1)


class DemoGeneration(StrictModel):
    mode: Literal["demo"] = "demo"
    ai_called: Literal[False] = False
    method: Literal["deterministic"] = "deterministic"


class MealUse(StrictModel):
    pantry_item_id: int
    name: str
    days_left: int


class MealSuggestion(StrictModel):
    name: str
    label: Literal["Deterministic demo suggestion"] = (
        "Deterministic demo suggestion"
    )
    uses: list[MealUse]
    why_now: str
    steps: list[str] = Field(max_length=3)
    time_minutes: int = Field(ge=1, le=40)
    safety_note: Literal[
        "Time is an estimate; package directions, tracked dates, and safe internal temperature take priority."
    ] = (
        "Time is an estimate; package directions, tracked dates, and safe internal temperature take priority."
    )


class MealsTodayResponse(StrictModel):
    date: str
    cached: bool
    generation: DemoGeneration
    meals: list[MealSuggestion] = Field(max_length=3)


class MealConsumeItem(StrictModel):
    pantry_item_id: int = Field(gt=0)
    portion: float = Field(default=1.0, gt=0, le=1)


class MealConsumeRequest(StrictModel):
    items: list[MealConsumeItem] = Field(min_length=1, max_length=25)


class MealConsumeResult(StrictModel):
    pantry_item_id: int
    qty_remaining: float = Field(ge=0)
    status: Literal["active", "eaten"]


class MealConsumeResponse(StrictModel):
    consumed: list[MealConsumeResult]
    generation: DemoGeneration


class DemoSeedRequest(StrictModel):
    profile: Literal["judge"] = "judge"


class DemoSeedResponse(StrictModel):
    seeded: Literal[True] = True
    receipts: int = Field(ge=0)
    pantry_items: int = Field(ge=0)
    waste_events: int = Field(ge=0)
    generation: DemoGeneration


class InsightsPeriod(StrictModel):
    from_date: str = Field(alias="from")
    to: str


class InsightTotals(StrictModel):
    spent: float
    food_spent: float
    wasted: float
    waste_rate: float = Field(ge=0)
    receipt_count: int = Field(ge=0)


class CategorySpend(StrictModel):
    category: str
    spent: float


class WasteEventInsight(StrictModel):
    name: str
    occurred_at: str
    cost_lost: float


class BuyingAdvice(StrictModel):
    kind: Literal["buy_less", "buy_more", "stop_buying", "well_bought"]
    canonical_key: str
    text: str
    label: Literal["Deterministic demo advice"] = "Deterministic demo advice"


class InsightsResponse(StrictModel):
    period: InsightsPeriod
    totals: InsightTotals
    by_category: list[CategorySpend]
    waste_events: list[WasteEventInsight]
    advice: list[BuyingAdvice]
    generation: DemoGeneration
