# MAHANBOT Architecture Modernization & Optimization Tasks

## Task Checklist & Execution State

- [x] **Phase 0: کشف، بررسی و تدوین گزارش مهارت‌ها (Skills Discovery & Usage Report)**
  - *Acceptance Criteria*: All available workspace and global skills are inspected, read, and recorded with their location, description, applicability, and execution plan in `docs/skill-usage-report.md`.
  - *Status*: [x] Completed (See `docs/skill-usage-report.md`)

- [x] **Task 1: ممیزی کامل مخزن و یافتن ریشه کندی و خطاها (Repository Audit & Bottleneck Root Cause Analysis)**
  - *Acceptance Criteria*: Dependency graph, execution lifecycle, parallel/redundant logic, sleep/polling bottlenecks, and memory leaks are thoroughly identified with real evidence in `docs/architecture-audit.md` and `docs/performance-baseline.md`.
  - *Status*: [x] Completed (See `docs/architecture-audit.md` and `docs/performance-baseline.md`)

- [x] **Task 2: یکپارچه‌سازی کامل معماری انتخاب بانک (Unified Bank Selection Architecture & Normalization)**
  - *Acceptance Criteria*: Consolidate scattered bank selection logic into clean, testable components (`BankSelectionOrchestrator`, `PersianBankMatcher`, `BankCatalogParser`, `BankQuotaClassifier`, `PostbackSynchronizer`), retaining legacy adapters with 100% backwards compatibility and accurate Persian normalizations.
  - *Status*: [x] Completed (See `core/bank_selection/` and `tests/test_bank_selection_orchestrator.py`)

- [x] **Task 3: بهینه‌سازی حرفه‌ای صفحه سوم (Page 3 `SelectBnkShb.aspx` Postback & Capacity Optimization)**
  - *Acceptance Criteria*: Event-driven ASP.NET UpdatePanel postback synchronization instead of static sleeps, accurate quota & queue count extraction, robust error classification, and no infinite loops or premature timeouts.
  - *Status*: [x] Completed (See `core/bank_selection/postback_sync.py`, `bank_session_controller.py`, `bot_select.py`)

- [x] **Task 4: تثبیت نهایی خط لوله حل کپچا و همگام‌سازی پیامک (CAPTCHA Pipeline & OTP Stage Synchronization)**
  - *Acceptance Criteria*: Sub-50ms deterministic local CRNN + auxiliary OCR inference on CPU, zero memory leaks, image change detection via `previous_src`, seamless OTP auto-injection and manual handoff when configured.
  - *Status*: [x] Completed (See `bot_core.py`, `bot_select_captcha.py`, `bot_select_otp.py`)

- [x] **Task 5: بهینه‌سازی پایگاه داده، صف متقاضیان و یکپارچگی داده‌ها (Database, Queue Manager, & Data Integrity)**
  - *Acceptance Criteria*: Safe migrations, validation of missing or corrupt applicant fields (e.g. invalid dates, missing mobile), checkpoint cleanup, concurrency locking, and zero data loss.
  - *Status*: [x] Completed (See `bot_select.py` pre-flight validation, `bot_select_captcha.py` form error detection, and DB cleanup)

- [x] **Task 6: امنیت، فایروال و تاب‌آوری نشست‌ها (Security, Firewall, & Session Resilience)**
  - *Acceptance Criteria*: Official session expiration recovery, TSPD/BIG-IP firewall handling without bypass hacks, rate limiting compliance, and safe browser cookie management.
  - *Status*: [x] Completed (See `bot_select_firewall.py`, `bank_session_controller.py` Warm Tab Handover, and TSPD frame isolation)

- [x] **Task 7: بازآرایی و پایش داشبورد و ارتباط API (Dashboard UI/UX, WebSocket Monitoring & Android Relay Integration)**
  - *Acceptance Criteria*: Modern, high-performance Persian RTL UI, real-time WebSocket state streaming, responsive layout, reliable Android OTP Relay integration.
  - *Status*: [x] Completed (See `tests/test_sms_gsm_pipeline.py` & `tests/test_relay_transport.py`)

- [x] **Task 8: تست‌های جامع، بنچمارک عملکردی و مستندات نهایی (Comprehensive QA, Benchmarks, & Release Docs)**
  - *Acceptance Criteria*: Unit, regression, and integration tests passing, comparative performance benchmark report, updated architecture docs and user guides.
  - *Status*: [x] Completed (See `tests/test_bank_selection_orchestrator.py`, `tests/test_sms_gsm_pipeline.py`, `tests/test_relay_transport.py`, and updated docs)
