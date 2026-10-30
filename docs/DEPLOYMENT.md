# Deployment & Operations Guide (`DEPLOYMENT.md`)

## 1. Supported Runtime & Operating Environments

The `gmaps-scraper` application is engineered for **local-first bare-metal execution** across modern desktop and server operating systems:

- **Operating Systems**:
  - Windows 10 / Windows 11 / Windows Server (Confirmed & Tested on Windows 11).
  - Ubuntu Linux (20.04 LTS / 22.04 LTS / 24.04 LTS), Debian 11/12.
  - macOS (Monterey 12+ on Apple Silicon & Intel).
- **Python Version**: Python `3.10` or higher (verified on Python `3.12.10`).
- **Disk Storage**: Solid State Drive (SSD) highly recommended due to high SQLite Write-Ahead Logging (WAL) input/output operations.
- **Memory Requirements**: Minimum 2 GB RAM; 4 GB+ recommended for large-scale geographic spatial grid tiling and multi-worker runs.

---

## 2. Installation & Local Environment Setup

### 2.1 Step 1: Clone Repository & Create Virtual Environment

```bash
# Navigate to project directory
cd "r:/Roshan Onedata/Gmap scarp/gmaps-scraper"

# Create a dedicated Python virtual environment
python -m venv .venv

# Activate the virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (cmd.exe):
.venv\Scripts\activate.bat
# Linux / macOS (bash):
source .venv/bin/activate
```

### 2.2 Step 2: Install Dependencies
Install declared dependencies from `requirements.txt`:
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Verify package installation:
```bash
python -m gmaps_scraper --help
```

---

## 3. Database Initialization & Configuration

### 3.1 Initialize Central SQLite Repository
Before launching scrapers or the web application, initialize the SQLite schema and directory tree:
```bash
python -m gmaps_scraper init
```

This command creates:
- The central database at [`data/gmaps.sqlite`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/data/gmaps.sqlite) with WAL mode enabled.
- All 21 normalized relational tables and FTS5 search triggers.
- Required storage directories: `data/raw/`, `data/photos/`, `data/databases/`, `data/exports/`, and `logs/`.

### 3.2 Configuration Tuning (`config/user.yaml`)
To override default parameters without altering repository defaults, create `config/user.yaml`:

```yaml
# Concurrency tuning
concurrency: 5
category_workers: 3
all_categories_concurrent: false
request_concurrency: 3
per_host_rate: 2.0

# Network politeness & retries
max_retries: 3
backoff_base: 1.5

# Spatial tiling depth
tile_base_zoom: 15
tile_max_depth: 3
tile_overlap_pct: 10

# Media
download_photos: true
photo_max_width: 800

# Web Dashboard
web_port: 8765
web_host: "127.0.0.1"
```

---

## 4. Startup & Execution Commands

### 4.1 Launching the Web Dashboard
Start the local FastAPI dashboard and REST API on `127.0.0.1:8765`:
```bash
python -m gmaps_scraper serve --port 8765 --host 127.0.0.1
```
Open your browser to `http://127.0.0.1:8765` to monitor tasks, explore collected businesses, and inspect spatial `<canvas>` maps.

### 4.2 Executing Automated Regional Pipelines via CLI
To plan and execute automated multi-stage pipeline runs:

#### 1. Preview Execution Plan
```bash
python -m gmaps_scraper pipeline-plan --country US --regions CA,NY --categories healthcare,food
```

#### 2. Execute Automated Pipeline Run
```bash
python -m gmaps_scraper pipeline-run --country US --regions CA --categories healthcare --workers 3
```

#### 3. Monitor Pipeline Status
```bash
python -m gmaps_scraper pipeline-status
```

### 4.3 Running Single-Stage CLI Commands

#### Ingest Places via Coordinate Bounding Box
```bash
python -m gmaps_scraper search --query "coffee" --bbox "40.70,-74.02,0.02"
```

#### Deep Enrich Place Hours & Attributes
```bash
python -m gmaps_scraper enrich --limit 50
```

#### Harvest Customer Reviews
```bash
python -m gmaps_scraper reviews --limit 50 --max-per-place 100
```

#### Run Official Website Discovery & Multi-Signal Verification
```bash
python -m gmaps_scraper website-discover --country US --region CA --batch-size 50
```

#### Crawl Verified Websites for Corporate Intelligence
```bash
python -m gmaps_scraper website-enrich --country US --region CA --batch-size 50 --max-pages 5
```

#### Run Incremental Candidate Comparison
```bash
python -m gmaps_scraper incremental-enrich --country US --region CA --batch-size 50
```

---

## 5. Graceful Shutdown & Recovery Procedures

### 5.1 Graceful Interruption (`SIGINT`)
The application hooks `SIGINT` (Ctrl+C). When triggered, running workers commit in-flight database transactions, flush write buffers, and exit cleanly without corrupting the SQLite database.

### 5.2 Resuming Interrupted Runs
If a job or pipeline run was interrupted by a power failure or system restart, resume execution:
```bash
python -m gmaps_scraper resume
```
Or resume a specific orchestrator run:
```bash
python -m gmaps_scraper pipeline-resume --run-id <RUN_ID>
```

Already completed tiles and verified businesses are bypassed immediately.

---

## 6. Storage Management & Backup Procedures

### 6.1 Synchronize State-Wise Partitioned Databases
To partition the central database into regional databases (`data/databases/US/California_CA.sqlite`):
```bash
python -m gmaps_scraper storage-sync --country US --regions CA
```

### 6.2 Check Storage Status
```bash
python -m gmaps_scraper storage-status
```

### 6.3 Backup Procedures
To generate a point-in-time snapshot backup of the database:
```bash
python -m gmaps_scraper storage-backup
```
Manual backup can also be performed safely using the SQLite backup command while the server is running:
```bash
sqlite3 data/gmaps.sqlite ".backup 'data/backup_gmaps_$(date +%Y%m%d_%H%M%S).sqlite'"
```

---

## 7. Operational Best Practices & Untested Approaches

### Production Best Practices (Verified)
1. **Always Bind Dashboard to Localhost**: Never bind `web_host` to `0.0.0.0` unless protected by a reverse proxy with mutual TLS / basic authentication.
2. **Observe Rate Limiting**: Keep `per_host_rate` at `2.0` req/sec or lower to avoid triggering Google Maps HTTP 429 rate limit thresholds.
3. **Dedicated Virtual Environments**: Maintain isolated Python virtual environments to prevent package version conflicts.

### Untested Approaches (Clearly Labeled)
- **Docker Containerization**: No `Dockerfile` or `docker-compose.yml` is provided in the repository. Containerized execution is *untested* and may require configuring volume permissions for SQLite WAL locking and container network bridging for DNS resolution.
- **Headless Browser Scraping**: [`fallback_browser.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/fallback_browser.py) is an uninstantiated stub; do *not* deploy Playwright dependencies unless you implement this module.
- **Distributed Multiprocessing**: Multi-machine clustering is *not supported*; the project is architected for local single-node multi-threaded asynchronous execution.
