# Shelf-life reference data

`shelf_life.csv` is a deliberately small, conservative application subset of
the U.S. Department of Agriculture (USDA) FoodKeeper and federal cold-storage
guidance. It is not a verbatim redistribution of the full FoodKeeper database.
FreshLedger selects the lower end of a published range when a source gives a
range, caps storage durations in code, and treats freezer durations as quality
guidance rather than a claim that continuously frozen food becomes unsafe at
that date.

The row condition matters. For example, `opened_hot_dogs` means an opened
package, and raw meat rows are for food brought home and refrigerated promptly.
Package instructions and an earlier printed use-by date take priority. Users
should discard food showing spoilage or food that was not kept at a safe
temperature; the table cannot determine temperature-abuse history.

## Primary sources

- [USDA FSIS FoodKeeper dataset catalog](https://catalog.data.gov/dataset/fsis-foodkeeper-data)
  (Food Safety and Inspection Service, dataset updated 2025-01-22, CC0). This
  is the basis for common produce, dairy, bakery, beverage, and pantry rows.
- [FoodSafety.gov Cold Food Storage Chart](https://www.foodsafety.gov/food-safety-charts/cold-food-storage-charts)
  (U.S. Department of Health and Human Services, September 2023 review). This
  is the primary source for meat, poultry, seafood, deli, egg, leftover, and
  frozen-food refrigerator/freezer ranges. Refrigerator values use the lower
  end of a range where applicable.
- [USDA FSIS Refrigeration & Food Safety](https://www.fsis.usda.gov/food-safety/safe-food-handling-and-preparation/food-safety-basics/refrigeration)
  confirms a refrigerator target of 40 °F (4 °C) or below, raw poultry and
  ground meat limits of 1–2 days, fresh fish/shellfish limits of 1–2 days, and
  the general four-day maximum for cooked leftovers.
- [USDA FSIS Shelf-Stable Food Safety](https://www.fsis.usda.gov/food-safety/safe-food-handling-and-preparation/food-safety-basics/shelf-stable-food)
  is the basis for canned foods, dry rice, and dry pasta. FreshLedger shortens
  the published two-year dry-rice/pasta period to 365 days and the 2–5 year
  low-acid canned-food period to 365 days.
- [FDA Refrigerator & Freezer Storage Chart](https://www.fda.gov/media/74435/download)
  is used as a federal cross-check for the refrigerator/freezer ranges and the
  0 °F (-18 °C) freezer target.

## Field semantics

- `recommended_method` is the default storage location used by the resolver.
- `fridge_days`, `freezer_days`, and `pantry_days` are whole-day conservative
  maxima for the row's named condition; blank means FreshLedger does not make
  that recommendation.
- `eat_by_start_days` and `best_by_days` define an advisory consumption window
  within the recommended duration. They are product-quality reminders, not a
  substitute for sensory checks, package directions, or safe handling.
- `aliases` is a JSON string array used only for deterministic name matching.
  A reference row is unlocked only by normalized equality with one of these
  explicit phrases (after a small packaging/modifier whitelist); arbitrary
  substring matches are intentionally rejected.

RapidOCR spelling hints that do not exactly match this table may suggest a
category for review, but cannot unlock a canonical key or row-specific duration.
Merchant-scoped aliases are learned only from an explicit confirmed correction,
and their category/perishability are reloaded from the current reference row.

The file contains just over 100 data rows. Any value change should cite a current primary
government source and preserve the invariant that the recommended method has a
duration and the advisory window does not exceed it.
