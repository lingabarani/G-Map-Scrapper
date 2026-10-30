# Project Overview (`PROJECT_OVERVIEW.md`)

## 1. Business Problem & Executive Summary

Modern market research, B2B sales intelligence, and supply chain logistics require accurate, geographically bounded business data. However, acquiring comprehensive local business directories typically incurs heavy external API subscription costs (e.g., SerpApi, Apify, ScraperAPI), third-party vendor lock-in, recurring per-query pricing, and opaque rate limits. Furthermore, simple place discovery often leaves critical business profile gaps—lacking official domain verification, leadership contact rosters, and structured operating footprints.

The `gmaps-scraper` platform solves this problem by delivering an enterprise-ready, **100% local-first data collection, domain verification, and business intelligence extraction engine**. It connects directly to public Google Maps endpoints using custom-crafted Protobuf RPC query generators and recursive spatial grid tiling, discovers and verifies official company web domains with strict Server-Side Request Forgery (SSRF) guardrails, safely crawls verified company websites for leadership rosters and business contact channels, and provides an incremental intelligence comparison engine—all persisted locally in SQLite with zero external cloud dependencies.

---

## 2. Project Goals & Core Objectives

1. **Zero External Scraping APIs**: Operate purely via direct HTTP communication with Google Maps endpoints and target company web servers. No third-party scraping middleware, proxies, or cloud scrapers are required.
2. **Deterministic Geographic Scope**: Restrict national and subnational collection to the United States (50 official states) and Mexico (32 federal entities) using strict bounding-box subdivisions and administrative task tracking.
3. **Multi-Signal Domain Verification**: Mitigate spam, parked domains, directory listings (Yelp, TripAdvisor), and social media profiles (Facebook, Instagram) by scoring name-token overlap, page titles, H1–H3 headers, and meta descriptions before crawling.
4. **Autonomous Company Intelligence**: Extract company overviews, service catalogs, operating locations, executive leadership (`full_name`, `title`, `role_type`, `biography`), and direct contact channels (emails, telephones) with source-level URL provenance.
5. **Incremental Enrichment & Conflict Resolution**: Compare newly acquired intelligence against existing database records, classifying findings into exact matches, new information, possible matches, and conflicts without silently overwriting existing verified data.
6. **Local Persistence & Dashboard UI**: Store all entities in high-performance SQLite databases with Write-Ahead Logging (WAL) and full-text search (FTS5), managed via both a Typer/Rich command-line interface and an offline-capable FastAPI web dashboard on `127.0.0.1`.

---

## 3. Supported Geographic Regions & Business Verticals

### 3.1 Geographic Scope (Pipeline 2)
The collection boundary is hard-coded and validated in [`src/gmaps_scraper/scope.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/scope.py) and configured in [`config/pipeline2_regions.yaml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/config/pipeline2_regions.yaml):

- **United States (US)**: Exactly 50 official US states (e.g., `CA`, `TX`, `NY`, `FL`, `IL`, `PA`, `OH`, etc.), strictly excluding territories and military postal zones.
- **Mexico (MX)**: Exactly 32 Federal Entities (31 sovereign states plus Mexico City `CMX` / `CDMX`, explicitly distinguishing Mexico City from the State of Mexico `MEX`).

Any request attempting to target an unconfigured country code or invalid state abbreviation is rejected with a descriptive `ValueError`.

### 3.2 Business Verticals & Taxonomy
The platform maps raw Google Maps category strings into 10 structured business verticals configured in [`config/verticals.yaml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/config/verticals.yaml):

| Vertical Key | Display Label | Sample Matching Keywords |
| :--- | :--- | :--- |
| `healthcare` | Healthcare & Medical | `hospital`, `clinic`, `medical`, `doctor`, `physician`, `urgent care`, `dentist`, `pharmacy` |
| `hospitality` | Hospitality & Lodging | `hotel`, `motel`, `hostel`, `resort`, `guest house`, `bed and breakfast` |
| `food` | Food & Dining | `restaurant`, `cafe`, `coffee`, `bakery`, `bar`, `pub`, `pizza`, `fast food`, `deli` |
| `retail` | Retail & Shopping | `store`, `shop`, `boutique`, `supermarket`, `grocery`, `convenience`, `mall`, `market` |
| `fitness` | Fitness & Recreation | `gym`, `fitness`, `yoga`, `pilates`, `crossfit`, `sports centre`, `swimming` |
| `beauty` | Beauty & Personal Care | `salon`, `barber`, `spa`, `nail`, `hair`, `tattoo`, `beauty` |
| `automotive` | Automotive | `car repair`, `auto`, `garage`, `petrol`, `gas station`, `car wash`, `dealership` |
| `finance` | Finance & Banking | `bank`, `atm`, `credit union`, `insurance`, `financial` |
| `education` | Education | `school`, `college`, `university`, `kindergarten`, `academy`, `library` |
| `real_estate` | Real Estate | `real estate`, `property`, `estate agent` |
| `other` | Other | Fallback for unclassified categories |

Taxonomy assignment executes in [`src/gmaps_scraper/taxonomy.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/taxonomy.py) using case-insensitive keyword precedence.

---

## 4. Main Capabilities & Modules

The platform is organized into distinct, modular functional layers:

1. **Spatial Grid Tiling & Search (`tiling.py`, `search.py`, `http.py`)**:
   - Generates Google Maps Protobuf search requests with custom viewport spans.
   - Decomposes bounding boxes into recursive 4-quadrant spatial tiles to bypass Google's 20-place result cap per request.
   - Deduplicates places immediately via Google's 64-bit Customer Identification number (`cid`).
2. **Place Details & Review Scraping (`details.py`, `reviews.py`, `photos.py`)**:
   - Extracts complete operating hours, weekly schedules, accessibility attributes, secondary categories, and thumbnail photos.
   - Paginates Google Boq Proxy review streams, capturing reviewer profile links, ratings, timestamps, review text, and owner responses.
3. **Official Website Discovery & Multi-Signal Verification (`website/discovery.py`, `website/verifier.py`, `website/ssrf.py`)**:
   - Discovers candidate URLs for businesses lacking web links.
   - Applies multi-hop SSRF validation to block loopback addresses (`127.0.0.1`), private networks (`10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`), link-local IPs (`169.254.169.254`), and non-HTTP schemes.
   - Evaluates tokenized name overlap, HTML title tags, meta descriptions, and headers to classify candidate domains as `verified`, `needs_review`, `failed`, or `website_missing`.
4. **Safe Website Crawling & Company Profile Extraction (`website/crawler.py`, `website/extractor.py`)**:
   - Traverses verified public company websites up to a bounded limit (default: 5 pages per site).
   - Strictly enforces same-domain crawling prioritizing `/about`, `/team`, `/leadership`, `/services`, `/locations`, and `/contact`.
   - Parses Schema.org JSON-LD structured blocks (`Person`, `Organization`, `LocalBusiness`) and extracts public executive leaders and verified contact channels.
5. **Incremental Intelligence & Provenance Engine (`incremental/`)**:
   - Ingests candidates from modular adapters (Company Websites, State Registries, Professional Profiles, Open Sources).
   - Normalizes identifiers (phones, URLs, names) and performs non-destructive candidate comparison (`EXACT_MATCH`, `NEW_INFORMATION`, `POSSIBLE_MATCH`, `CONFLICT`).
   - Persists all observations in `additional_intelligence` and `intelligence_sources` tables with access timestamps and URL provenance.
6. **Regional Storage & Export Management (`storage/`, `export.py`)**:
   - Maintains the central SQLite repository (`data/gmaps.sqlite`) as single source of truth while synchronizing partitioned regional databases (`data/databases/US/California_CA.sqlite`).
   - Produces clean CSV, JSONL, and Apache Parquet export packages partitioned by date and jurisdiction.
7. **Pipeline Orchestration & Web Dashboard (`orchestrator.py`, `web/`)**:
   - Coordinates automated multi-stage pipeline runs with pause, resume, cancel, and retry capabilities.
   - Exposes a responsive, zero-CDN dark-mode web application on `127.0.0.1:8765` featuring real-time Server-Sent Events (SSE), offline `<canvas>` spatial maps, and data inspection drawers.

---

## 5. Expected System Inputs and Outputs

### Inputs
- **Search Queries**: Target category keywords (e.g., `"hospital"`, `"coffee"`, `"dentist"`) or natural language strings (e.g., `"coffee shops in Seattle"`).
- **Geographic Bounding Boxes**: Coordinates defined as `lat,lng,span_deg` or administrative state codes (e.g., `US:CA`, `MX:JAL`).
- **Configuration Files**: YAML-based definitions in [`config/default.yaml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/config/default.yaml), [`config/pipeline2_regions.yaml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/config/pipeline2_regions.yaml), and user overrides in `config/user.yaml`.

### Outputs
- **Central SQLite Database**: [`data/gmaps.sqlite`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/data/gmaps.sqlite) containing 21 normalized tables, indexed via B-trees and FTS5 triggers.
- **Regional SQLite Databases**: Dedicated partitioned databases under `data/databases/{country}/{state}_{code}.sqlite`.
- **Export Data Bundles**: Formatted CSV, JSON Lines (JSONL), and Parquet files stored under `data/exports/{YYYY-MM-DD}/{country}/{region}/`.
- **Local Photo Storage**: Downloaded place thumbnails and reviewer photos stored under `data/photos/{cid}/{photo_id}.jpg`.
- **Structured Logs**: File-based rotating execution logs in `logs/gmaps.log` and task checkpoint records.

---

## 6. Implementation Status: Completed vs. Incomplete Features

| Component / Feature | Implementation Status | Ground Truth Location / Notes |
| :--- | :--- | :--- |
| Google Maps Protobuf RPC Scraper | **Fully Implemented** | [`src/gmaps_scraper/search.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/search.py), [`http.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/http.py) |
| 4-Quadrant Recursive Spatial Tiling | **Fully Implemented** | [`src/gmaps_scraper/tiling.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/tiling.py) |
| CID Deduplication & COALESCE Upsert | **Fully Implemented** | [`src/gmaps_scraper/db.py:upsert_place_stub`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/db.py#L415) |
| Reviews & Place Details RPC Extraction | **Fully Implemented** | [`src/gmaps_scraper/details.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/details.py), [`reviews.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/reviews.py) |
| US (50) & MX (32) Geographic Scope | **Fully Implemented** | [`config/pipeline2_regions.yaml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/config/pipeline2_regions.yaml), [`scope.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/scope.py) |
| Multi-Hop SSRF Network Guard | **Fully Implemented** | [`src/gmaps_scraper/website/ssrf.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/website/ssrf.py) |
| Website Discovery & Multi-Signal Verification | **Fully Implemented** | [`src/gmaps_scraper/website/discovery.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/website/discovery.py), [`verifier.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/website/verifier.py) |
| Bounded Website Crawler & JSON-LD Extractor | **Fully Implemented** | [`src/gmaps_scraper/website/crawler.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/website/crawler.py), [`extractor.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/website/extractor.py) |
| Incremental Intelligence Comparison Engine | **Fully Implemented** | [`src/gmaps_scraper/incremental/`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/incremental/) |
| Regional Storage Manager (Sync & Partitioning) | **Fully Implemented** | [`src/gmaps_scraper/storage/manager.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/storage/manager.py) |
| Pipeline Orchestrator (Pause, Resume, Cancel) | **Fully Implemented** | [`src/gmaps_scraper/orchestrator.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/orchestrator.py) |
| FastAPI Local Web Dashboard & Canvas Maps | **Fully Implemented** | [`src/gmaps_scraper/web/app.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/web/app.py) |
| Comprehensive Pytest Test Suite (188 tests) | **Fully Implemented** | [`tests/`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/tests/) (100% pass rate: 188 passed) |
| Playwright Headless Browser Fallback | **Stub Only** | [`src/gmaps_scraper/fallback_browser.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/fallback_browser.py) (contains stub placeholder, not required for HTTP pipeline) |
| Cloud Scraping API Integration | **Intentionally Omitted** | Zero external scraping APIs by design. |
| Machine Learning / LLM Models | **Intentionally Omitted** | System relies strictly on deterministic rule-based algorithms. |
