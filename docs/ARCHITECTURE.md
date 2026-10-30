# System Architecture & Technical Design (`ARCHITECTURE.md`)

## 1. System Overview & Component Diagram

The `gmaps-scraper` application is engineered as a local-first, modular data processing system. It consists of six tightly integrated subsystems:
1. **Target Scope & Taxonomy Configuration**: Geographic boundaries (US 50 states, Mexico 32 entities) and category keyword mappings.
2. **Spatial Discovery & Google Maps Scraper**: Protobuf RPC generation, 4-quadrant grid subdivision, and details/reviews extraction.
3. **Official Website Discovery & SSRF Verification**: Domain candidate discovery, multi-hop SSRF IP protection, and multi-signal corroboration.
4. **Safe Crawler & Intelligence Extraction**: Same-domain bounded traversal, Schema.org JSON-LD parsing, and executive/contact extraction.
5. **Incremental Intelligence Engine**: Multi-adapter public querying, canonical normalization, and conflict-aware candidate comparison.
6. **Persistence, Storage Management & Web Dashboard**: Central SQLite WAL database, regional SQLite synchronization, export generators, and offline FastAPI dashboard.

```mermaid
flowchart TB
    subgraph Layer1 ["1. Configuration & Scope Management"]
        YAML_REG["pipeline2_regions.yaml\n(50 US States, 32 MX Entities)"]
        YAML_VERT["verticals.yaml\n(10 Industry Verticals)"]
        SCOPE_MOD["scope.py\n(Jurisdiction Validation & ISO Codes)"]
        TAX_MOD["taxonomy.py\n(Keyword Precedence Matching)"]
        YAML_REG --> SCOPE_MOD
        YAML_VERT --> TAX_MOD
    end

    subgraph Layer2 ["2. Pipeline Orchestration & CLI Engine"]
        CLI_APP["cli.py / __main__.py\n(Typer CLI & Signal Handlers)"]
        ORCH["orchestrator.py (PipelineOrchestrator)\n• Task Scheduler & Lifecycle\n• Pause / Resume / Cancel\n• Rate Limits & Semaphores"]
        CLI_APP --> ORCH
        SCOPE_MOD --> ORCH
    end

    subgraph Layer3 ["3. Spatial Ingestion & Maps Extraction"]
        TILING["tiling.py\n(4-Quadrant Grid Subdivision)"]
        HTTP_CLIENT["http.py (HttpClient)\n(Async HTTPX + Backoff)"]
        SEARCH_RPC["search.py\n(Protobuf Search RPC)"]
        DETAILS_RPC["details.py\n(Hours & Attributes)"]
        REVIEWS_RPC["reviews.py\n(Boq Proxy Pagination)"]
        ORCH --> TILING
        TILING --> SEARCH_RPC
        SEARCH_RPC --> HTTP_CLIENT
        DETAILS_RPC --> HTTP_CLIENT
        REVIEWS_RPC --> HTTP_CLIENT
    end

    subgraph Layer4 ["4. Verification & Safe Crawling"]
        SSRF["website/ssrf.py\n(Multi-Hop SSRF Guard)"]
        DISC["website/discovery.py\n(Candidate URL Discovery)"]
        VERIF["website/verifier.py\n(Multi-Signal Scorer)"]
        CRAWL["website/crawler.py\n(Bounded Same-Domain Crawler)"]
        EXTRACT["website/extractor.py\n(JSON-LD & Header Extractor)"]
        
        ORCH --> DISC --> SSRF
        ORCH --> VERIF --> SSRF
        ORCH --> CRAWL --> SSRF
        CRAWL --> EXTRACT
    end

    subgraph Layer5 ["5. Incremental Intelligence Engine"]
        ADAPTERS["incremental/adapters/\n• CompanyWebsite\n• StateRegistry\n• ProfessionalProfile\n• OpenBusiness"]
        COMPARATOR["incremental/comparison.py\n(Candidate Comparison Engine)"]
        NORM["incremental/normalizer.py\n(Phone/URL/Name Normalizer)"]
        ORCH --> ADAPTERS --> NORM --> COMPARATOR
    end

    subgraph Layer6 ["6. Storage, Exports & Web Interface"]
        CENTRAL_DB[("data/gmaps.sqlite\n(WAL Mode, FTS5 Triggers,\n21 Normalized Tables)")]
        REG_STORAGE["storage/manager.py\n(Regional Partitioning)"]
        EXPORTER["export.py\n(CSV, JSONL, Parquet)"]
        FASTAPI["web/app.py\n(FastAPI Dashboard on 127.0.0.1:8765)"]

        SEARCH_RPC -->|"CID Dedup & Upsert"| CENTRAL_DB
        EXTRACT -->|"Profiles, Leaders, Contacts"| CENTRAL_DB
        COMPARATOR -->|"Additional Intelligence"| CENTRAL_DB
        CENTRAL_DB --> REG_STORAGE
        CENTRAL_DB --> EXPORTER
        CENTRAL_DB --> FASTAPI
        ORCH -->|"SSE Real-time Events"| FASTAPI
    end
```

---

## 2. Directory Layout & Module Responsibilities

The codebase resides in `src/gmaps_scraper/`:

```text
src/gmaps_scraper/
├── __init__.py               # Package metadata and version definition
├── __main__.py               # CLI entry point invoking cli.app()
├── config.py                 # Pydantic BaseSettings loading YAML and env vars
├── db.py                     # Central SQLite repository with 21 tables and WAL mode
├── cli.py                    # Typer command-line interface with Rich tables
├── http.py                   # Async HTTPX wrapper with rate limiting & backoff
├── search.py                 # Protobuf query generator & search RPC parser
├── tiling.py                 # 4-quadrant recursive spatial grid subdivision
├── details.py                # Place details parser for hours, attributes & thumbnail
├── reviews.py                # Boq proxy pagination extractor for reviews
├── photos.py                 # Local photo downloader into data/photos/{cid}/
├── export.py                 # Multi-format data exporter (CSV, JSONL, Parquet)
├── checkpoint.py             # Active run state tracking, counters, and ETA
├── logging_setup.py          # Rotating file logging and rich console handler
├── models.py                 # Pydantic core data schemas
├── orchestrator.py           # Multi-stage pipeline scheduler and task coordinator
├── parser_state.py           # Balanced-bracket scanner for initialization state
├── scope.py                  # Pipeline 2 US/MX country and region validator
├── taxonomy.py               # Keyword-based business vertical mapping
├── fallback_browser.py       # Playwright browser fallback stub (uninstantiated)
├── geo/                      # Geographic boundaries and natural language parsing
│   ├── boundaries.py         # State/entity bounding box coordinates
│   ├── nlparse.py            # Natural language parser into (query, location)
│   └── resolve.py            # Bounding box and coordinate resolver
├── website/                  # Step 2 & 3: Discovery, verification, and crawling
│   ├── ssrf.py               # Multi-hop SSRF validation and IP blocklist
│   ├── discovery.py          # Candidate website discovery
│   ├── verifier.py           # Multi-signal verification scoring engine
│   ├── crawler.py            # Bounded, same-domain safe page crawler
│   ├── extractor.py          # JSON-LD, profile, leadership, and contact extractor
│   ├── service.py            # Website discovery service orchestration
│   └── enrichment_service.py # Website enrichment service orchestration
├── incremental/              # Incremental intelligence & candidate comparison
│   ├── comparison.py         # Comparison engine (exact, new, conflict, unverified)
│   ├── normalizer.py         # Canonical normalizer for strings, phones, and URLs
│   ├── service.py            # Incremental enrichment orchestration service
│   ├── models.py             # Candidate findings and source check schemas
│   ├── export.py             # Intelligence export utilities
│   └── adapters/             # Modular public data source adapters
│       ├── base.py           # BaseSourceAdapter interface
│       ├── company_site.py   # Company website extended adapter
│       ├── state_registry.py # State registry public records adapter
│       ├── prof_profile.py   # Professional profile public records adapter
│       └── open_business.py  # Open business directories adapter
├── storage/                  # Regional database and backup management
│   └── manager.py            # RegionalStorageManager: state SQLite partitioning
└── web/                      # FastAPI Web Application & Dashboard
    ├── app.py                # REST endpoints, SSE streams, template routes
    ├── demo_data.py          # Safe sample data seeder for offline evaluation
    ├── static/               # Zero-CDN stylesheets and HTML5 canvas scripts
    └── templates/            # Jinja2 templates (dashboard, places, pipeline, etc.)
```

---

## 3. Execution Entry Points

1. **CLI Commands (`src/gmaps_scraper/cli.py`)**:
   - `python -m gmaps_scraper <command> [options]`
   - Direct commands for every pipeline stage: `init`, `search`, `enrich`, `reviews`, `photos`, `export`, `pipeline-plan`, `pipeline-run`, `pipeline-status`, `website-discover`, `website-enrich`, `incremental-enrich`, `storage-sync`, `serve`.
2. **Web Dashboard Server (`src/gmaps_scraper/web/app.py`)**:
   - `python -m gmaps_scraper serve --port 8765 --host 127.0.0.1`
   - FastAPI application serving visual controls, map plots, live logs, and SSE progress updates.
3. **Programmatic Python API**:
   - `from gmaps_scraper import Database, PipelineOrchestrator, ScraperConfig`

---

## 4. Task Scheduling & Concurrent Worker Execution

### 4.1 Orchestration Workflow
The `PipelineOrchestrator` (`orchestrator.py`) handles execution planning and state management:

```mermaid
sequenceDiagram
    autonumber
    actor Operator as CLI / Web Dashboard
    participant Orch as PipelineOrchestrator
    participant DB as SQLite (collection_tasks)
    participant Pool as Category Worker Pool
    participant HTTP as HttpClient (Rate-Limited)
    participant Maps as Google Maps Endpoints

    Operator->>Orch: start(PipelineConfig)
    Orch->>DB: Fetch/Create collection_tasks
    Orch->>Pool: Spawn N workers (category_workers)
    loop For each Task (State + Category)
        Pool->>HTTP: Fetch Root Viewport Tile
        HTTP->>Maps: Protobuf Search RPC
        Maps-->>HTTP: Response (Anti-XSSI prefix + JSON)
        HTTP-->>Pool: Parsed Places
        alt Results Count >= 20
            Pool->>Pool: Subdivide into 4 Quadrants (Tiling)
        end
        Pool->>DB: Upsert Places (CID Dedup)
        Pool->>DB: Mark collection_task Completed
    end
    Orch->>Operator: Run Completed
```

### 4.2 Worker Concurrency Controls
The scraper controls concurrency at two distinct tiers:
1. **Category Worker Pool (`category_workers`)**:
   - Controls how many categories run in parallel within a region (default: `3`).
   - If `all_categories_concurrent: true` or `category_workers: 'all'`, all configured categories (10 standard) execute simultaneously.
2. **Network Request Semaphore (`request_concurrency` / `concurrency`)**:
   - Irrespective of the number of active category workers, outbound HTTP traffic is governed by a shared `asyncio.Semaphore` (`request_concurrency`, default: 3).
   - This prevents network saturation, socket exhaustion, and aggressive server-side throttling.

---

## 5. Data Extraction, Normalization & Deduplication

### 5.1 Protobuf RPC Extraction & Stripping
Google Maps search and details endpoints prepend an anti-XSSI security token `)]}'\n` to prevent unauthorized JSON cross-site inclusion. The scraper strips this prefix before decoding JSON payloads via `json.loads` (`search.py`, `parser_state.py`).

### 5.2 Deduplication: Current vs. Proposed Separate-Stage Architecture

```mermaid
flowchart TD
    subgraph Current ["Current Implementation: In-Flight Upsert Deduplication"]
        direction TB
        C1["Scraper Tile Harvest"] --> C2["Extract CID (64-bit uint)"]
        C2 --> C3["SQLite: upsert_place_stub()\nON CONFLICT(cid) DO UPDATE\nSET field = COALESCE(excluded.field, places.field)"]
        C3 --> C4[("data/gmaps.sqlite")]
    end

    subgraph Proposed ["Proposed Future Architecture: Decoupled Collection & Dedup"]
        direction TB
        P1["Stage 1: Raw Ingestion"] --> P2["Append-Only Raw Stream\n(data/raw/*.jsonl)"]
        P2 --> P3["Stage 2: Offline Dedup & Reconcile"]
        P3 --> P4["Cross-Source CID & Phone/Address Match"]
        P4 --> P5["Entity Graph & Clean Master Record"]
        P5 --> P6[("Production Warehouse")]
    end
```

- **Current Implementation**: Deduplication operates **in-flight** upon database write. Google's permanent Customer Identification number (`cid`) serves as the primary key of the `places` table. Duplicate encounters across overlapping tiles trigger an `ON CONFLICT(cid)` update using `COALESCE` to merge attributes without creating redundant rows. Uniqueness constraints on `company_leaders (cid, full_name, title)` and `company_contacts (cid, contact_type, contact_value)` prevent duplicate public leadership and contact entries.
- **Proposed Future Workflow**: High-throughput collection runs as an append-only raw ingestion stage, writing immutable JSONL dumps to disk without immediate database upserts. An offline batch deduplication stage subsequently resolves entity clusters using fuzzy phone, address, and domain matching across diverse providers.

---

## 6. Persistence, Checkpoints & Recovery

1. **Write-Ahead Logging (WAL)**: The central database executes with `PRAGMA journal_mode = WAL` and `PRAGMA synchronous = NORMAL`, ensuring concurrent reads alongside active writes without lock starvation.
2. **Atomic State Checkpoints**:
   - `collection_tasks`: Tracks status (`pending`, `running`, `completed`, `failed`), attempt counts, and error summaries per `(country, region, category)`.
   - `geographic_tiles`: Tracks spatial bounding boxes, subdivision depth, results count, and last checkpoint timestamp.
   - `pipeline_runs`: Persists orchestrator run configuration, completed task counts, and error states.
3. **Resumption**:
   - CLI: `python -m gmaps_scraper resume` or `pipeline-resume --run-id <ID>`.
   - The orchestrator checks existing task statuses in SQLite. Already-completed tasks and tiles are bypassed instantly without redundant HTTP requests.

---

## 7. Security Architecture: Multi-Hop SSRF Defense

When discovering and crawling external websites, the scraper enforces strict SSRF controls (`website/ssrf.py`):
1. **URL Scheme Whitelist**: Only `http` and `https` schemes are accepted.
2. **Pre-Flight DNS Resolution**: Target hostnames are resolved via `socket.getaddrinfo`.
3. **IP Range Blacklist**: The resolved IP is evaluated against `ipaddress.ip_network`:
   - Loopback: `127.0.0.0/8`, `::1`
   - Private Networks (RFC 1918): `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
   - Carrier-Grade NAT: `100.64.0.0/10`
   - Link-Local & Cloud Metadata: `169.254.0.0/16`, `fe80::/10`
   - Broadcast & Unspecified: `0.0.0.0`, `255.255.255.255`
4. **Redirect Tracking**: If an external site issues an HTTP 301/302 redirect, the destination URL is intercepted and re-validated against the SSRF guard before the next request is dispatched.
