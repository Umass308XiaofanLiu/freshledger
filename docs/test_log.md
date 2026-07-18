# Test log

All timestamps are US Eastern Time.

| Date and time | Scope | Result | Notes |
|---|---|---|---|
| 2026-07-17 02:53 EDT | Server unit/contract tests | PASS | 6 tests: health, bearer auth, multipart draft response, image handling, and exactly one strict GPT-5.6 image call. |
| 2026-07-17 02:58 EDT | Expo TypeScript | PASS | `npx tsc --noEmit`. |
| 2026-07-17 02:59 EDT | Expo web export | PASS | SDK 57 Metro bundle exported successfully to ignored `app/dist/`. |
| 2026-07-17 03:01 EDT | Local FastAPI smoke | PARTIAL | `/health` returned 200; authenticated multipart upload reached `OPENAI_NOT_CONFIGURED`, confirming the expected missing `server/.env` blocker. |
| 2026-07-17 03:02 EDT | Expo Doctor | PASS | 20/20 project checks passed. |
| 2026-07-17 03:12 EDT | Expo browser visual smoke | PASS | Initial receipt-scan screen rendered in the Codex in-app browser. The check exposed a missing Paper icon dependency; `@expo/vector-icons` and its required `expo-font` peer were added, and the photo icon then rendered correctly. |
| 2026-07-17 03:14 EDT | D0 regression gate | PASS | Server: 6 tests passed. App: TypeScript passed, SDK 57 web export passed, and Expo Doctor passed 20/20. Browser logs contain only upstream React Native Web style deprecation warnings. |
| 2026-07-17 21:39 EDT | Food-safety interim clamp | PASS | 17 server tests passed, including all nine category ceilings, the `[1, 730]` bound, the conservative unknown-category fallback, and the legal `llm_clamped` source value. Python compileall and `git diff --check` also passed. |
