# Business Data Scraping & Enrichment Pipeline (`gmaps-scraper`)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-188%20passed-success.svg)](TESTING.md)
[![Database](https://img.shields.io/badge/storage-SQLite%20WAL-informational.svg)](DATASET.md)
[![Architecture](https://img.shields.io/badge/architecture-local--first-orange.svg)](ARCHITECTURE.md)

A production-grade, 100% local-first business data collection, website verification, and intelligence extraction pipeline.

Built from first principles with **zero external scraping APIs** (no SerpApi, Apify, or ScraperAPI), **zero third-party cloud services**, and **zero mandatory paid dependencies**. The pipeline collects geographic business entities, verifies official company domains with strict multi-hop SSRF controls, safely crawls public pages, and extracts structured corporate profiles, leadership rosters, and business contact channels directly into a local SQLite repository.

---

## Complete Project Documentation Index

| Document | Description |
| :--- | :--- |
| **[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)** | Executive summary, business goals, geographic scope (US 50 states, MX 32 entities), vertical taxonomy, and implementation status. |
| **[REQUIREMENTS.md](REQUIREMENTS.md)** | Functional requirements (FR-01 to FR-21), non-functional requirements (NFR-01 to NFR-09), Python dependencies, configuration defaults, and network access rules. |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | End-to-end data flow, Mermaid system diagrams, module responsibilities, concurrency pools, deduplication mechanics, and SSRF security architecture. |
| **[API_DOCUMENTATION.md](API_DOCUMENTATION.md)** | Complete reference for internal REST and Server-Sent Events (SSE) endpoints, external Google Maps RPC protocols, rate limits, timeouts, and cURL/Python usage examples. |
| **[DATASET.md](DATASET.md)** | Storage layout, Entity-Relationship (ER) diagram, 21 table schemas, field types, synthetic sample records, and quality controls. |
| **[MODEL_DOCUMENTATION.md](MODEL_DOCUMENTATION.md)** | Verification of zero ML/LLM models and complete documentation of deterministic rule-based algorithms: taxonomy engine, multi-signal domain verifier, extraction heuristics, and candidate comparison engine. |
| **[TESTING.md](TESTING.md)** | Pytest test suite architecture, verified execution results (188 tests passing), test fixtures inventory, mocked vs. live testing, and profiling instructions. |
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | Supported OS platforms, virtual environment setup, database initialization, CLI and Web dashboard startup commands, graceful shutdown, and backup procedures. |
| **[CHANGELOG.md](CHANGELOG.md)** | Dated release history grounded in Git commit history and working tree developments. |
| **[CONTRIBUTING.md](CONTRIBUTING.md)** | Developer onboarding guide, code conventions, how to extend business categories or regions, security guidelines, and Pull Request workflow. |
| **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** | Actionable diagnosis and recovery procedures for rate limits (HTTP 429), anti-XSSI decoding, SSRF blocks, database locks, and checkpoint synchronization. |
| **[LICENSE.md](LICENSE.md)** | Formal licensing status notice and third-party dependency open-source licenses. |

---

## Key Capabilities

1. **Local Business Ingestion**: Collects physical places, business metadata, coordinates, categories, and listed websites via local Google Maps endpoints using 4-quadrant adaptive spatial grid tiling.
2. **Official Website Discovery & Verification**: Identifies candidate web domains for businesses lacking URLs, and verifies existing and discovered domains using multi-signal corroboration (name tokens, title/heading tags, meta descriptions, address/phone corroboration, and directory/social platform rejection).
3. **Company & Leadership Information Extraction**: Safely traverses same-domain public website pages (e.g. About, Leadership, Team, Services, Locations, Contact) within bounded limits, extracting corporate overviews, service catalogs, operating locations, executive leadership, and business contact channels while storing exact source URLs for every attribute.
4. **Incremental Intelligence Comparison**: Compares candidate intelligence findings across modular adapters (Company Websites, State Registries, Professional Profiles, Open Sources) against existing records, classifying matches into `EXACT_MATCH`, `NEW_INFORMATION`, `POSSIBLE_MATCH`, `CONFLICT`, and `UNVERIFIED` without destructive overwrites.
5. **Local-First SQLite Repository**: Stores all records, tasks, checkpoints, full-text indexes, and provenance metadata locally in SQLite (`data/gmaps.sqlite`) with Write-Ahead Logging (WAL) and partitioned regional databases (`data/databases/`).
6. **Local Web Dashboard & Canvas Maps**: Exposes an offline-first dark-mode dashboard at `http://127.0.0.1:8765` featuring real-time Server-Sent Events, HTML5 `<canvas>` spatial scatter plots, and data inspection drawers.

---

## Technology Stack

| Component | Technology | Role in Codebase |
| :--- | :--- | :--- |
| **Language** | Python 3.10+ (tested on 3.12.10) | Core execution runtime. |
| **CLI Framework** | Typer & Rich | Command-line interface, progress bars, formatting, and tables. |
| **HTTP Engine** | HTTPX (`httpx>=0.27.0`) | Async HTTP client with per-host rate limiting, redirect interception, and backoff. |
| **Database Engine** | SQLite3 (Standard Library) | WAL mode, FTS5 full-text indexing, JSON storage, foreign keys, and transactions. |
| **Configuration** | PyYAML & Pydantic Settings | Region manifests (`pipeline2_regions.yaml`) and typed runtime configurations. |
| **HTML Parsing** | Standard Library (`re`, `html`, `json`)| Zero-dependency extraction: Schema.org JSON-LD parsing, entity decoding, and text sanitization. |
| **Network Security** | Standard Library (`socket`, `ipaddress`) | Multi-hop SSRF validation, private IP range blocking, loopback prevention, and URL normalization. |
| **Web UI & API** | FastAPI, Uvicorn, Jinja2 | Local dashboard on `127.0.0.1`, inspection drawers, offline `<canvas>` maps. |
| **Test Suite** | Pytest (`pytest>=9.0.0`, `pytest-asyncio`) | Unit, integration, and mocked regression testing (**188 tests passing**). |

---

## Quick Start Guide

### 1. Prerequisites & Installation
Ensure Python 3.10+ is installed. Clone the repository and install dependencies:

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt
```

### 2. Initialize the Database
Create the central SQLite database and directory structure:
```bash
python -m gmaps_scraper init
```

### 3. Launch the Web Dashboard
```bash
python -m gmaps_scraper serve --port 8765 --host 127.0.0.1
```
Visit `http://127.0.0.1:8765` in your browser.

---

## Command-Line Interface (CLI) Reference

The CLI is invoked via `python -m gmaps_scraper <command>`:

| Command | Description | Example Usage |
| :--- | :--- | :--- |
| `init` | Initialize SQLite schema and storage directories. | `python -m gmaps_scraper init` |
| `search` | Collect place stubs via recursive 4-quadrant tiling. | `python -m gmaps_scraper search -q "coffee" -b "40.70,-74.02,0.02"` |
| `enrich` | Deep-enrich place operating hours and attributes. | `python -m gmaps_scraper enrich --limit 50` |
| `reviews` | Harvest customer reviews via Boq RPC. | `python -m gmaps_scraper reviews --limit 50 --max-per-place 100` |
| `photos` | Download place thumbnails and review photos. | `python -m gmaps_scraper photos --limit 50` |
| `website-discover` | Discover and SSRF-verify candidate company websites. | `python -m gmaps_scraper website-discover --country US --region CA` |
| `website-enrich` | Crawl verified websites for leadership and contacts. | `python -m gmaps_scraper website-enrich --country US --region CA` |
| `incremental-enrich`| Compare candidate findings against existing records. | `python -m gmaps_scraper incremental-enrich --country US --region CA` |
| `pipeline-plan` | Preview deterministic multi-stage execution plan. | `python -m gmaps_scraper pipeline-plan --country US --regions CA,NY` |
| `pipeline-run` | Launch automated state-by-state pipeline run. | `python -m gmaps_scraper pipeline-run --country US --regions CA` |
| `pipeline-status` | Display status and task counts of active run. | `python -m gmaps_scraper pipeline-status` |
| `pipeline-pause` | Pause active pipeline run cooperatively. | `python -m gmaps_scraper pipeline-pause` |
| `pipeline-resume` | Resume paused pipeline run. | `python -m gmaps_scraper pipeline-resume` |
| `pipeline-cancel` | Cancel active pipeline run. | `python -m gmaps_scraper pipeline-cancel` |
| `pipeline-retry` | Retry failed tasks within the active run. | `python -m gmaps_scraper pipeline-retry` |
| `storage-sync` | Partition central data into regional state databases. | `python -m gmaps_scraper storage-sync --country US --regions CA` |
| `storage-status` | Inspect regional database sync status. | `python -m gmaps_scraper storage-status` |
| `storage-backup` | Create snapshot backup of SQLite database. | `python -m gmaps_scraper storage-backup` |
| `export` | Export collected data to CSV, JSONL, or Parquet. | `python -m gmaps_scraper export --format parquet --full` |
| `serve` | Start the local FastAPI web dashboard. | `python -m gmaps_scraper serve --port 8765` |

---

## Testing & Verification

Run the comprehensive test suite offline using Pytest:
```bash
pytest
```
- **Verified Result**: `188 passed, 75 warnings in 77.89s` (100% pass rate).
- See [TESTING.md](TESTING.md) for detailed test breakdown and execution commands.

---

## Implementation Status & Known Limitations

- **Completed**:
  - Google Maps Search & Tiling RPC parser (`search.py`, `tiling.py`).
  - CID-based deduplication and COALESCE upserting (`db.py`).
  - US (50 states) and Mexico (32 entities) scope validation (`scope.py`).
  - Multi-hop SSRF validation guard (`website/ssrf.py`).
  - Official website discovery and multi-signal verification (`website/discovery.py`, `website/verifier.py`).
  - Same-domain crawler and JSON-LD leadership/contact extractor (`website/crawler.py`, `website/extractor.py`).
  - Incremental intelligence comparison engine (`incremental/`).
  - Regional storage partitioning and date-stamped exports (`storage/manager.py`).
  - Automated pipeline orchestrator (`orchestrator.py`).
  - FastAPI local dashboard and offline HTML5 canvas map (`web/app.py`).
- **Known Limitations**:
  - `fallback_browser.py`: Currently an uninstantiated stub file; headless browser rendering is not active or needed for the direct HTTP pipeline.
  - Multi-node clustering: The platform is designed for single-node local execution.
