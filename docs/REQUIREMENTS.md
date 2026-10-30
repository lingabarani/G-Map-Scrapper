# Requirements Specification (`REQUIREMENTS.md`)

## 1. Functional Requirements

### 1.1 Geographic Business Ingestion
- **FR-01 (Spatial Grid Decomposition)**: The system must accept geographic query bounds or administrative jurisdictions and decompose them recursively into 4-quadrant tiles (`tiling.py`) with a configurable overlap percentage (`tile_overlap_pct`, default: 10%) up to a maximum depth (`tile_max_depth`, default: 3).
- **FR-02 (Direct Protobuf RPC Execution)**: The system must construct Google Maps Protobuf search requests (`search.py:build_search_pb`) and parse raw responses without relying on third-party scraping services or headless browser rendering engines.
- **FR-03 (CID-Based Entity Deduplication)**: Incoming place entities must be deduplicated on Google's permanent 64-bit Customer Identification number (`cid`). The database upsert logic must merge existing records using SQL `COALESCE` to preserve historical metadata.
- **FR-04 (Deep Attribute & Review Harvesting)**: The system must extract full operating schedules (Monday–Sunday), accessibility attributes, secondary category arrays, claimed status, and paginated customer reviews via the Google Boq proxy (`reviews.py`).
- **FR-05 (Media Ingestion)**: The system must download place thumbnails and review photos locally into `data/photos/{cid}/` up to a configurable maximum dimension (`photo_max_width`, default: 800px).

### 1.2 Website Discovery & Multi-Signal Verification
- **FR-06 (Candidate URL Discovery)**: For business entities lacking an official web link, the system must generate candidate domain queries (`website/discovery.py`) and evaluate existing links.
- **FR-07 (Multi-Hop SSRF Defense)**: Every candidate link and subsequent HTTP redirect must be vetted against strict SSRF constraints (`website/ssrf.py`), immediately aborting requests targeting loopback addresses (`127.0.0.1`), RFC 1918 private subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), cloud metadata services (`169.254.169.254`), or non-HTTP schemes.
- **FR-08 (Directory & Social Rejection)**: Candidate URLs pointing to known directories or social platforms (e.g., Facebook, Yelp, Instagram, TripAdvisor, YellowPages) must be rejected from company domain status.
- **FR-09 (Multi-Signal Corroboration)**: Domain candidates must be scored using tokenized name matching, `<title>` tags, `<meta name="description">`, and `<h1>`-`<h3>` headers, classifying status into `verified`, `needs_review`, `failed`, or `website_missing`.

### 1.3 Bounded Website Crawling & Intelligence Extraction
- **FR-10 (Same-Domain Restricted Crawl)**: For verified businesses, the crawler (`website/crawler.py`) must traverse same-domain public links up to a hard cap (`max_crawl_pages`, default: 5), prioritizing core paths: `/about`, `/team`, `/leadership`, `/services`, `/locations`, and `/contact`.
- **FR-11 (Structured Corporate Profiling)**: The extractor (`website/extractor.py`) must identify the company overview, industry classification, service listings, operating locations, and primary contact page URL.
- **FR-12 (Executive Leadership Extraction)**: The system must parse Schema.org `Person` JSON-LD blocks and HTML header structures to extract leadership members (`full_name`, `title`, `role_type`, `biography`, `confidence`), enforcing uniqueness on `(cid, full_name, title)`.
- **FR-13 (Business Contact Extraction)**: The system must extract corporate email addresses and telephone numbers, filtering out binary assets, tracking scripts, and invalid syntax, enforcing uniqueness on `(cid, contact_type, contact_value)`.
- **FR-14 (Provenance Tracking)**: Every extracted leadership member, contact point, and corporate profile attribute must retain its exact origin `source_url`.

### 1.4 Incremental Intelligence & Conflict Engine
- **FR-15 (Modular Source Adapters)**: The system must query public data sources through priority-ordered adapters (`incremental/adapters/`): Company Websites, State Registries, Professional Profiles, and Open Business Directories.
- **FR-16 (Candidate Comparison)**: Candidate findings must be compared against existing records (`incremental/comparison.py`), classifying findings into:
  - `EXACT_MATCH`: Value matches existing verified records.
  - `NEW_INFORMATION`: Field was empty; record is updated non-destructively.
  - `POSSIBLE_MATCH`: Partial token or fuzzy overlap requiring review.
  - `CONFLICT`: Discrepancy detected; existing value is preserved and conflict is logged.
  - `UNVERIFIED`: Insufficient corroboration.
- **FR-17 (Intelligence Logging)**: Findings and source access history must be stored in `additional_intelligence` and `intelligence_sources` tables.

### 1.5 Storage, Synchronization & Exports
- **FR-18 (Central & Regional Partitioning)**: All entities must be persisted in central SQLite (`data/gmaps.sqlite`). The storage manager (`storage/manager.py`) must synchronize state-wise databases (`data/databases/{country}/{state}_{code}.sqlite`) while preserving the central database as the single source of truth.
- **FR-19 (Multi-Format Export)**: The platform must generate CSV, JSON Lines (JSONL), and Apache Parquet export packages partitioned by date and jurisdiction under `data/exports/{YYYY-MM-DD}/{country}/{region}/`.

### 1.6 Management, Orchestration & Web UI
- **FR-20 (Orchestration Lifecycle)**: The pipeline orchestrator (`orchestrator.py`) must provide programmatically callable and CLI-accessible actions to `start`, `pause`, `resume`, `cancel`, and `retry-failed` tasks.
- **FR-21 (Local Web Interface)**: The FastAPI web server must serve real-time progress via Server-Sent Events (SSE), offline `<canvas>` scatter maps, data inspection drawers, and filterable search tables at `http://127.0.0.1:8765`.

---

## 2. Non-Functional Requirements

### 2.1 Reliability & Resilience
- **NFR-01 (Zero External Scraping Cloud Dependency)**: The entire ingestion, verification, and extraction workflow must function without third-party paid scraping services.
- **NFR-02 (Rate Limiting & Politeness)**: Requests to Google endpoints and company domains must observe per-host rate limiting (`per_host_rate`, default: 2.0 req/sec) and exponential backoff (`backoff_base`, default: 1.5) with retry caps (`max_retries`, default: 3).
- **NFR-03 (Graceful Interruption & Resumability)**: In the event of a `SIGINT` (Ctrl+C) or process termination, active SQLite transactions must be committed immediately, and state must be recoverable via `python -m gmaps_scraper resume` or pipeline checkpoints.
- **NFR-04 (Bounded Memory & Concurrency)**: Concurrency must be strictly controlled via `asyncio.Semaphore` (`concurrency`, default: 5; `request_concurrency`, default: 3) to prevent socket exhaustion and excessive memory consumption.

### 2.2 Security & Data Protection
- **NFR-05 (Localhost Binding)**: The web dashboard and REST API must bind strictly to loopback (`127.0.0.1`), preventing unauthorized external network access.
- **NFR-06 (Strict SSRF Prevention)**: Domain verification and crawler HTTP clients must perform pre-flight DNS resolution and IP verification on every redirect to guarantee no internal services are probed.
- **NFR-07 (Data Hygiene & Zero Secret Leaks)**: Configuration files and logs must never store API tokens, passwords, or personal credentials.

### 2.3 Maintainability & Code Quality
- **NFR-08 (Zero-CDN Architecture)**: The web dashboard must bundle all CSS stylesheets and JavaScript assets locally. No external CDNs (fonts, styles, scripts) are permitted.
- **NFR-09 (High Test Coverage)**: Every critical pipeline phase must be validated with automated unit and regression tests (188 tests currently passing).

---

## 3. Runtime & Dependency Requirements

### 3.1 Python Runtime
- **Runtime**: Python 3.10 or higher.
- **Verified Runtime**: Python 3.12.10 (tested on Windows 10/11 and Linux environments).
- **Standard Library Modules Utilized**: `sqlite3`, `asyncio`, `urllib.parse`, `ipaddress`, `socket`, `json`, `html`, `re`, `logging`, `pathlib`, `typing`.

### 3.2 Python Package Dependencies
The project manifest is defined in [`pyproject.toml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/pyproject.toml) and pinned in [`requirements.txt`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/requirements.txt):

| Package | Declared Version | Pinned Version | Purpose |
| :--- | :--- | :--- | :--- |
| `fastapi` | `>=0.111.0` | `0.143.0` | Local Web Dashboard and REST API engine |
| `uvicorn` | `>=0.30.0` | `0.54.0` | ASGI server runtime for FastAPI |
| `jinja2` | `>=3.1.4` | `3.1.6` | Server-side template rendering for UI |
| `pydantic` | `>=2.7.0` | `2.14.1` | Data validation and schema parsing |
| `pydantic-settings` | `>=2.2.0` | `2.15.0` | Typed runtime configuration management |
| `typer` | `>=0.12.3` | `0.27.3` | CLI command framework |
| `rich` | `>=13.7.1` | `15.0.0` | Terminal formatting, tables, and progress bars |
| `httpx` | `>=0.27.0` | `0.28.1` | Async HTTP client with redirect and timeout control |
| `tenacity` | `>=8.3.0` | `9.2.1` | Exponential backoff and retry decorators |
| `pandas` | `>=2.2.2` | `3.0.5` | Tabular data processing and file formatting |
| `pyarrow` | `>=15.0.0` | `>=15.0.0` | Parquet binary dataset serialization |
| `pyyaml` | `>=6.0.1` | `>=6.0` | Configuration and region manifest parsing |
| `pytest` | `>=9.0.0` | `9.1.1` | Automated test suite execution |
| `pytest-asyncio` | `>=1.4.0` | `1.4.0` | Asynchronous test fixture support |
| `brotli` | `>=1.1.0` | `>=1.1.0` | Brotli decompression for HTTP responses |
| `python-dotenv` | `>=1.0.0` | `1.2.3` | Optional local `.env` variable loading |

---

## 4. Configuration Options & Defaults

The configuration is managed by `ScraperConfig` in [`src/gmaps_scraper/config.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/config.py) and loaded hierarchically from `config/default.yaml` and optional `config/user.yaml`:

| Setting Key | Type | Default Value | Description |
| :--- | :--- | :--- | :--- |
| `concurrency` | `int` | `5` | Maximum global worker tasks |
| `category_workers` | `int \| str` | `3` | Parallel category workers per region (`'all'` or integer) |
| `all_categories_concurrent` | `bool` | `false` | Run all categories simultaneously |
| `request_concurrency` | `int` | `3` | Maximum concurrent HTTP network calls across workers |
| `per_host_rate` | `float` | `2.0` | Max requests per second directed to a single host |
| `max_retries` | `int` | `3` | Maximum HTTP retry attempts on transient failure |
| `backoff_base` | `float` | `1.5` | Exponential multiplier for retry sleep |
| `tile_base_zoom` | `int` | `15` | Default base zoom level for Google Maps tiles |
| `tile_max_depth` | `int` | `3` | Maximum recursive subdivision depth for spatial tiles |
| `tile_overlap_pct` | `int` | `10` | Percentage overlap between adjacent spatial tiles |
| `max_reviews_per_place` | `int` | `100` | Review extraction limit per business |
| `download_photos` | `bool` | `true` | Download photo assets to local disk |
| `photo_max_width` | `int` | `800` | Image downsampling maximum width |
| `user_agent` | `str` | Chrome 126 Windows NT | Browser User-Agent header for HTTP requests |
| `accept_language` | `str` | `en-US,en;q=0.9` | HTTP Accept-Language header |
| `hl` | `str` | `en` | Google Maps interface language parameter |
| `gl` | `str` | `us` | Google Maps geographic bias parameter |
| `proxies` | `list[str]` | `[]` | Optional list of HTTP/SOCKS5 proxy URIs |
| `headless_fallback` | `bool` | `false` | Enable Playwright fallback (stub in current release) |
| `db_path` | `str` | `data/gmaps.sqlite` | Path to central SQLite repository |
| `raw_data_dir` | `str` | `data/raw` | Path for raw JSON RPC captures |
| `photos_dir` | `str` | `data/photos` | Path for local photo storage |
| `databases_dir` | `str` | `data/databases` | Path for regional SQLite databases |
| `exports_dir` | `str` | `data/exports` | Path for date-partitioned exports |
| `logs_dir` | `str` | `logs` | Path for rotating execution log files |
| `web_port` | `int` | `8765` | Local TCP port for web dashboard |
| `web_host` | `str` | `127.0.0.1` | Local IP address for web dashboard binding |

---

## 5. Network Requirements & Access Rules

1. **Outbound Internet Access**:
   - Outbound HTTPS (TCP 443) access to `*.google.com` (specifically `www.google.com/search` and `www.google.com/httpservice/web/...`).
   - Outbound HTTP/HTTPS (TCP 80/443) access to public company web domains.
2. **DNS Resolution**:
   - System standard DNS resolver (`socket.getaddrinfo`) required for pre-flight SSRF checking and domain validation.
3. **Firewall & SSRF Restrictions**:
   - Outbound connections targeting `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `0.0.0.0`, and IPv6 `::1` or `fc00::/7` are rejected programmatically before socket creation.

---

## 6. Requirements Status & Verification Notes

- **Verified via Code & Tests**: All functional requirements (FR-01 to FR-21), non-functional requirements (NFR-01 to NFR-09), dependencies, and configuration keys are confirmed in active codebase files and covered by 188 passing tests.
- **Playwright Headless Fallback (`headless_fallback: false`)**: The configuration setting exists, but [`fallback_browser.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/fallback_browser.py) is currently an uninstantiated stub file. Headless browser scraping is *not* required for normal HTTP pipeline operation.
- **Cloud Telemetry & Tracking**: Confirmed *absent*. The system operates 100% locally.
