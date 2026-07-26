# MahanBot — Phase 0 Audit

Date: 2026-07-17

## Scope

This audit covers the current Python/FastAPI/Playwright code paths for:

- registration flow
- OTP ingestion and handoff
- bank and branch selection
- browser startup and navigation
- captcha service integration
- SQLite persistence
- dashboard events and logs

The SMSForwardManager integration is intentionally paused until this cleanup branch is validated.

## Completed in this branch

### 1. Shared form interaction layer

`browser_actions.py` was rewritten as a single, reusable interaction layer.

Changes:

- parameterized JavaScript instead of interpolating user data into scripts
- visible/enabled/editable checks before changing controls
- focus, select-all, clear, controlled typing and value verification
- deterministic timing intended for UI stability
- verified select and click helpers
- backward-compatible `force_fill` API
- no hidden/read-only control mutation
- no fingerprint spoofing, mouse-path simulation or anti-detection behavior

### 2. Sensitive-data redaction

`event_logger.py` now sanitizes logs and WebSocket events before persistence or broadcast.

Protected values include:

- OTP values
- passwords
- tokens
- API keys
- authorization data
- cookies and session secrets

### 3. Quality gate

Added:

- `tests/test_browser_actions.py`
- `scripts/quality_check.py`
- `.github/workflows/quality.yml`

The quality gate performs syntax compilation of the core modules and unit tests for the interaction layer.

## Critical findings still pending

### P0 — security and correctness

1. `server.py` allows API access when `MAHANBOT_API_KEY` is missing.
2. CORS is configured with `allow_origins=["*"]`.
3. OTP is stored in plaintext inside the applicant JSON document.
4. captcha predictions are stored in plaintext for up to 200 attempts.
5. `/wait_otp/{nid}` clears its event around the wait operation, which can race with a newly received OTP.
6. `BankSelectionBot.fetch_otp_fast()` ignores the freshness timestamp and can consume stale OTP data.
7. OTP and captcha codes are also written by direct `print()` or bot console logging outside the central event logger.

### P0 — anti-bot control boundary

1. `browser_launcher.py` adds `--disable-blink-features=AutomationControlled`.
2. the firewall state is recorded as manual intervention while another path tries to solve it automatically.
3. CAPTCHA logic is duplicated between registration and selection flows.

Target behavior:

- normal form interaction remains automated and testable
- the user's existing captcha model is treated as an external code provider
- WAF/firewall challenges pause the job and require an explicit operator action
- no anti-detection flags or identity/security-control bypass logic

### P1 — maintainability

1. `bot_register.py` and `bot_select.py` contain duplicated sleep, selector, captcha and recovery logic.
2. broad exception handlers hide root causes and make diagnosis difficult.
3. `BotCore` contains a hardcoded remote API URL.
4. SQLite path selection can resolve beside the packaged executable and needs a migration-safe data directory.
5. default retry count is `1000`, which can create effectively endless jobs.
6. runtime status strings are inconsistent across Persian and English values.
7. `clear_otp()` does not remove `otp_ts_ms`.

## Planned phases

### Phase 1 — interaction migration

- migrate direct `.fill()`, `.type()`, `.select_option()` and `.click()` calls to `BrowserActions`
- remove duplicated `_fill_text` and select helpers
- add page-object selector constants

### Phase 2 — captcha boundary

- introduce a small `CaptchaProvider` interface
- connect the existing user model through that interface
- centralize result validation and attempt reporting
- make WAF/firewall handling operator-driven

### Phase 3 — OTP correctness

- enforce freshness timestamps
- fix event signaling race
- clear every OTP timestamp field
- prevent OTP reuse
- add end-to-end API/DB/waiter tests

### Phase 4 — server hardening

- fail closed when API key is missing outside development mode
- configurable CORS allowlist
- request validation and rate limiting
- masked structured logs

### Phase 5 — database migration

- versioned schema migration
- writable application-data directory
- retention policy for attempts and screenshots
- backup and rollback script

## Rollback

No changes in this branch are merged into `main` yet.

Rollback is therefore:

1. close the pull request without merging, or
2. delete branch `refactor/phase-0-cleanup`.
