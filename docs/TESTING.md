# Testing & Quality Assurance Guide (`TESTING.md`)

## 1. Testing Framework & Environment

The test suite is built on **Pytest** with asynchronous test execution powered by `pytest-asyncio` and `anyio`.

### Verified Test Run Results
- **Execution Date**: 2026-10-27
- **Platform**: `win32` / Python `3.12.10`
- **Pytest Version**: `pytest-9.1.1`, `pluggy-1.6.0`, `asyncio-1.4.0`
- **Total Tests Collected**: **188**
- **Results**: **188 passed**, **75 warnings**, **0 failed** (Duration: 77.89s)
- **Deprecation Warnings**: Starlette/FastAPI `on_event("startup")` deprecations (safe; library scheduled for lifespan transition).

---

## 2. Test Directory Layout & Inventory

The test suite resides in [`tests/`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/tests/) and is organized into 20 distinct modules:

| Test Module File | Test Count | Scope & Coverage Area |
| :--- | :--- | :--- |
| `test_cleanroom_acceptance.py` | 1 | End-to-end verification of clean database initialization, search, and table counts. |
| `test_collection_limit_removal.py` | 4 | Verifies unlimited collection capacity when `collection_limit` is `None` or `0`. |
| `test_db.py` | 6 | SQLite schema initialization, foreign keys, WAL mode, FTS5 triggers, and connection pooling. |
| `test_end_to_end.py` | 2 | Full workflow test from tile search to place details upsert and export. |
| `test_geo_tiling_pipeline.py` | 11 | Grid tile generation, recursive 4-quadrant subdivision, center coordinates, and bounding boxes. |
| `test_incremental_enrichment.py` | 14 | Source adapters, candidate comparison, conflict detection, and non-destructive merges. |
| `test_live_geo.py` | 6 | Geographic location resolution, state bounding boxes, and natural language query parsing. |
| `test_live_jobs_api.py` | 7 | FastAPI live search job creation, pre-flight estimation, cancellation, and retry APIs. |
| `test_parallel_category_scraping.py` | 16 | Category worker concurrency pool, shared HTTP request semaphore, and rate limiter. |
| `test_parsers.py` | 10 | Protobuf search RPC parser, anti-XSSI stripper, place details parser, and reviews parser. |
| `test_pipeline_orchestrator.py` | 16 | Pipeline lifecycle: start, pause, resume, cancel, singleton locking, and SSE broadcasting. |
| `test_resilience.py` | 5 | HTTP retry backoff, connection timeouts, HTTP 429 backoff, and graceful SIGINT database flushing. |
| `test_scope_and_tasks.py` | 8 | Strict validation of US (50 states) and MX (32 entities) scope and task tracking. |
| `test_storage_sync.py` | 14 | Regional SQLite database partitioning, table synchronization, and date-based exports. |
| `test_taxonomy_and_filters.py` | 10 | Taxonomy keyword precedence matching and multi-dimensional web filtering. |
| `test_tiling.py` | 3 | Bounding box spatial division math, span degrees, and coordinate calculations. |
| `test_web.py` | 23 | FastAPI template routes, REST endpoints, HTML escaping (XSS prevention), and offline canvas charts. |
| `test_website_discovery.py` | 12 | Candidate URL discovery, directory/social platform rejection, and multi-hop SSRF validation. |
| `test_website_enrichment.py` | 20 | Bounded website crawler, Schema.org JSON-LD parser, leadership extraction, and contact harvesting. |
| **Total** | **188** | **Complete pipeline test coverage** |

---

## 3. Ground Truth Test Fixtures (`tests/fixtures/`)

To ensure deterministic, offline test execution without querying live Google endpoints during automated testing, the suite uses real-world network captures captured and verified in [`tests/fixtures/`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/tests/fixtures/):

1. `search_rpc.json` (117 KB): Real capture of a Google Maps search RPC response for a NYC coffee tile (contains 20 place stubs with CIDs, coordinates, ratings, and phone numbers).
2. `place_details_rpc.json` (126 KB): Real capture of place details for "787 Coffee" (contains 7-day operating hours, accessibility attribute groups, and thumbnail links).
3. `reviews_rpc.json` (56 KB): Real capture of Google Boq proxy reviews batch (contains author names, profile links, text, reaction counts, and next-page pagination token).
4. `place_page.html` (208 KB): Real capture of Google Maps place HTML embedding `APP_INITIALIZATION_STATE` for balanced-bracket scanner testing.
5. `place_no_website_no_phone.json`: Real capture of a place lacking website and phone metadata (tests edge-case null handling).
6. `place_permanently_closed.json`: Real capture of a permanently closed restaurant ("Uncle Boons") to verify closed-state handling.
7. `reviews_second_place.json` (67 KB): Second distinct review capture ("Thai Diner") verifying root path parsing stability across multiple entities.

---

## 4. How to Execute Tests

### 4.1 Running the Entire Test Suite
Execute Pytest from the project root directory:
```bash
pytest
```

### 4.2 Running Specific Test Categories

#### Run Core Database Tests
```bash
pytest tests/test_db.py -v
```

#### Run Website Discovery & SSRF Guard Tests
```bash
pytest tests/test_website_discovery.py -v
```

#### Run Website Crawler & Intelligence Extraction Tests
```bash
pytest tests/test_website_enrichment.py -v
```

#### Run Pipeline Orchestrator Lifecycle Tests
```bash
pytest tests/test_pipeline_orchestrator.py -v
```

#### Run Web Dashboard & API Endpoints Tests
```bash
pytest tests/test_web.py -v
```

### 4.3 Running with Execution Profiling
To display test execution duration and identify the slowest running fixtures:
```bash
pytest --durations=10
```

---

## 5. Mocked vs. Live Service Testing

- **Offline Unit & Integration Tests**: 100% of the standard automated test suite executes **completely offline**. HTTP client calls are mocked using real JSON/HTML fixtures, `unittest.mock`, or in-memory SQLite fixtures (`:memory:` or temporary test files).
- **Network Safety**: Tests never transmit live HTTP traffic to Google Maps or third-party company domains. This guarantees CI reproducibility and avoids triggering server-side rate limits.
- **SSRF Unit Tests**: Specifically validate that attempts to reach `127.0.0.1`, `169.254.169.254`, and private RFC 1918 subnets raise `ValueError` before any network socket is opened.

---

## 6. Known Gaps & Untested Scenarios

1. **Headless Browser Fallback (`fallback_browser.py`)**: As noted in architecture documentation, this file is an uninstantiated stub and has no active unit tests.
2. **Prolonged Multi-Day Live Scraping**: Long-term continuous scraping (e.g., scraping all 50 US states continuously over multiple weeks) is subject to real-world network fluctuations, residential IP rotation needs, and dynamic Google anti-bot threshold changes that cannot be fully replicated in synthetic CI environments.
