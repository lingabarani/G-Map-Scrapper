# Project Changelog (`CHANGELOG.md`)

All notable changes to the `gmaps-scraper` platform are documented in this file, grounded strictly in verifiable Git commit history, code additions, and test suite expansions.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] - 2026-10-27

### Added
- **Incremental Business Intelligence Service (`src/gmaps_scraper/incremental/`)**:
  - Implemented `IncrementalEnrichmentService` supporting priority-ordered public data source querying.
  - Built modular adapters: `CompanyWebsiteExtendedAdapter`, `StateRegistryAdapter`, `ProfessionalProfileAdapter`, and `OpenBusinessSourcesAdapter`.
  - Added `ComparisonEngine` classifying candidate observations into `EXACT_MATCH`, `NEW_INFORMATION`, `POSSIBLE_MATCH`, `CONFLICT`, and `UNVERIFIED`.
  - Added `additional_intelligence` and `intelligence_sources` tables in SQLite to store field findings, conflict notes, and URL provenance.
- **Regional Storage & Synchronization Manager (`src/gmaps_scraper/storage/`)**:
  - Implemented `RegionalStorageManager` to partition central data from `data/gmaps.sqlite` into regional databases (`data/databases/{country}/{state}_{code}.sqlite`).
  - Added automatic date-stamped export packaging (`data/exports/YYYY-MM-DD/{country}/{region}/`).
  - Added CLI commands `storage-sync`, `storage-status`, and `storage-backup`.
- **Advanced Pipeline Orchestration (`src/gmaps_scraper/orchestrator.py`)**:
  - Implemented `PipelineOrchestrator` managing automated multi-stage runs (`collection`, `discovery`, `enrichment`).
  - Added lifecycle controls: start, pause, resume, cooperative cancellation, and failed task retries.
  - Added two-tier concurrency controls: `category_workers` and shared `request_concurrency` semaphore.
  - Added Server-Sent Events (SSE) event broadcasting for dashboard synchronization.
- **Geographic Boundary Engine (`src/gmaps_scraper/geo/boundaries.py`)**:
  - Added coordinate bounding box definitions for all 50 US States and 32 Mexico Federal Entities.
- **Expanded Quality Assurance Suite (`tests/`)**:
  - Added `test_incremental_enrichment.py`, `test_storage_sync.py`, `test_pipeline_orchestrator.py`, `test_geo_tiling_pipeline.py`, `test_collection_limit_removal.py`, and `test_parallel_category_scraping.py`.
  - Expanded total automated test suite to **188 passing tests** (100% pass rate).

### Changed
- Refactored `src/gmaps_scraper/db.py` to support 21 normalized relational tables, compound uniqueness constraints, and dynamic parameter binding defaults.
- Updated FastAPI web dashboard (`src/gmaps_scraper/web/`) to include dedicated `/pipeline`, `/enrichment`, and `/intelligence` views.

---

## [0.1.0] - 2026-10-15

### Added (`bc3d876`)
- **Documentation Overhaul**: Created comprehensive technical documentation detailing pipeline data flow, module responsibilities, database schemas, and testing instructions.

### Added (`b6a10ed`)
- **Step 3 Company Information Extraction (`src/gmaps_scraper/website/`)**:
  - Implemented `SafeWebsiteCrawler` enforcing same-domain bounded traversal (cap of 5 pages), prioritizing `/about`, `/team`, `/leadership`, `/services`, `/locations`, and `/contact`.
  - Implemented `WebsiteExtractor` for Schema.org JSON-LD blocks (`Person`, `Organization`), HTML leadership rosters, corporate summaries, and verified email/phone contact channels.
  - Added `company_profiles`, `company_leaders`, and `company_contacts` tables in SQLite with unique constraints and source-level URL provenance.
  - Added CLI command `website-enrich`.

### Added (`2eea52a`)
- **Step 2 Website Discovery & Multi-Signal Verification (`src/gmaps_scraper/website/`)**:
  - Implemented `WebsiteDiscoverer` to identify candidate web domains for businesses lacking URLs.
  - Implemented multi-hop SSRF validation (`website/ssrf.py`) blocking loopback addresses, RFC 1918 subnets, cloud metadata IPs, and non-HTTP protocols.
  - Implemented `WebsiteVerifier` evaluating tokenized business name overlap, `<title>` tags, `<meta name="description">`, and `<h1>`-`<h3>` headers.
  - Added directory and social platform exclusion filters (rejecting Yelp, TripAdvisor, Facebook, Instagram).
  - Added CLI command `website-discover`.

### Added (`d975f5b`)
- **Target Scope Configuration (`config/pipeline2_regions.yaml`, `src/gmaps_scraper/scope.py`)**:
  - Enforced strict geographic scope restricted to United States (50 official states) and Mexico (32 federal entities).
  - Added regional collection task tracking in `collection_tasks` table.
  - Added CLI command `scope`.

### Added (`a319ab7`)
- **Baseline Project Commit**:
  - Core Google Maps local scraper using Protobuf search RPC generator (`search.py`) and anti-XSSI parsing.
  - 4-quadrant recursive spatial grid tiling engine (`tiling.py`).
  - CID-based deduplication and place details/reviews extraction (`details.py`, `reviews.py`, `photos.py`).
  - SQLite repository (`db.py`) with WAL mode and FTS5 full-text search.
  - FastAPI local dashboard (`web/app.py`) with offline HTML5 `<canvas>` charts and scatter maps.
  - Baseline Typer CLI (`cli.py`).
