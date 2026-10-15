# Google Maps Local Scraper & Web Dashboard

A 100% LOCAL, production-grade Google Maps discovery engine, data extractor, and analytical dashboard.

Built from first principles with **zero cloud APIs**, **zero third-party scrapers (no SerpApi, Apify, ScraperAPI)**, **zero API keys**, **zero CDNs**, and **zero external map tiles**. All scraped places, hours, attributes, reviews, and photos reside completely inside your local filesystem and SQLite database.

---

## Key Features & Constraints

1. **100% Local Execution**: The server binds exclusively to `127.0.0.1`. No telemetry, no external authentication providers, and zero cloud services.
2. **Zero CDNs**: All stylesheets, JavaScript, and typography use locally vendored assets and modern system font stacks. Works flawlessly with Wi-Fi disabled.
3. **Offline Canvas Spatial Map**: The coordinate map is rendered using an HTML5 `<canvas>` coordinate scatter plot with smooth drag-to-pan, scroll-wheel zoom, and clickable place markers. No external tile servers or internet connection required.
4. **Sub-500ms Server-Side Paginated Table**: Powered by indexed SQLite queries and FTS5 full-text search, keeping query times under 500ms even across 100,000+ rows.
5. **Keyboard-First Navigation**: Dense, modern dark interface featuring keyboard shortcuts (`j`/`k` row selection, `/` search focus, `Enter` inspect drawer, `Esc` dismiss).
6. **XSS Proofing**: All user-supplied fields are strictly HTML-escaped across views and detail drawers.

---

## Directory Structure

```
gmaps-scraper/
├── config/
│   └── default.yaml         # Default scraper parameters and rates
├── data/
│   ├── gmaps.sqlite         # SQLite database with WAL and FTS5
│   └── photos/              # Local photo storage organized by CID
├── docs/
│   └── FIELD_MAP.md         # Field mapping documentation & RPC paths
├── src/
│   └── gmaps_scraper/
│       ├── __init__.py
│       ├── __main__.py      # Module entry point for python -m gmaps_scraper
│       ├── cli.py           # Typer CLI application
│       ├── config.py        # Pydantic settings management
│       ├── db.py            # SQLite schema, queries, quality reports
│       ├── http.py          # Resilient HTTP client with rate limiting
│       ├── tiling.py        # 4-quadrant recursive spatial grid tiling
│       ├── search.py        # Search RPC Protobuf builder and parser
│       ├── details.py       # Place details extractor (hours, amenities)
│       ├── reviews.py       # UGC BOQ review pagination extractor
│       ├── photos.py        # Async photo downloader
│       ├── export.py        # CSV, JSONL, Parquet exporter
│       ├── checkpoint.py    # Run state and ETA tracker
│       └── web/
│           ├── app.py       # FastAPI application and JSON endpoints
│           ├── demo_data.py # Sample database seeder
│           ├── templates/   # Jinja2 templates (dashboard, places, map, runs, quality)
│           └── static/
│               ├── css/style.css
│               └── js/      # Offline canvas charts, canvas map, keyboard controls
└── tests/                   # Pytest suite with real network fixtures
```

---

## Setup & Installation

Clone or navigate into the project directory:

```powershell
cd "gmaps-scraper"
```

Install requirements into your Python environment (Python 3.10+):

```powershell
pip install -r requirements.txt
pip install -e .
```

---

## Launching the Local Web Dashboard

### Instant Demo Mode (Populated with Sample Data)
If starting on a clean machine or empty database, pass `--demo` to seed sample records immediately:

```powershell
python -m gmaps_scraper serve --port 8765 --demo
```

### Standard Local Server
```powershell
python -m gmaps_scraper serve --port 8765
```

Navigate to [http://127.0.0.1:8765](http://127.0.0.1:8765) in any browser.

---

## Web Dashboard Views

| Route | View | Description |
|---|---|---|
| `/` | **Dashboard** | KPI cards (total, enriched, reviews, photos, failed, last run), rating distribution histogram (`<canvas>`), top-20 categories chart, and pipeline status counters. |
| `/places` | **Places Explorer** | Server-side paginated table with full-text search (`name`, `address`, `phone`, `website`). Filters for status, category, min rating, min reviews, phone/website presence, local photos, and claimed status. Click any row or press `Enter` to slide out the detail drawer. Includes an **Export Current Filter** button. |
| `/places/{cid}` | **Place Details** | Complete field inspection: hours table, attributes & features, hourly popular-times chart, paginated customer reviews, and local photos served from disk. |
| `/map` | **Coordinate Map** | 100% offline HTML5 Canvas scatter plot displaying places by latitude/longitude. Drag to pan, scroll to zoom, click pins to inspect in drawer. Optional online OpenStreetMap toggle clearly isolated. |
| `/runs` | **Runs & Logs** | Trigger discovery, enrichment, reviews, photos, or full pipeline directly from the UI. Displays active step progress, counters, ETA estimation, and live streaming log tail. |
| `/quality` | **Quality Audit** | Automated data hygiene audit displaying per-column null counts, duplicate detection (by CID and by name + address collisions), coordinate anomalies (e.g. `(0,0)` outliers), failed records, and a one-click **Retry Failed** button. |

---

## Keyboard Shortcuts

The dashboard is designed for rapid, mouse-free data curation:

- <kbd>/</kbd> — Focus the global search bar
- <kbd>j</kbd> — Move selection down one row in the places table
- <kbd>k</kbd> — Move selection up one row in the places table
- <kbd>Enter</kbd> — Open the slide-in detail drawer for the selected place
- <kbd>Esc</kbd> — Close the detail drawer or clear focus

---

## JSON API Reference

All backend functionality is available programmatically via local JSON endpoints under `/api`:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/stats` | Aggregated dataset metrics, rating distribution, and top categories |
| `GET` | `/api/places` | Paginated places search with query, filter, and sorting parameters |
| `GET` | `/api/places/{cid}` | Complete structured place details including hours and attributes |
| `GET` | `/api/places/{cid}/reviews` | Paginated reviews with author, rating, text, and owner response |
| `GET` | `/api/places/{cid}/photos` | Local photo records for a given place |
| `GET` | `/api/map` | Compact coordinates `[{cid, lat, lng, name, rating, category}]` for canvas rendering |
| `GET` | `/api/progress` | Current pipeline execution state, counters, and ETA |
| `GET` | `/api/logs?tail=200` | Tail of the local execution log file |
| `POST` | `/api/run/{action}` | Trigger pipeline actions (`search`, `enrich`, `reviews`, `photos`, `export`, `full`) |
| `POST` | `/api/resume` | Resume incomplete tasks from previous runs |
| `POST` | `/api/retry-failed` | Reset failed places (`status = 'failed'`) to `pending` status |
| `GET` | `/api/export?format=csv` | Filtered export in CSV or JSONL format (byte-identical to CLI export) |
| `GET` | `/media/photos/{cid}/{file}` | Clean local image serving directly from disk with proper MIME types |

---

## CLI Commands

You can run individual pipeline stages or the full end-to-end pipeline directly from the command line:

```powershell
# 1. Initialize SQLite schema and directories (7 tables + FTS5 index)
python -m gmaps_scraper init

# 2. Search discovery tile grid with recursive subdivision
python -m gmaps_scraper search --query "coffee" --bbox "40.7128,-74.0060,0.01"

# 3. Enrich places with hours, attributes, popular times, and photos
python -m gmaps_scraper enrich --limit 50

# 4. Fetch customer reviews (BOQ pagination loop)
python -m gmaps_scraper reviews --max-per-place 50 --limit-places 50

# 5. Download photos to local disk (organizes into data/photos/{cid}/ with manifests)
python -m gmaps_scraper photos --limit 20

# 6. Export data to files (csv, jsonl, or parquet)
python -m gmaps_scraper export --format csv
python -m gmaps_scraper export --format jsonl
python -m gmaps_scraper export --format parquet

# 7. Print local database statistics, status breakdown, and last run
python -m gmaps_scraper stats

# 8. Resume incomplete tasks after interruption
python -m gmaps_scraper resume

# 9. Full end-to-end pipeline in one command
python -m gmaps_scraper run --query "coffee" --bbox "40.7128,-74.0060,0.01" --max-reviews 50

# 10. Start 100% local web dashboard on 127.0.0.1
python -m gmaps_scraper serve --port 8765 --demo
```

---

## Configuration Reference (`config/default.yaml` & `config/user.yaml`)

| Key | Type | Default | Description |
|---|---|---|---|
| `concurrency` | int | `4` | Max concurrent async workers |
| `per_host_rate` | float | `2.0` | Requests per second limit per host |
| `max_retries` | int | `4` | Retry attempts on 429/5xx before failing |
| `backoff_base` | float | `2.0` | Exponential backoff multiplier base |
| `tile_base_zoom` | int | `15` | Default spatial zoom level |
| `tile_max_depth` | int | `3` | Maximum recursive tile subdivision depth |
| `tile_overlap_pct`| float | `0.1` | Fractional quadrant boundary overlap |
| `max_reviews_per_place` | int | `100` | Max customer reviews to paginate per place |
| `download_photos` | bool | `true` | Whether to download images locally to disk |
| `photo_max_width` | int | `1200` | Max width parameter for downloaded photos |
| `user_agent` | str | *(Chrome 122)* | Realistic User-Agent header string |
| `accept_language` | str | `en-US,en;q=0.9` | Accept-Language header string |
| `hl` | str | `en` | Google Maps host language code |
| `gl` | str | `us` | Google Maps geographic region code |
| `proxies` | list | `[]` | Optional list of HTTP/SOCKS5 proxies for rotation |
| `headless_fallback` | bool | `false` | Enable Playwright fallback for tricky challenges |

---

---

## Business Vertical Taxonomy & Country Lookup

### 1. Vertical Taxonomy (`config/verticals.yaml`)
Normalizes raw Google categories into high-level business verticals using case-insensitive keyword matching with ordered evaluation (first match wins):

- `healthcare`: hospital, clinic, medical, doctor, physician, urgent care, dentist, dental, pharmacy, optometrist, chiropractor, physical therapy, diagnostic, laboratory, health
- `hospitality`: hotel, motel, hostel, resort, guest house, bed and breakfast
- `food`: restaurant, cafe, coffee, bakery, bar, pub, pizza, fast food, deli, diner, ice cream, food court, juice, tea house
- `retail`: store, shop, boutique, supermarket, grocery, convenience, mall, market, pharmacy retail, clothing, electronics
- `fitness`: gym, fitness, yoga, pilates, crossfit, sports centre, swimming
- `beauty`: salon, barber, spa, nail, hair, tattoo, beauty
- `automotive`: car repair, auto, garage, petrol, gas station, car wash, dealership
- `finance`: bank, atm, credit union, insurance, financial
- `education`: school, college, university, kindergarten, academy, tuition, library
- `government`: government, city hall, post office, embassy, courthouse, police, fire station
- `real_estate`: real estate, property, estate agent
- `other`: fallback when no keywords match

#### How to Extend Taxonomy
Edit `config/verticals.yaml`. Add a new vertical key with `label` and `keywords` list. Then run:
```powershell
python -m gmaps_scraper reclassify
```
All existing records are reclassified idempotently against the updated taxonomy.

### 2. Local Country Resolution (`config/countries.yaml`)
Resolves addresses strictly locally with zero external network or geocoding API calls.
The last comma-separated address component is stripped of postal codes and matched against `config/countries.yaml` (supporting ISO 3166-1 alpha-2 codes and comprehensive aliases). If unresolvable, sets `country = "unknown"`, `country_code = "unknown"` without guessing.

---

## CLI Commands

| Command | Usage | Description |
|---|---|---|
| `init` | `python -m gmaps_scraper init` | Initializes directories, SQLite tables, triggers, and runs idempotent migrations. |
| `reclassify` | `python -m gmaps_scraper reclassify` | Re-derives `vertical`, `country`, `city`, `region`, `postal_code` for all existing rows idempotently. |
| `search` | `python -m gmaps_scraper search -q "coffee" -b "40.70,-74.02,0.02"` | Recursive quadtree tiling discovery and place stub collection. |
| `enrich` | `python -m gmaps_scraper enrich -l 100` | Details enrichment: operating hours, attributes, popular times. |
| `reviews` | `python -m gmaps_scraper reviews --max-per-place 50` | BOQ review pagination. |
| `photos` | `python -m gmaps_scraper photos -l 50` | Downloads photo files to disk with magic byte verification. |
| `export` | `python -m gmaps_scraper export -f csv [--full]` | Exports data to CSV/JSONL/Parquet. `--full` emits all 29 schema columns. |
| `serve` | `python -m gmaps_scraper serve --port 8765 [--demo]` | Launches local FastAPI web dashboard on `127.0.0.1`. |

---

## API Endpoints

### List & Filtering Endpoints (Support all composable filter params)
Filter parameters supported across `/api/places`, `/api/map`, `/api/export`, and HTML `/places`:
- `vertical` (repeatable / comma-separated list, e.g. `?vertical=healthcare,food`)
- `country` (repeatable / comma-separated list, accepts ISO alpha-2 or country name)
- `city` (repeatable / comma-separated list)
- `min_rating` (float, e.g. `4.0`)
- `min_reviews` (int, e.g. `50`)
- `has_phone`, `has_website`, `has_photos`, `claimed` (bool)
- `has_reviews`, `has_hours`, `has_attributes` (bool)

### New Analytical Endpoints
- `GET /api/verticals`: Live facet counts for business verticals respecting other active filters (`[{key, label, count}]`).
- `GET /api/countries`: Live facet counts for countries respecting other active filters (`[{code, name, count}]`).
- `GET /api/cities?country=`: Live facet counts for cities in the selected country (`[{city, count}]`).
- `GET /api/places/{cid}/full`: Complete JSON document containing every column of `places` plus `hours`, `attributes`, `reviews`, `photos`, and `popular_times`.
- `GET /api/export?format=csv|jsonl|parquet&full=true`: Full data export emitting all 29 schema columns.

---

## Output Schema

### 1. `places` (29 Columns)
- `cid`: Unique 64-bit Customer ID string (Primary Key)
- `ftid`: Feature ID string
- `place_id`: Google Place ID string
- `name`: Business / establishment name
- `address`: Clean street address
- `street_address`: Street name and number
- `latitude`, `longitude`: Decimal WGS-84 coordinates
- `website`: Official website URL
- `phone`, `phone_e164`: Local and E.164 phone formats
- `category`: Primary Google business category
- `vertical`: Normalized business vertical (e.g. `healthcare`, `food`, `retail`)
- `vertical_raw`: Original Google primary category, untouched
- `country`: Full country name (e.g. `United States`)
- `country_code`: ISO-3166 alpha-2 country code (e.g. `US`)
- `region`: State / province abbreviation or name (e.g. `NY`)
- `city`: City / locality name (e.g. `New York`)
- `postal_code`: Extracted postal / zip code (e.g. `10004`)
- `rating`: Float rating in [1.0, 5.0]
- `review_count`: Total advertised reviews count
- `timezone`: Local IANA timezone identifier
- `claimed_status`: 1 if verified owner, 0 if unclaimed
- `photo_thumbnail`: Primary photo thumbnail URL
- `status`: Enum: `pending`, `details_done`, `reviews_done`, `complete`, `failed`
- `attempts`: Processing attempt counter
- `error`: Last processing error description
- `created_at`: Initial discovery timestamp
- `updated_at`: Last update timestamp

### 2. Lookup Tables
- `verticals`: `(key TEXT PRIMARY KEY, label TEXT)`
- `countries`: `(code TEXT PRIMARY KEY, name TEXT)`

### 3. `hours`
- `cid`: Foreign key to `places.cid`
- `day`: Day index (1 = Monday .. 7 = Sunday)
- `day_name`: Day name string
- `formatted_text`: Opening hours string
- `is_closed`: 1 if closed, 0 if open

### 4. `attributes`
- `cid`: Foreign key to `places.cid`
- `group_id`, `group_name`: Attribute category (e.g. `Accessibility`, `Amenities`)
- `attribute_id`, `attribute_name`: Specific amenity tag
- `is_enabled`: 1 if available, 0 if not
- `display_value`: Formatted display text

### 5. `reviews`
- `cid`: Foreign key to `places.cid`
- `review_id`: Unique review ID
- `author_name`: Reviewer name
- `author_id`, `author_avatar_url`, `author_profile_url`: Author metadata
- `rating`: Integer star rating (1-5)
- `relative_date`: String relative age
- `timestamp_ms`: Epoch millisecond timestamp
- `text`: Review text body
- `owner_response_text`, `owner_response_date`: Business owner reply
- `reaction_count`: Helpful vote count
- `photos_json`: Attached review photo URLs

### 6. `photos`
- `cid`: Foreign key to `places.cid`
- `review_id`: Associated review ID (if reviewer photo)
- `photo_url`: Google CDN photo URL
- `local_path`: Relative disk path under `data/photos/{cid}/`
- `file_size`: File size in bytes
- `attribution`: Photographer or owner credit

---

## Troubleshooting

### 1. Consent Redirect (`CONSENT=YES+` / `SOCS`)
- **Symptom**: Requests redirect to `consent.google.com` or return 302.
- **Fix**: The client automatically injects `Cookie: CONSENT=YES+; SOCS=...` in request headers. When Google issues updated Set-Cookie consent headers, the client re-establishes and persists them across the session.

### 2. HTTP 429 Too Many Requests
- **Symptom**: Google responds with HTTP status 429.
- **Fix**: The client incorporates exponential backoff with random jitter (`(backoff_base ** attempts) + uniform(0.5, 1.5)`). You can also lower `concurrency` or reduce `per_host_rate` in `config/default.yaml`.

### 3. Empty Parse / Schema Shift
- **Symptom**: Search results return zero parsed places.
- **Fix**: The parser automatically activates a dual-tier strategy: RPC protobuf parsing first; if empty, it activates the balanced-bracket scanner for `window.APP_INITIALIZATION_STATE` HTML fallback. If Google alters sub-array indexes, `FIELD_MAP.md` documents the exact paths and named exceptions pinpoint missing arrays.

---

## Legal & Ethical Note

This scraper is intended solely for personal data analysis, local archiving, and research purposes. Please scrape responsibly: respect per-host rate limits, avoid abusive request bursts, and adhere to local privacy and copyright laws regarding scraped content.

---

## Offline Verification Step (Zero-CDN Guarantee)

To verify that the application functions 100% offline without external dependencies:

1. Launch the server:
   ```powershell
   python -m gmaps_scraper serve --port 8765 --demo
   ```
2. **Disconnect your machine from the internet** (turn off Wi-Fi or unplug ethernet).
3. Open your browser and navigate to `http://127.0.0.1:8765`.
4. Open Developer Tools (<kbd>F12</kbd>) and check the **Network** tab.
5. Refresh the page and browse across all views (`/`, `/places`, `/places/{cid}`, `/map`, `/runs`, `/quality`).
6. **Result**: All assets (`style.css`, `canvas_charts.js`, `canvas_map.js`, `app.js`) load locally from `127.0.0.1`. No external domain requests (Google Fonts, Cloudflare, unpkg, cdnjs, etc.) are initiated. All charts and the coordinate map render locally on `<canvas>`.

---

---

## On-Demand Live Search & Scraper (`/live`)

The system includes a 100% local, on-demand live search engine that lets you type any business type and any location, resolves geographic boundaries without cloud geocoders, streams places directly into your browser via Server-Sent Events (SSE) as they are scraped from Google Maps, and stores everything in the local SQLite database.

### 1. Zero-API-Key Location Resolver (`src/gmaps_scraper/geo/resolve.py`)
> [!IMPORTANT]
> Location resolution **never uses the Google Geocoding API** or cloud keys. It reuses the exact same internal Google Maps search RPC already employed for scraping, with automatic fallback:
> 1. **Primary**: Protobuf search RPC (`!1d50000...q=<location>`), extracting top entity coordinates and decoded protobuf classification tokens (`p[209]`).
> 2. **Fallback**: `https://www.google.com/maps/place/<location>` reading `window.APP_INITIALIZATION_STATE` coordinates.
> 3. **Manual**: Prompts for coordinate input (`lat,lng@radius`).

Features are classified locally to derive default bounding box spans from `config/geo_spans.yaml`:
- **Country**: `2.0°` span
- **State / Region**: `0.5°` span
- **City / Metro**: `0.10°` span (~11 km)
- **District / Borough / Area**: `0.03°` span (~3.3 km)
- **Street / Address**: `0.005°` span (~500 m)

All resolutions are cached locally in the `geo_cache` table.

### 2. Natural Language Parse (`nlparse.py`)
Enables single-input queries like `"hospitals in Chicago"`, `"bakeries in Lyon, France"`, or `"gyms near 40.75,-73.98"`.
- Splits on the **last** occurrence of `" in "`, `" near "`, `" around "`, `" at "`, `" within "`.
- Cleans `"in the..."` and extracts relative locations.
- Never auto-executes: pre-fills the "What" and "Where" form fields for explicit user confirmation.

### 3. Persistent Job Engine & State Machine (`jobs.py`)
A background worker executes jobs sequentially from a FIFO queue:
`queued` &rarr; `resolving` &rarr; `discovering` &rarr; `enriching` &rarr; `reviews` &rarr; `photos` &rarr; `done` (or `cancelled` / `failed`).
- **Deduplication**: Globally dedupes places against existing CIDs in SQLite. Existing places with `reviews_done` or `complete` are skipped unless `force_refresh=true`.
- **Cooperative Cancellation**: Checkpoints flags between every tile, review page, and photo. On cancel, flushes partial records and marks status as `cancelled`.
- **Restart Resilience**: If the server process restarts mid-job, pending/active jobs are marked `interrupted` and can be resumed via `/api/live/retry/{id}`.

### 4. Streaming API & Guardrails
- `POST /api/live/parse` &rarr; Natural language parse
- `POST /api/live/resolve` &rarr; Resolves location with pre-flight tile & request estimates
- `POST /api/live/start` &rarr; Creates and enqueues job (refuses requests >5,000 without explicit user confirmation)
- `GET /api/live/stream?job_id=` &rarr; Real-time SSE stream (`stage`, `progress`, `place`, `error`, `done`) with 15-second heartbeat comments (`: ping\n\n`) and max 10 concurrent connections. Client disconnection does not abort the scrape job.
- `GET /api/live/job/{id}` &rarr; Job summary and event log history
- `GET /api/live/jobs` &rarr; Recent job history
- `POST /api/live/cancel/{id}` &rarr; Graceful cooperative cancellation
- `POST /api/live/retry/{id}` &rarr; Re-runs an interrupted or failed job

### 5. CLI Commands

```powershell
# Resolve any location name, coordinates, or bbox without API keys
python -m gmaps_scraper resolve --location "Austin, TX"

# Start an on-demand live scrape with live terminal progress stream
python -m gmaps_scraper live --query "hospital" --location "Chicago" --max-places 200 --follow

# List recent jobs
python -m gmaps_scraper jobs

# Cancel a running job
python -m gmaps_scraper jobs --cancel <job_id>
```

---

## Running the Automated Test Suite

Run the full pytest suite:

```powershell
python -m pytest -q
```

All 66 tests validate live geo-resolution, natural language parsing, job state machine, cooperative cancellation, SSE framing, keyset query plans, FastAPI endpoints, resilience retries, XSS protection, and offline asset integrity.

