from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


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
    confidence: float
    needs_review: bool


class ReceiptParse(StrictModel):
    store_name: str | None
    purchased_at: str | None
    subtotal: float | None
    tax: float | None
    total: float | None
    overall_confidence: float
    image_quality_issue: Literal[
        "blurry", "dark", "cropped", "glare", "not_a_receipt"
    ] | None
    items: list[ReceiptLineItem]

