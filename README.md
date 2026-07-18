# FreshLedger

FreshLedger turns a grocery receipt into an itemized spending ledger and a
freshness-aware food inventory. It grounds storage guidance in a curated
USDA/FoodKeeper reference table, then helps the user act before groceries become
waste.

This is an OpenAI Build Week 2026 project built in Codex. The repository is in
active hackathon development. The zero-token demo now exposes all five promised
product loops: receipt-to-ledger, grounded storage science, freshness-aware meal
pairings, exact waste-cost tracking, and history-derived purchase advice.

## Two honest scan modes

| Mode | What happens | OpenAI cost |
|---|---|---:|
| **Demo — free** | Loads one of three original synthetic receipt parses, then runs it through the real reconciliation, shelf-life, review, SQLite, and pantry pipeline. The UI permanently labels the result `Sample data · no AI call`. | **0 tokens** |
| **Live Scan — GPT-5.6** | Uploads a camera/gallery image to FastAPI and makes one OpenAI Responses API vision call with strict Structured Outputs before the same server pipeline. It never falls back silently to sample data. | Uses API credits |

Demo Mode is for deterministic judging, development, and users who do not want
to spend API credits. It does not pretend to be evidence of a live GPT-5.6 call.
The Live route remains the project's meaningful GPT-5.6 integration and still
needs one controlled real-call recording before final hackathon submission.

## Zero-token quick start

Requirements: Python 3.12 and Node.js. From PowerShell in the repository root:

```powershell
py -3.12 -m venv server\.venv
server\.venv\Scripts\python.exe -m pip install -r server\requirements.txt
npm ci --prefix app
powershell -ExecutionPolicy Bypass -File .\scripts\setup-local-env.ps1 -ApiUrl http://127.0.0.1:8000
```

Start the API:

```powershell
cd server
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In a second PowerShell window, start Expo Web:

```powershell
cd app
npx expo start --web
```

Open `http://127.0.0.1:8081`, leave **Demo · free** selected, and choose
**Run sample through FreshLedger**. Review the location chips, confirm the
receipt, and use **Ate it** or **Tossed it** in the fridge section.
The same confirmed receipt also drives three rescue-meal cards, the atomic
**I made this** loop, spending/category tiles, waste history, and deterministic
buy-smarter advice.

For an instant judge-ready dashboard, choose **Open full judge demo**. It
idempotently reloads all three bundled fixtures through the same resolver,
persistence, and confirmation pipeline, then pre-warms the zero-token meal
cache. An empty server database performs this seed automatically at startup.

To reset a local filming/demo take without exposing either token:

```powershell
$freshEnv = Get-Content .\server\.env -Raw | ConvertFrom-StringData
Invoke-RestMethod -Method Post http://127.0.0.1:8000/v1/demo/reset `
  -Headers @{ Authorization = "Bearer $($freshEnv.DEMO_TOKEN)"; "X-Admin-Token" = $freshEnv.ADMIN_TOKEN }
```

For Expo Go on a phone, rerun `setup-local-env.ps1` with the computer's LAN URL,
for example `-ApiUrl http://10.0.0.60:8000`, then restart Expo. The phone and
computer must be on the same network.

## Optional Live Scan

Add an OpenAI API key only to the ignored `server/.env` file:

```dotenv
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-5.6
```

Restart FastAPI, select **Live · GPT-5.6**, and take or choose a receipt photo.
The key never enters the Expo bundle. Image uploads are capped at 8 MB,
validated by file signature and pixel count, EXIF-normalized, and downscaled
before the API call.

The server has both per-IP limits and an atomic global UTC-day OpenAI circuit
breaker (`OPENAI_DAILY_CALL_LIMIT`, conservative default: 3). The client demo
token is still a crawler deterrent, not a private credential. A public zero-cost
Demo deployment should leave `OPENAI_API_KEY` empty; for a controlled Live
recording, set the breaker to only 1–3 calls.

## Architecture

```text
Expo SDK 57 (native + web)
   ├─ Demo: POST /v1/demo/receipts/{r1|r2|r3}/scan ─┐
   └─ Live: photo → POST /v1/receipts/scan → GPT-5.6│
                                                     ▼
FastAPI → strict Pydantic contract → reconciliation
        → 100-row shelf-life resolver + safety clamps
        → SQLite draft → review/confirm → pantry + waste events
        → deterministic Demo meals + exact SQL insights/advice
```

Both paths share the same response model and server-side post-processing. Live
images are idempotent by SHA-256 so a retry can return the stored parse without
another model call. Demo scans deliberately create fresh drafts so the review
flow can be repeated.

## Food-safety approach

- Exact canonical-key matching, then longest-alias matching, routes foods into
  a 100-row static reference table.
- Reference values use conservative federal guidance; ranges use the lower end.
- Long-tail model durations are bounded to 1–730 days and category ceilings.
- `eat_by_window.end_days` can never exceed the resolved storage duration.
- Non-food, excluded, or storage-less items cannot leak contradictory storage or
  eat-by advice.
- Storage source is always one of `reference`, `llm_clamped`, or `default`.

See [`server/data/README.md`](server/data/README.md) for USDA/FoodKeeper,
FoodSafety.gov, USDA FSIS, and FDA source links and interpretation notes.
FreshLedger is a planning aid, not a substitute for package directions,
temperature-history knowledge, or normal spoilage checks.

## Synthetic receipt fixtures

The three sample receipts are original project assets generated deterministically
by `scripts/generate_demo_receipts.py`. They use fictional stores and addresses,
contain no customer/payment information or third-party trademarks, and visibly
say `SAMPLE / NOT A REAL PURCHASE`.

- `fixtures/receipts/r1.jpg` — everyday perishables and weighted items.
- `fixtures/receipts/r2.jpg` — fridge/freezer/pantry coverage plus a non-food item.
- `fixtures/receipts/r3.jpg` — longer mixed-category grocery receipt.
- `fixtures/receipts/expected/*.json` — strict model-boundary parses.
- `app/assets/samples/*.jpg` — byte-identical Expo-bundled copies.

## Verification

```powershell
cd server
.\.venv\Scripts\python.exe -m pytest -q

cd ..\app
npx tsc --noEmit
npx expo export --platform web
npx expo-doctor
```

Tests cover strict Live call parameters, image hardening, error envelopes,
food-safety clamps, 100-row reference resolution, fixture integrity, real SQLite
IDs and integer cents, zero-token Demo provenance, review/confirm, pantry reads,
consumption, waste-cost events, deterministic meal/advice provenance, and
atomic meal consumption.
Current manual evidence is recorded in `docs/test_log.md`; Live GPT-5.6
end-to-end remains explicitly blocked until a key is supplied.

## Repository layout

- `app/` — Expo React Native + React Native Paper client.
- `server/` — Python FastAPI API, OpenAI vision integration, SQLite, and tests.
- `server/data/` — curated shelf-life reference data and provenance.
- `fixtures/receipts/` — generated receipt images and expected parses.
- `scripts/` — repeatable local environment and fixture generation.
- `docs/` — Codex session evidence, test logs, and specification deviations.

## Codex and submission evidence

The main resumable Codex task ID is
`019f6ec9-4142-73d1-a8fe-db94c09af60b`. The exact `/feedback` procedure,
surface/version distinction, and work log are maintained in
`docs/codex_sessions.md`.

Secrets are ignored from the first commit. Never commit `server/.env`,
`app/.env`, API keys, demo tokens, admin tokens, or local SQLite databases.
