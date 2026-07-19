# FreshLedger

FreshLedger turns a grocery receipt into an itemized spending ledger and a
freshness-aware food inventory. It grounds storage guidance in a curated
USDA/FoodKeeper reference table, then helps users act before groceries become
waste.

This OpenAI Build Week 2026 project was built in the resumable Codex session
`019f6ec9-4142-73d1-a8fe-db94c09af60b`. The submitted runtime defaults to a
zero-cloud Local Scan Beta, while the original GPT-5.6 Structured Outputs
adapter remains available as an explicit future integration.

## Three transparent modes

| Mode | What runs | Cloud/API cost |
|---|---|---:|
| **Demo** | One of three original synthetic receipt parses enters the real safety, review, SQLite, pantry, meal, waste, and insight pipeline. | **0 calls** |
| **Local OCR** | A real photo is read on the FastAPI host by RapidOCR/ONNX, reconstructed by deterministic receipt rules, matched against local product aliases, reconciled, reviewed, and persisted. | **0 calls** |
| **GPT · future** | The strict GPT-5.6 vision adapter is retained server-side. The submitted UI page is disabled and cannot trigger a call. | Disabled by default |

Every receipt response discloses `mode`, `provider`, `model`, and `ai_called`.
The Local OCR button calls `/v1/receipts/scan?engine=offline`, so it stays local
even if a server administrator configures the generic route for OpenAI.

## What works

- Receipt photo → OCR → spatial line reconstruction → item/price parsing.
- Exact local product grounding and conservative unknown-item abstention.
- Fridge/freezer/pantry advice from a 100+ row federal-reference table.
- Editable review: name, quantity, unit, price, category, storage location, and ledger-only exclusion.
- Atomic confirm to SQLite, including corrected line totals and reconciliation.
- Freshness-ranked pantry, deterministic rescue meals, and consumption actions.
- Exact waste-cost tracking and history-derived purchase advice.
- Merchant-scoped learning from explicitly confirmed name corrections only.
- Guarded `Reset all data` control that atomically clears mutable receipts,
  pantry, waste, caches, and learned corrections after explicit confirmation.
  The empty state survives server restarts; the judge dataset returns only when
  it is explicitly loaded again.

## Quick start — no API key

Requirements: Python 3.12 and Node.js. From PowerShell in the repository root:

```powershell
py -3.12 -m venv server\.venv
server\.venv\Scripts\python.exe -m pip install -r server\requirements.txt
npm ci --prefix app
powershell -ExecutionPolicy Bypass -File .\scripts\setup-local-env.ps1 -ApiUrl http://127.0.0.1:8000
```

`server/.env` should contain `RECEIPT_SCAN_ENGINE=offline`; `OPENAI_API_KEY` may
remain empty. Both `.env` files and local SQLite databases are ignored by Git.

Start the API:

```powershell
cd server
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In a second window, start Expo:

```powershell
cd app
npx expo start --web
```

Open `http://127.0.0.1:8081` and choose:

- **Demo** for the repeatable judge flow; or
- **Local OCR** to upload a real receipt image with zero cloud calls.

For Expo Go on a phone, rerun `setup-local-env.ps1` with the computer's LAN URL,
for example `-ApiUrl http://10.0.0.60:8000`, and restart Expo. The phone and
computer must be on the same network.

## Local recognition pipeline

```text
photo
  → file-signature/pixel validation + EXIF normalization
  → RapidOCR PP-OCRv6-small via ONNX Runtime (CPU, on server)
  → spatial token grouping and deterministic receipt parsing
  → exact shelf-life aliases / constrained review-only fuzzy hints
  → cents-based reconciliation
  → editable review and explicit confirmation
  → conservative shelf-life resolver
  → SQLite pantry, meals, waste, and insights
```

The OCR model is initialized once and inference runs off the async event loop.
Cache keys include provider, model, parser/matcher version, and image SHA-256.
Confirmed receipts are never reissued as new drafts.

Local Scan Beta currently targets English grocery receipts. Unknown lines are
marked for review and treated as perishable food with a conservative three-day
fridge default unless the user excludes or safely corrects them. It does not
claim arbitrary-receipt accuracy beyond the committed synthetic benchmark.

## Food-safety rules

- Only strict alias equality may unlock a canonical key and reference duration.
- Fuzzy matches never unlock item-specific shelf life and always require review.
- Prepared foods cannot inherit dry rice/pasta or other substring-based advice.
- Unmatched name corrections reset the old identity to unknown/perishable.
- Unknown food defaults to three refrigerated days.
- All durations remain within 1–730 days; ungrounded model/default guidance also obeys conservative category ceilings.
- `eat_by_window.end_days` never exceeds the resolved storage duration.
- Non-food and excluded lines cannot carry storage or eat-by guidance.
- Storage source is `reference`, `llm_clamped`, `default`, or `null` when excluded.

See [`server/data/README.md`](server/data/README.md) for primary government
sources and interpretation notes. FreshLedger is a planning aid; package dates,
temperature history, official cooking guidance, and spoilage signs take priority.

## Fixture benchmark

The three receipts under `fixtures/receipts/` are original project assets with
fictional stores and no personal or payment data. Run:

```powershell
server\.venv\Scripts\python.exe scripts\benchmark_local_ocr.py
```

The gate requires, for every fixture:

- exact item count with no extra lines;
- exact canonical identity, category, quantity, and line price;
- zero unreviewed uncertainty;
- exact subtotal reconciliation; and
- reference-grounded storage for every food item, with safe window invariants.

Verified on 2026-07-18: r1/r2/r3 parsed 5/7/8 items respectively; all identity,
category, quantity, and price checks passed; all reconciliations were `ok`; all
19 food items were reference-grounded; the one non-food line was safely excluded.
Cold OCR was about 2.7 seconds and warm runs about 1.3–1.5 seconds on this host.

## Verification

```powershell
cd server
.\.venv\Scripts\python.exe -m pytest -q

cd ..\app
npm test
npx tsc --noEmit
npx expo-doctor
npx expo export --platform web
```

Current result: **139 server tests passed**, **6 app tests passed**, Expo
TypeScript passed, Expo Doctor passed **20/20**, and the SDK 57 Web production
export bundled 531 modules.
A browser end-to-end test also completed Local OCR upload → 5-item review →
confirm → pantry, while `ai_call_usage` remained zero.

## Optional GPT-5.6 adapter

The future server integration still uses the OpenAI Responses API with the
strict prompt/schema defined by the project specification. To exercise it in a
private development environment, set the ignored `server/.env` explicitly:

```dotenv
RECEIPT_SCAN_ENGINE=openai
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-5.6
OPENAI_DAILY_CALL_LIMIT=1
```

The generic `/v1/receipts/scan` route then uses OpenAI. The visible Local OCR
mode still forces `engine=offline`, and the submitted **GPT · future** screen has
no enabled action. No OpenAI key is needed for the delivered demo.

## Repository layout

- `app/` — Expo SDK 57 + React Native Paper client.
- `server/` — FastAPI, local/OpenAI recognizers, safety resolver, SQLite, tests.
- `server/data/` — shelf-life CSV and source provenance.
- `fixtures/receipts/` — synthetic images and hand-maintained expectations.
- `scripts/` — environment setup, fixture generation, and OCR benchmark.
- `docs/` — test evidence, Codex session evidence, and specification deviations.

## Evidence and secrets

The `/feedback` procedure and implementation work log are maintained in
[`docs/codex_sessions.md`](docs/codex_sessions.md). Test evidence is in
[`docs/test_log.md`](docs/test_log.md).

Never commit `server/.env`, `app/.env`, API keys, demo/admin tokens, or local
SQLite databases.
