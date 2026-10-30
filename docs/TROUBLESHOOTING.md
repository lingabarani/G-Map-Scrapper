# Operational Troubleshooting Guide (`TROUBLESHOOTING.md`)

This guide provides actionable diagnosis and recovery steps for common operational issues, grounded in the actual codebase, error handlers, and database constraints.

---

## 1. Google Maps Search & RPC Failures

### 1.1 Anti-XSSI Security Prefix & JSON Decoding Errors
- **Symptoms**: `json.decoder.JSONDecodeError` or `ValueError: Unexpected response format from Google Maps search RPC`.
- **Root Cause**: Google Maps endpoints prepend an anti-XSSI token `)]}'\n` before the JSON payload. If network interception or proxy rewriting modifies this header, standard JSON decoding fails.
- **Diagnostic Command**:
  ```bash
  python -m gmaps_scraper search --query "test" --bbox "40.70,-74.02,0.01"
  ```
  Check the raw capture in `data/raw/` or `logs/gmaps.log`.
- **Safe Recovery Steps**:
  1. Verify network connectivity without transparent proxy interference.
  2. Inspect [`src/gmaps_scraper/search.py:parse_search_rpc_response`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/search.py) to ensure the `)]}'\n` stripping regex is active.
  3. Re-run the command; transient Google captcha challenges typically resolve or trigger exponential backoff automatically.

---

### 1.2 Rate Limiting (HTTP 429) & Connection Timeouts
- **Symptoms**: `httpx.HTTPStatusError: 429 Too Many Requests` or repeated `ReadTimeout` logs.
- **Root Cause**: Outbound request frequency exceeds Google's transient threshold or the target company server's rate limit.
- **Diagnostic Command**:
  Check recent error frequency in the logs:
  ```bash
  grep -i "429" logs/gmaps.log
  ```
- **Safe Recovery Steps**:
  1. Increase retry backoff and lower per-host rate in `config/user.yaml`:
     ```yaml
     per_host_rate: 1.0       # Lower from 2.0 to 1.0 req/sec
     backoff_base: 2.0        # Increase exponential backoff multiplier
     max_retries: 5           # Increase retry allowance
     ```
  2. If using residential proxies, populate the `proxies` array in `config/user.yaml`.

---

## 2. SSRF Guardrail & Website Verification Issues

### 2.1 Candidate URL Blocked with `SSRFBlockedError`
- **Symptoms**: Log entry `[WARNING] SSRF check failed: Host resolves to private/loopback IP`.
- **Root Cause**: The discovered candidate website or one of its HTTP redirects resolved to a loopback address (`127.0.0.1`), RFC 1918 private subnet (`192.168.x.x`, `10.x.x.x`), or cloud metadata IP (`169.254.169.254`).
- **Diagnostic Command**:
  Check SSRF resolution manually:
  ```python
  import socket
  print(socket.getaddrinfo("candidate-domain.com", 80))
  ```
- **Safe Recovery Steps**:
  - **Do NOT disable SSRF checks**. The SSRF guard is functioning as designed by preventing the scraper from probing internal networks or cloud infrastructure.
  - The scraper automatically flags these candidates as `failed` and records the error in `places.website_error`.

---

### 2.2 Legitimate Website Marked as `needs_review` or `failed`
- **Symptoms**: Business website exists, but `website_status` in SQLite is set to `needs_review` ($0.35 \le S < 0.65$) or `failed` ($S < 0.35$).
- **Root Cause**: The business trade name listed on Google Maps significantly differs from the website `<title>` or domain tokens (e.g., Google Maps lists "Dr. John Doe DDS", but the website domain is "DowntownSmilesDental.com").
- **Diagnostic Command**:
  Inspect the recorded JSON evidence:
  ```sql
  SELECT cid, name, website, website_status, website_evidence 
  FROM places 
  WHERE website_status IN ('needs_review', 'failed') 
  LIMIT 5;
  ```
- **Safe Recovery Steps**:
  1. If operator inspection confirms domain ownership, manually update the record:
     ```sql
     UPDATE places 
     SET website_status = 'verified', official_website_url = website 
     WHERE cid = 'YOUR_CID';
     ```
  2. Run website enrichment for verified places:
     ```bash
     python -m gmaps_scraper website-enrich --country US --region YOUR_STATE
     ```

---

## 3. Database & Concurrency Issues

### 3.1 `sqlite3.OperationalError: database is locked`
- **Symptoms**: Worker task aborts with `sqlite3.OperationalError: database is locked`.
- **Root Cause**: Multiple concurrent worker processes attempted simultaneous SQLite write transactions while Write-Ahead Logging (WAL) was disabled or busy timeout was exceeded.
- **Diagnostic Command**:
  Check SQLite journal mode:
  ```bash
  sqlite3 data/gmaps.sqlite "PRAGMA journal_mode;"
  ```
  Expected output: `wal`.
- **Safe Recovery Steps**:
  1. Ensure WAL mode is active:
     ```bash
     sqlite3 data/gmaps.sqlite "PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;"
     ```
  2. Reduce `category_workers` or `concurrency` in `config/user.yaml` if executing on resource-constrained hardware.
  3. Ensure all Python processes access the database via the thread-safe `Database` connection context manager (`with db.get_connection() as conn:`).

---

### 3.2 Interrupted Run or Checkpoint Desynchronization
- **Symptoms**: Pipeline run unexpectedly terminated or stopped responding during long execution.
- **Root Cause**: Host power interruption, terminal closure, or system SIGKILL.
- **Diagnostic Command**:
  Check pipeline run records:
  ```bash
  python -m gmaps_scraper pipeline-status
  ```
  Inspect task states in SQLite:
  ```sql
  SELECT status, count(*) FROM collection_tasks GROUP BY status;
  ```
- **Safe Recovery Steps**:
  1. **Do NOT delete the SQLite database or raw directory.** All completed tiles and places are safely persisted.
  2. Resume the run directly via CLI:
     ```bash
     python -m gmaps_scraper pipeline-resume
     ```
  3. If specific tasks failed due to temporary network failure, trigger retry:
     ```bash
     python -m gmaps_scraper pipeline-retry
     ```

---

## 4. Web Dashboard & Server-Sent Events (SSE) Issues

### 4.1 Dashboard Shows "Stream Disconnected" or HTTP 429
- **Symptoms**: Live progress bar stops updating in browser; console displays `EventSource failed loading: GET http://127.0.0.1:8765/api/live/stream`.
- **Root Cause**: The web application enforces `MAX_SSE_CONNECTIONS = 10`. If more than 10 browser tabs are opened simultaneously, additional streams are rejected with HTTP 429.
- **Diagnostic Command**:
  Check open browser tabs or test SSE connectivity via curl:
  ```bash
  curl -N "http://127.0.0.1:8765/api/pipeline/events"
  ```
- **Safe Recovery Steps**:
  1. Close duplicate dashboard browser tabs.
  2. The frontend automatically reconnects with exponential backoff and replays historical events from SQLite upon reconnection.

---

### 4.2 Web Dashboard Port Collision (`Errno 98 / 10048`)
- **Symptoms**: `OSError: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8765)`.
- **Root Cause**: An existing instance of `gmaps-scraper serve` or another application is already bound to port 8765.
- **Diagnostic Command**:
  Windows (PowerShell):
  ```powershell
  Get-NetTCPConnection -LocalPort 8765 | Select-Object OwningProcess, State
  ```
  Linux / macOS:
  ```bash
  lsof -i :8765
  ```
- **Safe Recovery Steps**:
  1. Terminate the existing process, or
  2. Launch the dashboard on an alternate port:
     ```bash
     python -m gmaps_scraper serve --port 8790
     ```
