# API Documentation (`API_DOCUMENTATION.md`)

## 1. Overview & Authentication Model

The `gmaps-scraper` application exposes an internal REST and Server-Sent Events (SSE) API via FastAPI, running on `http://127.0.0.1:8765`. 

### Authentication
- **Internal API**: Operates locally on `127.0.0.1` and requires **no authentication tokens or API keys**. It is designed strictly for local dashboard operations and local programmatic control.
- **External Google Endpoints**: Communicates directly with public Google Maps search and Boq RPC endpoints using session headers (`User-Agent`, `Accept-Language`, `hl`, `gl`). **Zero API keys, cloud subscriptions, or paid credentials** are required.
- **External Target Websites**: Direct, unauthenticated public HTTP/HTTPS GET requests with standard user-agent identification and multi-hop SSRF validation.

---

## 2. Internal REST & SSE Endpoints

### 2.1 Application Statistics & Metadata

#### `GET /api/stats`
Returns aggregated business counts, status breakdowns, and enrichment metrics.

**Response Structure (JSON)**:
```json
{
  "total_places": 1420,
  "pending_places": 35,
  "completed_places": 1385,
  "failed_places": 0,
  "verified_websites": 890,
  "enriched_companies": 450,
  "total_reviews": 12840,
  "total_photos": 2150
}
```

#### `GET /api/verticals`
Returns place counts partitioned by classified business vertical.

**Response Structure (JSON)**:
```json
[
  {"vertical": "food", "label": "Food & Dining", "count": 420},
  {"vertical": "healthcare", "label": "Healthcare & Medical", "count": 210},
  {"vertical": "retail", "label": "Retail & Shopping", "count": 185}
]
```

#### `GET /api/countries` & `GET /api/cities`
Returns geographic distribution summaries.

---

### 2.2 Place Ingestion & Query Endpoints

#### `GET /api/places`
Queries collected business records with rich multi-parameter filtering.

**Query Parameters**:
- `q` (string, optional): Text search query evaluated against FTS5 full-text index.
- `status` (string, optional): Ingestion status (`pending`, `completed`, `failed`).
- `category` (string, optional): Raw Google category filter.
- `vertical` (list[string], optional): Vertical taxonomy filters.
- `country` (list[string], optional): Country code filters (e.g., `US`, `MX`).
- `city` (list[string], optional): City name filters.
- `min_rating` (float, optional): Minimum rating threshold (e.g., `4.0`).
- `min_reviews` (int, optional): Minimum review count threshold (e.g., `50`).
- `has_phone` (bool, optional): Filter by presence of telephone number.
- `has_website` (bool, optional): Filter by presence of website URL.
- `has_photos` (bool, optional): Filter by presence of downloaded photos.
- `claimed` (bool, optional): Filter by Google verified claimed status.
- `page` (int, default: 1): Pagination page number.
- `page_size` (int, default: 50): Number of results per page (max: 200).

**Response Structure (JSON)**:
```json
{
  "page": 1,
  "page_size": 50,
  "total": 1420,
  "items": [
    {
      "cid": "11785376804253324318",
      "name": "787 Coffee",
      "address": "66 Pearl St, New York, NY 10004",
      "street_address": "66 Pearl St, New York, NY 10004",
      "latitude": 40.7034852,
      "longitude": -74.0108313,
      "website": "http://www.787coffee.com/",
      "phone": "(646) 449-9200",
      "category": "Coffee shop",
      "vertical": "food",
      "country_code": "US",
      "region": "NY",
      "city": "New York",
      "rating": 4.8,
      "review_count": 4144,
      "claimed_status": 1,
      "website_status": "verified",
      "enrichment_status": "completed"
    }
  ]
}
```

#### `GET /api/places/{cid}` & `GET /api/places/{cid}/full`
Returns comprehensive entity data for a single business identified by its 64-bit `cid`.

- `/api/places/{cid}`: Core place record.
- `/api/places/{cid}/full`: Deep record joining `places`, `hours`, `attributes`, `company_profiles`, `company_leaders`, `company_contacts`, and `reviews`.

#### `GET /api/places/{cid}/reviews`
Returns extracted reviews for a business entity.

#### `GET /api/places/{cid}/photos`
Returns local image metadata and disk paths for a business entity.

---

### 2.3 Automated Pipeline Orchestration Endpoints

#### `POST /api/pipeline/plan`
Generates a deterministic multi-stage execution plan preview without running tasks.

**Request Body (JSON)**:
```json
{
  "country": "US",
  "regions": ["CA", "NY"],
  "categories": ["food", "healthcare"],
  "category_workers": 3,
  "all_categories_concurrent": false,
  "request_concurrency": 3,
  "stages": {
    "collection": true,
    "discovery": true,
    "enrichment": true
  }
}
```

**Response (JSON)**: Returns summary counts, ordered stage 1 tasks, stage 2 regions, and stage 3 regions.

#### `POST /api/pipeline/start`
Initiates an automated background pipeline run governed by singleton concurrency locking.

#### `POST /api/pipeline/pause`
Pauses the active pipeline run cooperatively after current in-flight HTTP requests complete.

#### `POST /api/pipeline/resume`
Resumes a paused pipeline run.

#### `POST /api/pipeline/cancel`
Cooperatively terminates active tasks and marks the run status as `cancelled`.

#### `POST /api/pipeline/retry-failed`
Resets failed collection tasks and error states to `pending` for re-execution.

#### `GET /api/pipeline/status`
Returns active orchestrator state, active stage, task completion counts, and error summaries.

#### `GET /api/pipeline/events`
Server-Sent Events (SSE) streaming endpoint delivering real-time lifecycle events (`stage_started`, `task_started`, `task_completed`, `task_failed`, `progress`, `run_completed`).

---

### 2.4 Live Search & Interactive Job Endpoints

#### `POST /api/live/parse`
Parses a natural language string into a structured query and location.
- **Request**: `{"text": "dentists in Austin, TX"}`
- **Response**: `{"query": "dentists", "location": "Austin, TX"}`

#### `POST /api/live/resolve`
Resolves a geographic location string into coordinates and bounding box dimensions.

#### `POST /api/live/start`
Launches an interactive live scraping job.
- **Request**: `{"query": "coffee", "location": "Seattle, WA", "lat": 47.6062, "lng": -122.3321, "span_deg": 0.05}`
- **Response**: `{"job_id": "job_1729000000_a1b2c3"}`

#### `GET /api/live/stream?job_id={job_id}`
Server-Sent Events endpoint streaming real-time discovered places, tile progress, and completion events with 15-second heartbeat pings.

---

### 2.5 Incremental Business Intelligence Endpoints

#### `GET /api/intelligence`
Queries findings from `additional_intelligence` filtered by `entity_type`, `field_name`, `match_status`, and `verification_status`.

#### `GET /api/intelligence/summary`
Returns statistical aggregates of findings classified by match status (`EXACT_MATCH`, `NEW_INFORMATION`, `POSSIBLE_MATCH`, `CONFLICT`, `UNVERIFIED`).

#### `GET /api/intelligence/sources`
Returns audit history of external source checks with URL provenance, status, and retrieved timestamps.

#### `POST /api/pipeline/run-incremental-enrichment`
Triggers candidate comparison enrichment for a given country and region.

---

### 2.6 Regional Storage & Data Export Endpoints

#### `GET /api/storage/status`
Returns synchronization status between central SQLite (`data/gmaps.sqlite`) and partitioned regional databases.

#### `GET /api/storage/databases`
Lists all state-wise SQLite database files stored on disk.

#### `GET /api/export`
Streams dynamic filtered exports directly to the browser.
- **Parameters**: `format` (`csv`, `jsonl`, `parquet`), `full` (boolean), plus search filters.

---

## 3. External Google Maps Endpoints

### 3.1 Search & Tiling RPC

- **URL**: `https://www.google.com/search?tbm=map&authuser=0&hl={hl}&gl={gl}&pb={pb}&q={query}`
- **Method**: `GET`
- **Protocol**: HTTP/1.1 or HTTP/2 via HTTPX
- **Key Parameters**:
  - `tbm=map`: Activates Google Maps search handler.
  - `hl`, `gl`: Language and geographic bias (e.g., `en`, `us`).
  - `pb`: URL-encoded Protobuf descriptor encoding viewport center latitude/longitude, tile span, and pagination size (`search.py:build_search_pb`).
  - `q`: Target query string.
- **Response Format**: Text stream prefixed with anti-XSSI security token `)]}'\n` followed by nested multi-dimensional JSON arrays.
- **Parsing**: Prefix stripped; root place entries located at `response[0][1][1:]` with place entity at index `[14]`.

### 3.2 Place Details RPC

- **URL**: `https://www.google.com/search?tbm=map&authuser=0&hl={hl}&gl={gl}&pb={pb_details}&q={place_name}`
- **Method**: `GET`
- **Response Format**: Anti-XSSI prefixed JSON containing structured weekly hours array at index `[203][0]`, business attribute groups at index `[100][1]`, and thumbnail URLs at index `[157]`.

### 3.3 Customer Reviews RPC (Boq Proxy)

- **URL**: `https://www.google.com/httpservice/web/PrivateLocalSearchUiDataService/GetLocalBoqProxy?msc=gwsrpc&reqpld={encoded_json}`
- **Method**: `GET`
- **Key Parameters**:
  - `reqpld`: Base64/URL-encoded JSON parameter containing place feature ID (`ftid`) and continuation page token (`reviews.py:build_boq_reviews_url`).
- **Response Format**: Anti-XSSI prefixed JSON containing reviews array at `response[1][10][2]` and next-page pagination token at `response[1][10][6]`.

---

## 4. Rate Limits, Timeouts & Resilience

| Mechanism | Configuration Key | Default Value | Implementation Details |
| :--- | :--- | :--- | :--- |
| **Per-Host Rate Limit** | `per_host_rate` | `2.0` req/sec | Enforced via `asyncio.sleep` delay tracking in `HttpClient` |
| **Global Concurrency** | `concurrency` | `5` | Governed by `asyncio.Semaphore` |
| **Request Concurrency** | `request_concurrency`| `3` | Maximum concurrent outbound HTTP requests |
| **Request Timeout** | Hardcoded / Config | `15.0` seconds | `httpx.Timeout(15.0, connect=10.0)` |
| **Max Retries** | `max_retries` | `3` attempts | Retry on HTTP 429, 500, 502, 503, 504, or transport timeout |
| **Backoff Multiplier** | `backoff_base` | `1.5` | Exponential delay: `backoff_base ** attempt` seconds |

---

## 5. Usage Examples

### Example 1: Query Ingested Places via cURL
```bash
curl -X GET "http://127.0.0.1:8765/api/places?vertical=healthcare&min_rating=4.5&has_website=true&page_size=10" \
     -H "Accept: application/json"
```

### Example 2: Plan an Automated Pipeline Run via Python
```python
import httpx

payload = {
    "country": "US",
    "regions": ["CA"],
    "categories": ["healthcare", "food"],
    "category_workers": 2,
    "stages": {
        "collection": True,
        "discovery": True,
        "enrichment": True
    }
}

response = httpx.post("http://127.0.0.1:8765/api/pipeline/plan", json=payload)
print(response.json())
```

### Example 3: Export Filtered Dataset to Parquet
```bash
curl -X GET "http://127.0.0.1:8765/api/export?format=parquet&full=true&vertical=food" \
     --output "food_businesses.parquet"
```
