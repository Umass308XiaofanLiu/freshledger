# Specification deviations

Log deliberate departures from the canonical FreshLedger specifications here. The five publicly promised features and the food-safety conservatism rules are not eligible for deviation.

- 2026-07-17 — The implementation task runs in Codex Desktop (`gpt-5.6-sol`, agent core `0.145.0-alpha.18`) rather than the handoff's assumed standalone CLI 0.114.0; the standalone CLI installed on the machine is 0.116.0. Session evidence records both accurately.
- 2026-07-17 — Interim D0-to-D1 grounding state: until the full shelf-life CSV resolver replaces it, scan responses clamp LLM storage durations to `[1, 730]` and the specification's category maxima, use a conservative 3-day ceiling for unknown/unmapped categories, and emit the legal `llm_clamped` source; `storage_options` remains pending the D1 resolver.
