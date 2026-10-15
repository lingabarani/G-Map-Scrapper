# Reconcile & Implementation Plan (`docs/RECONCILE_PLAN.md`)

This plan governs the execution from Stage 1 through Stage 12 in strict accordance with project requirements.

---

## Stage Breakdown & Execution Order

### Stage 1: Config, HTTP, Logging, DB
- **1.1 Config (`config.py`)**: Ensure all 16 configuration parameters are present in `ScraperConfig` with pydantic-settings, defaults in `config/default.yaml`, and optional `config/user.yaml` override.
- **1.2 HTTP Client (`http.py`)**: Ensure async client includes realistic browser headers (`User-Agent`, `Accept-Language`, `Cookie: CONSENT=YES+`), request counter instrumentation, semaphore rate limiting, and exponential backoff with jitter on 429/5xx.
- **1.3 Logging (`logging_setup.py`)**: Structured rotating file log (`logs/gmaps.log`) and Rich console logging.
- **1.4 Database (`db.py`)**: SQLite with WAL mode, foreign keys, 7 tables (`places`, `hours`, `attributes`, `reviews`, `photos`, `popular_times`, `run_state`), FTS5 virtual table, and `init` CLI command.
- **1.5 Fallback Browser (`fallback_browser.py`)**: Playwright fallback renderer for blocked/JS-only scenarios, caching browser in `.cache/ms-playwright/`.
- **Acceptance 1**: `python -m gmaps_scraper init` creates database with all 7 tables. `pytest -q` runs cleanly without import errors.

### Stage 2: Discovery & Tiling
- **2.1 Tiling (`tiling.py`)**: Recursive 4-quadrant subdivision when search result count == 20 (Google cap); terminates when < 20 or max depth reached. Deduplication by CID.
- **2.2 Search RPC (`search.py`)**: Construct search Protobuf parameters, parse stubs with clean unpolluted addresses (`p[39]`), cache raw responses under `data/raw/`.
- **2.3 State Scanner (`parser_state.py`)**: Balanced-bracket scanner for `APP_INITIALIZATION_STATE` as fallback.
- **Acceptance 2**: Real search in a small bounding box; verify subdivision logic, deduplication, and raw cache creation.

### Stage 3: Place Details Enrichment
- **3.1 Details (`details.py`)**: Extract hours (including special hours), attributes (accessibility, amenities), price level, claimed status, plus codes, secondary categories, popular times, and photo URLs.
- **3.2 Robustness**: Handle missing websites, missing phone numbers, and permanently closed entities without crashing.
- **3.3 Status transitions**: `pending` -> `details_done`, or `failed` with error and attempt counts.
- **Acceptance 3**: Enrich real places; dump a record with >20 populated fields.

### Stage 4: Reviews & Photos
- **4.1 Reviews (`reviews.py`)**: Pagination loop via continuation token extracted from `response[1][10][6]`. Extract author, rating, relative date, timestamp, text, reactions, and owner reply.
- **4.2 Photos (`photos.py`)**: Async downloader saving images to `data/photos/{cid}/{filename}` with validation of image magic bytes and metadata manifest.
- **4.3 Status transition**: `details_done` -> `reviews_done`.
- **Acceptance 4**: Fetch full reviews for a high-review place; download photos with valid magic bytes.

### Stage 5: Resilience & Checkpoint
- **5.1 Checkpoint (`checkpoint.py`)**: Track per-row status and attempts. `resume` command resumes from last incomplete state without refetching completed records.
- **5.2 Signal Handling**: Graceful SIGINT handler in CLI completing in-flight request, flushing DB, and printing resume command.

### Stage 6: Exports & CLI
- **6.1 Exporter (`export.py`)**: Output CSV, JSONL, and Parquet into `data/exports/`. Flat places export plus separate child exports. Clean CSV quoting for special characters, newlines, and quotes.
- **6.2 CLI (`cli.py`)**: Typer commands (`init`, `search`, `enrich`, `reviews`, `photos`, `export`, `run`, `resume`, `stats`, `serve`) with Rich progress bars.
- **Acceptance 6**: End-to-end `run` on a small area producing all export formats.

### Stage 7: Frontend Hardening
- **7.1 Dashboard Views**: `/` (dashboard), `/places` (keyset pagination table), `/places/{cid}` (full inspector), `/map` (offline canvas scatter), `/runs` (live controller), `/quality` (audit & retry).
- **7.2 Offline Integrity**: 100% vendored CSS/JS, zero CDN calls, system font stacks.
- **7.3 XSS Security**: Strict escaping of user strings.
- **7.4 Demo Seeder**: `serve --demo` populates sample records if DB is empty.
- **Acceptance 7**: Server runs on 127.0.0.1; renders completely with Wi-Fi off; 100k rows responsive under 500ms.

### Stage 8: Tests & Documentation
- **8.1 Parser Tests**: Tests against all 7 static fixtures.
- **8.2 Tiling & DB Tests**: 20-result subdivision, 19-result negative test, dedupe tests, export round-trip tests, API tests for all routes.
- **8.3 Documentation**: Comprehensive `README.md`.
- **Acceptance 8**: Full pytest suite green.
- **Gate 8**: Report one-line status per stage 1-8.

### Stages 9–12: Adversarial Verification, Rectification, Clean-Room, & Sign-off
- **Stage 9**: Execute full adversarial test matrix A through J (Environment, Schema, Parsers, Tiling, Smoke, Resilience, Exports, Frontend, CLI, Docs).
- **Stage 10**: Rectification loop for any defect.
- **Stage 11**: Clean-room execution (0 to 9 steps).
- **Stage 12**: Generate `FINAL_REPORT.md` with complete verification sign-off block.
