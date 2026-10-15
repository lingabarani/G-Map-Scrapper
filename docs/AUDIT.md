# Project Audit & Inventory (`docs/AUDIT.md`)

Date: 2026-10-13  
Commit/Baseline: Stage 0 Audit

---

## 0.1 Module Inventory & Status

| Module / File Path | Status | Reason / Current State |
|---|---|---|
| `src/gmaps_scraper/__init__.py` | **complete** | Package initialization file present. |
| `src/gmaps_scraper/__main__.py` | **complete** | Invokes `cli.app()` for `python -m gmaps_scraper` execution. |
| `src/gmaps_scraper/config.py` | **complete** | Pydantic BaseSettings class loading `default.yaml` with Path support and extra arg tolerance. |
| `src/gmaps_scraper/db.py` | **complete** | Full SQLite repository with WAL mode, foreign keys, 7 tables, FTS5 triggers, and connection closing. |
| `src/gmaps_scraper/http.py` | **complete** | Async httpx client with rate limiting, concurrency semaphore, headers, and retry logic. |
| `src/gmaps_scraper/logging_setup.py` | **complete** | Configures rotating/structured file logs and rich console logging. |
| `src/gmaps_scraper/models.py` | **complete** | Pydantic schemas for PlaceStub, PlaceDetails, Hours, Attributes, Reviews, Photos. |
| `src/gmaps_scraper/parser_state.py` | **complete** | Balanced-bracket scanner for `APP_INITIALIZATION_STATE`, XSSI stripper, and strict path assertion. |
| `src/gmaps_scraper/search.py` | **complete** | Protobuf query generator and search RPC parser with unpolluted street address extraction. |
| `src/gmaps_scraper/tiling.py` | **complete** | Recursive 4-quadrant spatial subdivision with overlap and CID deduplication. |
| `src/gmaps_scraper/details.py` | **complete** | Place details extractor for hours, attributes, popular times, and thumbnail with path assertions. |
| `src/gmaps_scraper/reviews.py` | **complete** | Boq proxy pagination extractor for author, rating, text, reactions, owner reply, and tokens. |
| `src/gmaps_scraper/photos.py` | **complete** | Async photo downloader saving to `data/photos/{cid}/` with validation. |
| `src/gmaps_scraper/export.py` | **complete** | Exporter supporting CSV, JSONL, and Parquet with streaming byte output for API. |
| `src/gmaps_scraper/checkpoint.py` | **complete** | Tracks active run state, discovered/enriched counters, resume state, and ETA estimation. |
| `src/gmaps_scraper/fallback_browser.py` | **stub** | 0-byte file; playwright browser fallback renderer to be implemented in Stage 1/2. |
| `src/gmaps_scraper/cli.py` | **complete** | Typer CLI commands: `init`, `search`, `enrich`, `reviews`, `photos`, `export`, `run`, `resume`, `stats`, `serve`. |
| `src/gmaps_scraper/web/app.py` | **complete** | FastAPI local app on 127.0.0.1 serving all HTML views and JSON API endpoints. |
| `src/gmaps_scraper/web/demo_data.py` | **complete** | Seeds rich sample data (including `<script>alert(1)</script>` XSS test case). |
| `src/gmaps_scraper/web/static/css/style.css` | **complete** | Hand-crafted dense dark-mode stylesheet without any CDN or external font dependencies. |
| `src/gmaps_scraper/web/static/js/canvas_charts.js` | **complete** | 100% offline HTML5 canvas histograms and bar charts. |
| `src/gmaps_scraper/web/static/js/canvas_map.js` | **complete** | 100% offline HTML5 canvas coordinate scatter plot with pan, zoom, and clustering. |
| `src/gmaps_scraper/web/static/js/app.js` | **complete** | Keyboard shortcuts (`j`/`k`, `/`, `Enter`, `Esc`), drawer logic, HTML escaping. |
| `src/gmaps_scraper/web/templates/*.html` | **complete** | Jinja2 templates (`base`, `dashboard`, `places`, `place_detail`, `map`, `runs`, `quality`). |
| `tests/fixtures/search_rpc.json` | **complete** | Real capture for NYC coffee discovery tile (117 KB). |
| `tests/fixtures/place_details_rpc.json` | **complete** | Real capture for 787 Coffee place details (126 KB). |
| `tests/fixtures/reviews_rpc.json` | **complete** | Real capture for 787 Coffee reviews batch (56 KB). |
| `tests/fixtures/place_page.html` | **complete** | Real capture for place HTML with `APP_INITIALIZATION_STATE` (208 KB). |
| `tests/fixtures/place_no_website_no_phone.json` | **complete** | Real capture for Rafiqi's Food Cart with no website and no phone. |
| `tests/fixtures/place_permanently_closed.json` | **complete** | Real capture for permanently closed restaurant (Uncle Boons). |
| `tests/fixtures/reviews_second_place.json` | **complete** | Real capture for Thai Diner reviews batch (67 KB) proving root path stability. |

---

## 0.2 Frontend Reference Classification

- **DB Layer Import**: The frontend imports `Database` directly from `..db`. This module exists and is fully functional.
- **Tables & Columns Referenced in Web Layer**:
  - `places`: `cid`, `ftid`, `place_id`, `name`, `address`, `street_address`, `latitude`, `longitude`, `website`, `phone`, `category`, `rating`, `review_count`, `timezone`, `claimed_status`, `photo_thumbnail`, `status`, `attempts`, `error`, `created_at`, `updated_at`. (All exist in schema).
  - `hours`: `id`, `cid`, `day`, `day_name`, `formatted_text`, `is_closed`. (All exist in schema).
  - `attributes`: `id`, `cid`, `group_id`, `group_name`, `attribute_id`, `attribute_name`, `is_enabled`, `display_value`. (All exist in schema).
  - `reviews`: `review_id`, `cid`, `author_name`, `author_id`, `author_avatar_url`, `author_profile_url`, `rating`, `relative_date`, `timestamp_ms`, `text`, `language`, `owner_response_text`, `owner_response_date`, `reaction_count`. (All exist in schema).
  - `photos`: `id`, `cid`, `review_id`, `photo_url`, `local_path`, `attribution`, `file_size`. (All exist in schema).
  - `popular_times`: `id`, `cid`, `day_idx`, `hour_idx`, `popularity_pct`. (All exist in schema).
  - `run_state`: `run_id`, `query`, `bbox`, `status`, `current_step`, `total_discovered`, `total_enriched`, `total_reviews`, `total_photos`, `errors_count`, `start_time`, `end_time`, `last_active`. (All exist in schema).
  - *Identified & Resolved Discrepancies*:
    - Early code had `hours_updated = 1` in an update query; this was eliminated as `hours_updated` is not part of the schema.
    - Named parameter bindings in `Database.upsert_place_stub` were supplied with default `None`/`0` values to prevent SQLite parameter binding errors.
    - Modern Starlette `TemplateResponse` signature was updated to use keyword arguments (`request=request, name=..., context=...`).
- **API Routes**:
  - All routes called by templates (`/api/stats`, `/api/places`, `/api/map`, `/api/progress`, `/api/logs`, `/api/run/{action}`, `/api/resume`, `/api/retry-failed`, `/api/export`, `/media/photos/{cid}/{filename}`) are implemented in `web/app.py`.

---

## 0.3 Exact `pytest -q` Baseline Output

```
..................................                                       [100%]
============================== warnings summary ===============================
C:\Users\OND\AppData\Local\Programs\Python\Python312\Lib\site-packages\fastapi\testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
33 passed, 1 warning in 5.12s
```

---

## 0.4 Verification of Phase 0 Corrections

- **0.4(a) Street Address Name Unpollution**: `search.py` prioritizes `p[39]` for street address. If raw address `p[18]` begins with the business name prefix (`"{name}, "`), the prefix is stripped so addresses are never name-polluted. Verified in `tests/test_parsers.py::test_parse_search_rpc_and_address_pollution_correction`.
- **0.4(b) 3 Additional Real Fixtures Captured**:
  1. `place_no_website_no_phone.json`: Rafiqi's Food Cart (verified `website is None`, `phone is None`).
  2. `place_permanently_closed.json`: Uncle Boons (`p[88][0] == 'CLOSED'`).
  3. `reviews_second_place.json`: Thai Diner (`0x89c259e1eb6b2143:0xf8ef42b02bc96daf`).
- **0.4(c) Second Reviews Fixture Cross-Validation**: Validated that `data[1][10]` container with `container[2]` reviews array and `container[6]` continuation token is identical across independent businesses.
- **0.4(d) Explicit Named Path Assertions**:
  - `MissingAttributesPathError` raised when `p[100][1]` is missing.
  - `MissingReviewsRootPathError` raised when `data[1][10]` is missing.
  - `MissingHtmlFallbackStateError` raised when `state[5][3][2]` is missing.
