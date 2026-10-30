import sqlite3
import json
import contextlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS places (
    cid TEXT PRIMARY KEY,
    ftid TEXT,
    place_id TEXT,
    name TEXT NOT NULL,
    address TEXT,
    street_address TEXT,
    latitude REAL,
    longitude REAL,
    website TEXT,
    phone TEXT,
    phone_e164 TEXT,
    category TEXT,
    vertical TEXT,
    vertical_raw TEXT,
    country TEXT,
    country_code TEXT,
    region TEXT,
    city TEXT,
    postal_code TEXT,
    rating REAL,
    review_count INTEGER DEFAULT 0,
    timezone TEXT,
    claimed_status INTEGER DEFAULT 0,
    photo_thumbnail TEXT,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    error TEXT,
    website_status TEXT,
    website_candidate TEXT,
    website_discovery_method TEXT,
    website_evidence TEXT,
    website_last_checked TIMESTAMP,
    website_error TEXT,
    official_website_url TEXT,
    enrichment_status TEXT,
    enrichment_last_checked TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_places_cid ON places(cid);
CREATE INDEX IF NOT EXISTS idx_places_place_id ON places(place_id);
CREATE INDEX IF NOT EXISTS idx_places_status ON places(status);
CREATE INDEX IF NOT EXISTS idx_places_rating ON places(rating);
CREATE INDEX IF NOT EXISTS idx_places_review_count ON places(review_count);
CREATE INDEX IF NOT EXISTS idx_places_category ON places(category);
CREATE INDEX IF NOT EXISTS idx_places_lat_lng ON places(latitude, longitude);

CREATE TABLE IF NOT EXISTS raw_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id TEXT UNIQUE,
    cid TEXT NOT NULL,
    ftid TEXT,
    place_id TEXT,
    name TEXT NOT NULL,
    address TEXT,
    street_address TEXT,
    latitude REAL,
    longitude REAL,
    website TEXT,
    phone TEXT,
    phone_e164 TEXT,
    category TEXT,
    vertical TEXT,
    vertical_raw TEXT,
    country TEXT,
    country_code TEXT,
    region TEXT,
    city TEXT,
    postal_code TEXT,
    rating REAL,
    review_count INTEGER DEFAULT 0,
    search_task_id TEXT,
    tile_id TEXT,
    run_id TEXT,
    source_data TEXT,
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_raw_places_raw_id ON raw_places(raw_id);
CREATE INDEX IF NOT EXISTS idx_raw_places_cid ON raw_places(cid);
CREATE INDEX IF NOT EXISTS idx_raw_places_place_id ON raw_places(place_id);
CREATE INDEX IF NOT EXISTS idx_raw_places_task_id ON raw_places(search_task_id);
CREATE INDEX IF NOT EXISTS idx_raw_places_tile_id ON raw_places(tile_id);
CREATE INDEX IF NOT EXISTS idx_raw_places_run_id ON raw_places(run_id);
CREATE INDEX IF NOT EXISTS idx_raw_places_country_region ON raw_places(country_code, region);
CREATE INDEX IF NOT EXISTS idx_raw_places_category ON raw_places(category);
CREATE INDEX IF NOT EXISTS idx_raw_places_collected_at ON raw_places(collected_at);

CREATE TABLE IF NOT EXISTS verticals (
    key TEXT PRIMARY KEY,
    label TEXT
);

CREATE TABLE IF NOT EXISTS countries (
    code TEXT PRIMARY KEY,
    name TEXT
);

CREATE TABLE IF NOT EXISTS hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid TEXT NOT NULL,
    day INTEGER,
    day_name TEXT,
    formatted_text TEXT,
    is_closed INTEGER DEFAULT 0,
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_hours_cid ON hours(cid);

CREATE TABLE IF NOT EXISTS attributes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid TEXT NOT NULL,
    group_id TEXT,
    group_name TEXT,
    attribute_id TEXT,
    attribute_name TEXT,
    is_enabled INTEGER DEFAULT 1,
    display_value TEXT,
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_attributes_cid ON attributes(cid);

CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    cid TEXT NOT NULL,
    author_name TEXT NOT NULL,
    author_id TEXT,
    author_avatar_url TEXT,
    author_profile_url TEXT,
    rating INTEGER NOT NULL,
    relative_date TEXT,
    timestamp_ms INTEGER,
    text TEXT,
    language TEXT,
    owner_response_text TEXT,
    owner_response_date TEXT,
    reaction_count INTEGER DEFAULT 0,
    photos_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reviews_cid ON reviews(cid);
CREATE INDEX IF NOT EXISTS idx_reviews_rating ON reviews(rating);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid TEXT NOT NULL,
    review_id TEXT,
    photo_url TEXT NOT NULL,
    local_path TEXT,
    attribution TEXT,
    file_size INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_photos_cid ON photos(cid);
CREATE INDEX IF NOT EXISTS idx_photos_review_id ON photos(review_id);

CREATE TABLE IF NOT EXISTS popular_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid TEXT NOT NULL,
    day_idx INTEGER,
    hour_idx INTEGER,
    popularity_pct INTEGER,
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_popular_times_cid ON popular_times(cid);

CREATE TABLE IF NOT EXISTS run_state (
    run_id TEXT PRIMARY KEY,
    query TEXT,
    bbox TEXT,
    status TEXT DEFAULT 'running',
    current_step TEXT,
    total_discovered INTEGER DEFAULT 0,
    total_enriched INTEGER DEFAULT 0,
    total_reviews INTEGER DEFAULT 0,
    total_photos INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    meta_json TEXT
);

-- Full-text search on places
CREATE VIRTUAL TABLE IF NOT EXISTS places_fts USING fts5(
    name,
    address,
    phone,
    website,
    category,
    content='places',
    content_rowid='rowid'
);

-- Geo resolution cache
CREATE TABLE IF NOT EXISTS geo_cache (
    query TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    span_deg REAL NOT NULL,
    country TEXT,
    country_code TEXT,
    confidence REAL DEFAULT 1.0,
    resolved_from TEXT,
    feature_class TEXT,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status TEXT DEFAULT 'queued',
    query TEXT,
    location_input TEXT,
    resolved_label TEXT,
    lat REAL,
    lng REAL,
    span_deg REAL,
    country_code TEXT,
    options_json TEXT,
    stage TEXT DEFAULT 'queued',
    progress_json TEXT,
    places_found INTEGER DEFAULT 0,
    reviews_found INTEGER DEFAULT 0,
    photos_found INTEGER DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    level TEXT DEFAULT 'info',
    stage TEXT,
    message TEXT,
    payload_json TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id);

-- Regional geographic collection tasks for state-wise execution
CREATE TABLE IF NOT EXISTS collection_tasks (
    task_id TEXT PRIMARY KEY,
    country_code TEXT NOT NULL,
    region_code TEXT NOT NULL,
    region_name TEXT NOT NULL,
    category TEXT NOT NULL,
    tile_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    attempt_count INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tasks_region ON collection_tasks(country_code, region_code);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON collection_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_region_status ON collection_tasks(country_code, region_code, status);

-- Step 3: Company Profile & Website Enrichment Records
CREATE TABLE IF NOT EXISTS company_profiles (
    cid TEXT PRIMARY KEY,
    overview TEXT,
    industry TEXT,
    services_json TEXT,
    locations_json TEXT,
    contact_page_url TEXT,
    source_pages_json TEXT,
    extraction_status TEXT DEFAULT 'completed',
    last_extracted TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE
);

-- Step 3: Public Leadership Records
CREATE TABLE IF NOT EXISTS company_leaders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid TEXT NOT NULL,
    full_name TEXT NOT NULL,
    title TEXT NOT NULL,
    role_type TEXT,
    biography TEXT,
    source_url TEXT,
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cid, full_name, title),
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_leaders_cid ON company_leaders(cid);

-- Step 3: Public Business Contact Records
CREATE TABLE IF NOT EXISTS company_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid TEXT NOT NULL,
    contact_type TEXT NOT NULL,
    contact_value TEXT NOT NULL,
    label TEXT DEFAULT 'general',
    source_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cid, contact_type, contact_value),
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_contacts_cid ON company_contacts(cid);

-- Automated Pipeline 2 Orchestration Runs & Checkpoints
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT DEFAULT 'pending',
    config_json TEXT NOT NULL,
    current_stage TEXT,
    current_country TEXT,
    current_region TEXT,
    current_category TEXT,
    total_tasks INTEGER DEFAULT 0,
    completed_tasks INTEGER DEFAULT 0,
    failed_tasks INTEGER DEFAULT 0,
    businesses_collected INTEGER DEFAULT 0,
    websites_verified INTEGER DEFAULT 0,
    companies_enriched INTEGER DEFAULT 0,
    dry_run INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP,
    paused_at TIMESTAMP,
    completed_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);

-- Persistent Pipeline Configuration
CREATE TABLE IF NOT EXISTS pipeline_configs (
    id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Persistent Geographic Tiles for state-wise scraping and map visualization
CREATE TABLE IF NOT EXISTS geographic_tiles (
    tile_id TEXT PRIMARY KEY,
    parent_tile_id TEXT,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    country_code TEXT NOT NULL,
    region_code TEXT NOT NULL,
    category TEXT NOT NULL,
    min_lat REAL NOT NULL,
    max_lat REAL NOT NULL,
    min_lng REAL NOT NULL,
    max_lng REAL NOT NULL,
    center_lat REAL NOT NULL,
    center_lng REAL NOT NULL,
    span_deg REAL NOT NULL,
    depth INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    results_count INTEGER DEFAULT 0,
    new_records INTEGER DEFAULT 0,
    updated_records INTEGER DEFAULT 0,
    duplicate_cids INTEGER DEFAULT 0,
    last_error TEXT,
    http_status INTEGER,
    subdivision_status TEXT,
    last_checkpoint TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tiles_task_id ON geographic_tiles(task_id);
CREATE INDEX IF NOT EXISTS idx_tiles_run_id ON geographic_tiles(run_id);
CREATE INDEX IF NOT EXISTS idx_tiles_region ON geographic_tiles(country_code, region_code);
CREATE INDEX IF NOT EXISTS idx_tiles_status ON geographic_tiles(status);
CREATE INDEX IF NOT EXISTS idx_tiles_parent ON geographic_tiles(parent_tile_id);

-- Triggers to maintain FTS index
CREATE TRIGGER IF NOT EXISTS places_ai AFTER INSERT ON places BEGIN
  INSERT INTO places_fts(rowid, name, address, phone, website, category)
  VALUES (new.rowid, new.name, new.address, new.phone, new.website, new.category);
END;

CREATE TRIGGER IF NOT EXISTS places_ad AFTER DELETE ON places BEGIN
  INSERT INTO places_fts(places_fts, rowid, name, address, phone, website, category)
  VALUES('delete', old.rowid, old.name, old.address, old.phone, old.website, old.category);
END;

CREATE TRIGGER IF NOT EXISTS places_au AFTER UPDATE ON places BEGIN
  INSERT INTO places_fts(places_fts, rowid, name, address, phone, website, category)
  VALUES('delete', old.rowid, old.name, old.address, old.phone, old.website, old.category);
  INSERT INTO places_fts(rowid, name, address, phone, website, category)
  VALUES (new.rowid, new.name, new.address, new.phone, new.website, new.category);
END;

-- Incremental Business Intelligence Findings
CREATE TABLE IF NOT EXISTS additional_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid TEXT NOT NULL,
    place_id TEXT,
    company_name TEXT,
    entity_type TEXT NOT NULL,
    person_name TEXT,
    job_title TEXT,
    field_name TEXT NOT NULL,
    field_value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    match_status TEXT NOT NULL,
    verification_status TEXT DEFAULT 'unverified',
    confidence REAL DEFAULT 1.0,
    conflict_existing_value TEXT,
    match_reason TEXT,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE,
    UNIQUE(cid, entity_type, field_name, normalized_value)
);
CREATE INDEX IF NOT EXISTS idx_add_intel_cid ON additional_intelligence(cid);
CREATE INDEX IF NOT EXISTS idx_add_intel_status ON additional_intelligence(match_status);
CREATE INDEX IF NOT EXISTS idx_add_intel_entity ON additional_intelligence(entity_type);
CREATE INDEX IF NOT EXISTS idx_add_intel_field ON additional_intelligence(field_name);

-- Intelligence Source Evidence and Access History
CREATE TABLE IF NOT EXISTS intelligence_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intelligence_id INTEGER,
    cid TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_title TEXT,
    retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    evidence_summary TEXT,
    status TEXT DEFAULT 'success',
    error_message TEXT,
    FOREIGN KEY(cid) REFERENCES places(cid) ON DELETE CASCADE,
    FOREIGN KEY(intelligence_id) REFERENCES additional_intelligence(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_intel_src_cid ON intelligence_sources(cid);
CREATE INDEX IF NOT EXISTS idx_intel_src_url ON intelligence_sources(source_url);
CREATE INDEX IF NOT EXISTS idx_intel_src_intel ON intelligence_sources(intelligence_id);
CREATE INDEX IF NOT EXISTS idx_intel_src_type ON intelligence_sources(source_type);
CREATE INDEX IF NOT EXISTS idx_intel_src_status ON intelligence_sources(status);
"""

class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_commit_time: Optional[datetime] = None

    @contextlib.contextmanager
    def get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 60000;")
        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self):
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
            
            # Idempotent column migrations for places table
            cursor = conn.execute("PRAGMA table_info(places)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            
            new_columns = [
                ("vertical", "TEXT"),
                ("vertical_raw", "TEXT"),
                ("country", "TEXT"),
                ("country_code", "TEXT"),
                ("region", "TEXT"),
                ("city", "TEXT"),
                ("postal_code", "TEXT"),
                ("first_seen_job_id", "TEXT"),
                ("tile_id", "TEXT"),
                ("website_status", "TEXT"),
                ("website_candidate", "TEXT"),
                ("website_discovery_method", "TEXT"),
                ("website_evidence", "TEXT"),
                ("website_last_checked", "TIMESTAMP"),
                ("website_error", "TEXT"),
                ("official_website_url", "TEXT"),
                ("enrichment_status", "TEXT"),
                ("enrichment_last_checked", "TIMESTAMP"),
            ]
            for col_name, col_type in new_columns:
                if col_name not in existing_cols:
                    conn.execute(f"ALTER TABLE places ADD COLUMN {col_name} {col_type};")

            # Indexes on new columns
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_vertical ON places(vertical);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_country ON places(country);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_country_code ON places(country_code);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_city ON places(city);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_job ON places(first_seen_job_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_tile_id ON places(tile_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_website_status ON places(website_status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_official_url ON places(official_website_url);")

            # Idempotent column migrations for collection_tasks table
            cursor_ct = conn.execute("PRAGMA table_info(collection_tasks)")
            existing_ct_cols = {row["name"] for row in cursor_ct.fetchall()}
            if "run_id" not in existing_ct_cols:
                conn.execute("ALTER TABLE collection_tasks ADD COLUMN run_id TEXT;")
            if "records_collected" not in existing_ct_cols:
                conn.execute("ALTER TABLE collection_tasks ADD COLUMN records_collected INTEGER DEFAULT 0;")

            # Idempotent column migrations for pipeline_runs table
            cursor_pr = conn.execute("PRAGMA table_info(pipeline_runs)")
            existing_pr_cols = {row["name"] for row in cursor_pr.fetchall()}
            if "run_date" not in existing_pr_cols:
                conn.execute("ALTER TABLE pipeline_runs ADD COLUMN run_date TEXT;")
            if "tiles_planned" not in existing_pr_cols:
                conn.execute("ALTER TABLE pipeline_runs ADD COLUMN tiles_planned INTEGER DEFAULT 0;")
            if "tiles_completed" not in existing_pr_cols:
                conn.execute("ALTER TABLE pipeline_runs ADD COLUMN tiles_completed INTEGER DEFAULT 0;")
            if "tiles_failed" not in existing_pr_cols:
                conn.execute("ALTER TABLE pipeline_runs ADD COLUMN tiles_failed INTEGER DEFAULT 0;")

            # Populate verticals lookup table
            from .taxonomy import load_verticals, load_countries
            verticals_cfg = load_verticals()
            for v_key, v_info in verticals_cfg.items():
                label = v_info.get("label", v_key.title()) if isinstance(v_info, dict) else v_key.title()
                conn.execute(
                    "INSERT INTO verticals (key, label) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET label = excluded.label",
                    (v_key, label)
                )

            # Populate countries lookup table
            countries_cfg, _ = load_countries()
            for c_code, c_info in countries_cfg.items():
                code_str = "NO" if c_code is False else "YES" if c_code is True else str(c_code).upper()
                name_str = c_info.get("name", code_str) if isinstance(c_info, dict) else code_str
                conn.execute(
                    "INSERT INTO countries (code, name) VALUES (?, ?) ON CONFLICT(code) DO UPDATE SET name = excluded.name",
                    (code_str, name_str)
                )

            conn.commit()
            self._last_commit_time = datetime.now()

    def reclassify_all(self) -> Dict[str, int]:
        """Re-derives vertical and location dimensions for all existing rows idempotently."""
        from .taxonomy import enrich_place_dimensions
        with self.get_connection() as conn:
            rows = conn.execute("SELECT cid, category, address, vertical, country FROM places").fetchall()
            changed = 0
            for r in rows:
                p = {"category": r["category"], "address": r["address"]}
                enrich_place_dimensions(p)
                if p["vertical"] != r["vertical"] or p["country"] != r["country"]:
                    changed += 1
                conn.execute("""
                    UPDATE places SET
                        vertical = :vertical,
                        vertical_raw = :vertical_raw,
                        country = :country,
                        country_code = :country_code,
                        region = :region,
                        city = :city,
                        postal_code = :postal_code,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE cid = :cid
                """, {
                    "cid": r["cid"],
                    "vertical": p["vertical"],
                    "vertical_raw": p["vertical_raw"],
                    "country": p["country"],
                    "country_code": p["country_code"],
                    "region": p["region"],
                    "city": p["city"],
                    "postal_code": p["postal_code"]
                })
            conn.commit()
            self._last_commit_time = datetime.now()
            return {"total": len(rows), "changed": changed}

    def upsert_place_stub(self, place: Dict[str, Any]) -> bool:
        """Insert or ignore place stub by cid. Returns True if inserted."""
        from .taxonomy import enrich_place_dimensions
        if "region" not in place and "region_code" in place:
            place["region"] = place["region_code"]
        if "country_code" not in place and "country" in place:
            place["country_code"] = place["country"]
        enrich_place_dimensions(place)

        defaults = {
            "ftid": None,
            "place_id": None,
            "name": "",
            "address": None,
            "street_address": None,
            "latitude": None,
            "longitude": None,
            "website": None,
            "phone": None,
            "phone_e164": None,
            "category": None,
            "vertical": None,
            "vertical_raw": None,
            "country": None,
            "country_code": None,
            "region": None,
            "city": None,
            "postal_code": None,
            "rating": None,
            "review_count": 0,
            "status": "pending",
            "first_seen_job_id": None,
            "website_status": None,
            "official_website_url": None,
            "enrichment_status": None,
        }
        merged = {**defaults, **place}

        sql = """
        INSERT INTO places (
            cid, ftid, place_id, name, address, street_address,
            latitude, longitude, website, phone, phone_e164,
            category, vertical, vertical_raw, country, country_code,
            region, city, postal_code, rating, review_count, status,
            first_seen_job_id, website_status, official_website_url, enrichment_status,
            created_at, updated_at
        ) VALUES (
            :cid, :ftid, :place_id, :name, :address, :street_address,
            :latitude, :longitude, :website, :phone, :phone_e164,
            :category, :vertical, :vertical_raw, :country, :country_code,
            :region, :city, :postal_code, :rating, :review_count, :status,
            :first_seen_job_id, :website_status, :official_website_url, :enrichment_status,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT(cid) DO UPDATE SET
            ftid = COALESCE(excluded.ftid, places.ftid),
            place_id = COALESCE(excluded.place_id, places.place_id),
            name = COALESCE(excluded.name, places.name),
            address = COALESCE(excluded.address, places.address),
            street_address = COALESCE(excluded.street_address, places.street_address),
            latitude = COALESCE(excluded.latitude, places.latitude),
            longitude = COALESCE(excluded.longitude, places.longitude),
            website = COALESCE(excluded.website, places.website),
            phone = COALESCE(excluded.phone, places.phone),
            category = COALESCE(excluded.category, places.category),
            vertical = COALESCE(excluded.vertical, places.vertical),
            vertical_raw = COALESCE(excluded.vertical_raw, places.vertical_raw),
            country = COALESCE(excluded.country, places.country),
            country_code = COALESCE(excluded.country_code, places.country_code),
            region = COALESCE(excluded.region, places.region),
            city = COALESCE(excluded.city, places.city),
            postal_code = COALESCE(excluded.postal_code, places.postal_code),
            rating = COALESCE(excluded.rating, places.rating),
            review_count = MAX(places.review_count, excluded.review_count),
            first_seen_job_id = COALESCE(places.first_seen_job_id, excluded.first_seen_job_id),
            website_status = COALESCE(excluded.website_status, places.website_status),
            official_website_url = COALESCE(excluded.official_website_url, places.official_website_url),
            enrichment_status = COALESCE(excluded.enrichment_status, places.enrichment_status),
            updated_at = CURRENT_TIMESTAMP;
        """
        with self.get_connection() as conn:
            cur = conn.execute(sql, merged)
            conn.commit()
            self._last_commit_time = datetime.now()
            self._last_commit_time = datetime.now()
            return cur.rowcount > 0

    def insert_raw_place(self, record: Dict[str, Any]) -> bool:
        """
        Inserts a raw business discovery record into raw_places.
        Preserves original fields, category, region, search_task_id, tile_id, run_id, and collection timestamp.
        Idempotent on raw_id = '{search_task_id}:{tile_id}:{cid}' so task replay does not duplicate records.
        """
        from .taxonomy import enrich_place_dimensions

        rec = dict(record)
        explicit_cc = rec.get("country_code") or rec.get("country")
        explicit_reg = rec.get("region") or rec.get("region_code")
        if "region" not in rec and "region_code" in rec:
            rec["region"] = rec["region_code"]
        if "country_code" not in rec and "country" in rec:
            rec["country_code"] = rec["country"]
        enrich_place_dimensions(rec)
        if explicit_cc:
            rec["country_code"] = explicit_cc
        if explicit_reg:
            rec["region"] = explicit_reg

        cid = str(rec.get("cid") or "")
        task_id = str(rec.get("search_task_id") or rec.get("task_id") or "none")
        tile_id = str(rec.get("tile_id") or "none")
        raw_id = rec.get("raw_id") or f"{task_id}:{tile_id}:{cid}"

        source_data = rec.get("source_data")
        if source_data is not None and not isinstance(source_data, str):
            try:
                source_data = json.dumps(source_data)
            except Exception:
                source_data = str(source_data)

        defaults = {
            "raw_id": raw_id,
            "cid": cid,
            "ftid": rec.get("ftid"),
            "place_id": rec.get("place_id"),
            "name": rec.get("name") or "",
            "address": rec.get("address"),
            "street_address": rec.get("street_address"),
            "latitude": rec.get("latitude"),
            "longitude": rec.get("longitude"),
            "website": rec.get("website"),
            "phone": rec.get("phone"),
            "phone_e164": rec.get("phone_e164"),
            "category": rec.get("category"),
            "vertical": rec.get("vertical"),
            "vertical_raw": rec.get("vertical_raw"),
            "country": rec.get("country"),
            "country_code": rec.get("country_code"),
            "region": rec.get("region"),
            "city": rec.get("city"),
            "postal_code": rec.get("postal_code"),
            "rating": rec.get("rating"),
            "review_count": rec.get("review_count") or 0,
            "search_task_id": rec.get("search_task_id") or rec.get("task_id"),
            "tile_id": rec.get("tile_id"),
            "run_id": rec.get("run_id"),
            "source_data": source_data,
            "collected_at": rec.get("collected_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        sql = """
        INSERT INTO raw_places (
            raw_id, cid, ftid, place_id, name, address, street_address,
            latitude, longitude, website, phone, phone_e164,
            category, vertical, vertical_raw, country, country_code,
            region, city, postal_code, rating, review_count,
            search_task_id, tile_id, run_id, source_data, collected_at
        ) VALUES (
            :raw_id, :cid, :ftid, :place_id, :name, :address, :street_address,
            :latitude, :longitude, :website, :phone, :phone_e164,
            :category, :vertical, :vertical_raw, :country, :country_code,
            :region, :city, :postal_code, :rating, :review_count,
            :search_task_id, :tile_id, :run_id, :source_data, :collected_at
        )
        ON CONFLICT(raw_id) DO UPDATE SET
            ftid = COALESCE(excluded.ftid, raw_places.ftid),
            place_id = COALESCE(excluded.place_id, raw_places.place_id),
            name = COALESCE(excluded.name, raw_places.name),
            address = COALESCE(excluded.address, raw_places.address),
            street_address = COALESCE(excluded.street_address, raw_places.street_address),
            latitude = COALESCE(excluded.latitude, raw_places.latitude),
            longitude = COALESCE(excluded.longitude, raw_places.longitude),
            website = COALESCE(excluded.website, raw_places.website),
            phone = COALESCE(excluded.phone, raw_places.phone),
            phone_e164 = COALESCE(excluded.phone_e164, raw_places.phone_e164),
            category = COALESCE(excluded.category, raw_places.category),
            vertical = COALESCE(excluded.vertical, raw_places.vertical),
            vertical_raw = COALESCE(excluded.vertical_raw, raw_places.vertical_raw),
            country = COALESCE(excluded.country, raw_places.country),
            country_code = COALESCE(excluded.country_code, raw_places.country_code),
            region = COALESCE(excluded.region, raw_places.region),
            city = COALESCE(excluded.city, raw_places.city),
            postal_code = COALESCE(excluded.postal_code, raw_places.postal_code),
            rating = COALESCE(excluded.rating, raw_places.rating),
            review_count = MAX(raw_places.review_count, excluded.review_count),
            source_data = COALESCE(excluded.source_data, raw_places.source_data);
        """
        with self.get_connection() as conn:
            exists = conn.execute("SELECT 1 FROM raw_places WHERE raw_id = ?", (raw_id,)).fetchone() is not None
            cur = conn.execute(sql, defaults)
            conn.commit()
            self._last_commit_time = datetime.now()
            self._last_commit_time = datetime.now()
            return not exists

    def insert_raw_places_batch(self, records: List[Dict[str, Any]]) -> int:
        """Batch inserts raw place records into raw_places. Returns count inserted."""
        if not records:
            return 0
        from .taxonomy import enrich_place_dimensions
        inserted = 0
        sql = """
        INSERT INTO raw_places (
            raw_id, cid, ftid, place_id, name, address, street_address,
            latitude, longitude, website, phone, phone_e164,
            category, vertical, vertical_raw, country, country_code,
            region, city, postal_code, rating, review_count,
            search_task_id, tile_id, run_id, source_data, collected_at
        ) VALUES (
            :raw_id, :cid, :ftid, :place_id, :name, :address, :street_address,
            :latitude, :longitude, :website, :phone, :phone_e164,
            :category, :vertical, :vertical_raw, :country, :country_code,
            :region, :city, :postal_code, :rating, :review_count,
            :search_task_id, :tile_id, :run_id, :source_data, :collected_at
        )
        ON CONFLICT(raw_id) DO UPDATE SET
            ftid = COALESCE(excluded.ftid, raw_places.ftid),
            place_id = COALESCE(excluded.place_id, raw_places.place_id),
            name = COALESCE(excluded.name, raw_places.name),
            address = COALESCE(excluded.address, raw_places.address),
            street_address = COALESCE(excluded.street_address, raw_places.street_address),
            latitude = COALESCE(excluded.latitude, raw_places.latitude),
            longitude = COALESCE(excluded.longitude, raw_places.longitude),
            website = COALESCE(excluded.website, raw_places.website),
            phone = COALESCE(excluded.phone, raw_places.phone),
            phone_e164 = COALESCE(excluded.phone_e164, raw_places.phone_e164),
            category = COALESCE(excluded.category, raw_places.category),
            vertical = COALESCE(excluded.vertical, raw_places.vertical),
            vertical_raw = COALESCE(excluded.vertical_raw, raw_places.vertical_raw),
            country = COALESCE(excluded.country, raw_places.country),
            country_code = COALESCE(excluded.country_code, raw_places.country_code),
            region = COALESCE(excluded.region, raw_places.region),
            city = COALESCE(excluded.city, raw_places.city),
            postal_code = COALESCE(excluded.postal_code, raw_places.postal_code),
            rating = COALESCE(excluded.rating, raw_places.rating),
            review_count = MAX(raw_places.review_count, excluded.review_count),
            source_data = COALESCE(excluded.source_data, raw_places.source_data);
        """
        with self.get_connection() as conn:
            for record in records:
                rec = dict(record)
                explicit_cc = rec.get("country_code") or rec.get("country")
                explicit_reg = rec.get("region") or rec.get("region_code")
                if "region" not in rec and "region_code" in rec:
                    rec["region"] = rec["region_code"]
                if "country_code" not in rec and "country" in rec:
                    rec["country_code"] = rec["country"]
                enrich_place_dimensions(rec)
                if explicit_cc:
                    rec["country_code"] = explicit_cc
                if explicit_reg:
                    rec["region"] = explicit_reg

                cid = str(rec.get("cid") or "")
                task_id = str(rec.get("search_task_id") or rec.get("task_id") or "none")
                tile_id = str(rec.get("tile_id") or "none")
                raw_id = rec.get("raw_id") or f"{task_id}:{tile_id}:{cid}"

                source_data = rec.get("source_data")
                if source_data is not None and not isinstance(source_data, str):
                    try:
                        source_data = json.dumps(source_data)
                    except Exception:
                        source_data = str(source_data)

                defaults = {
                    "raw_id": raw_id,
                    "cid": cid,
                    "ftid": rec.get("ftid"),
                    "place_id": rec.get("place_id"),
                    "name": rec.get("name") or "",
                    "address": rec.get("address"),
                    "street_address": rec.get("street_address"),
                    "latitude": rec.get("latitude"),
                    "longitude": rec.get("longitude"),
                    "website": rec.get("website"),
                    "phone": rec.get("phone"),
                    "phone_e164": rec.get("phone_e164"),
                    "category": rec.get("category"),
                    "vertical": rec.get("vertical"),
                    "vertical_raw": rec.get("vertical_raw"),
                    "country": rec.get("country"),
                    "country_code": rec.get("country_code"),
                    "region": rec.get("region"),
                    "city": rec.get("city"),
                    "postal_code": rec.get("postal_code"),
                    "rating": rec.get("rating"),
                    "review_count": rec.get("review_count") or 0,
                    "search_task_id": rec.get("search_task_id") or rec.get("task_id"),
                    "tile_id": rec.get("tile_id"),
                    "run_id": rec.get("run_id"),
                    "source_data": source_data,
                    "collected_at": rec.get("collected_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                cur = conn.execute(sql, defaults)
                if cur.rowcount > 0:
                    inserted += 1
            conn.commit()
            self._last_commit_time = datetime.now()
        return inserted

    def get_raw_places(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        category: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetches raw discovery records from raw_places."""
        with self.get_connection() as conn:
            table_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_places'").fetchone()
            if not table_check:
                return []
            query = "SELECT * FROM raw_places WHERE 1=1"
            params: List[Any] = []
            if country_code:
                query += " AND UPPER(country_code) = ?"
                params.append(country_code.upper())
            if region_code:
                query += " AND UPPER(region) = ?"
                params.append(region_code.upper())
            if category:
                query += " AND LOWER(category) = ?"
                params.append(category.lower())
            if run_id:
                query += " AND run_id = ?"
                params.append(run_id)
            query += " ORDER BY id ASC"
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_raw_places_count(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        category: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> int:
        """Returns total count of raw discovery records."""
        with self.get_connection() as conn:
            table_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_places'").fetchone()
            if not table_check:
                return 0
            query = "SELECT COUNT(*) FROM raw_places WHERE 1=1"
            params: List[Any] = []
            if country_code:
                query += " AND UPPER(country_code) = ?"
                params.append(country_code.upper())
            if region_code:
                query += " AND UPPER(region) = ?"
                params.append(region_code.upper())
            if category:
                query += " AND LOWER(category) = ?"
                params.append(category.lower())
            if run_id:
                query += " AND run_id = ?"
                params.append(run_id)
            row = conn.execute(query, params).fetchone()
            return row[0] if row else 0

    def get_places_count(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        category: Optional[str] = None,
    ) -> int:
        """Returns total count of canonical places in places table."""
        with self.get_connection() as conn:
            query = "SELECT COUNT(*) FROM places WHERE 1=1"
            params: List[Any] = []
            if country_code:
                query += " AND UPPER(country_code) = ?"
                params.append(country_code.upper())
            if region_code:
                query += " AND UPPER(region) = ?"
                params.append(region_code.upper())
            if category:
                query += " AND LOWER(category) = ?"
                params.append(category.lower())
            row = conn.execute(query, params).fetchone()
            return row[0] if row else 0

    def get_collection_counts(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Provides side-by-side counts of raw records vs canonical places."""
        with self.get_connection() as conn:
            raw_cnt = self.get_raw_places_count(country_code=country_code, region_code=region_code)
            canonical_cnt = self.get_places_count(country_code=country_code, region_code=region_code)

            # Country breakdowns
            raw_by_c: Dict[str, int] = {}
            canonical_by_c: Dict[str, int] = {}

            table_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_places'").fetchone()
            if table_check:
                for r in conn.execute("SELECT COALESCE(country_code, country, 'Unknown') as c, COUNT(*) FROM raw_places GROUP BY c"):
                    raw_by_c[r[0]] = r[1]

            for r in conn.execute("SELECT COALESCE(country_code, country, 'Unknown') as c, COUNT(*) FROM places GROUP BY c"):
                canonical_by_c[r[0]] = r[1]

            # Region breakdowns
            raw_by_r: Dict[str, int] = {}
            canonical_by_r: Dict[str, int] = {}
            if table_check:
                for r in conn.execute("SELECT COALESCE(region, 'Unknown') as reg, COUNT(*) FROM raw_places GROUP BY reg"):
                    raw_by_r[r[0]] = r[1]

            for r in conn.execute("SELECT COALESCE(region, 'Unknown') as reg, COUNT(*) FROM places GROUP BY reg"):
                canonical_by_r[r[0]] = r[1]

            # Deduplication ratio
            dedup_diff = max(0, raw_cnt - canonical_cnt)
            dedup_pct = round((dedup_diff / raw_cnt * 100.0), 2) if raw_cnt > 0 else 0.0

            return {
                "raw_total": raw_cnt,
                "canonical_total": canonical_cnt,
                "duplicates_collapsed": dedup_diff,
                "dedup_ratio_pct": dedup_pct,
                "raw_by_country": raw_by_c,
                "canonical_by_country": canonical_by_c,
                "raw_by_region": raw_by_r,
                "canonical_by_region": canonical_by_r,
            }

    def update_place_details(self, cid: str, details: Dict[str, Any], status: str = "details_done"):
        with self.get_connection() as conn:
            conn.execute("""
                UPDATE places SET
                    timezone = COALESCE(:timezone, timezone),
                    claimed_status = COALESCE(:claimed_status, claimed_status),
                    photo_thumbnail = COALESCE(:photo_thumbnail, photo_thumbnail),
                    rating = COALESCE(:rating, rating),
                    review_count = COALESCE(:review_count, review_count),
                    status = :status,
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE cid = :cid
            """, {
                "cid": cid,
                "timezone": details.get("timezone"),
                "claimed_status": details.get("claimed_status"),
                "photo_thumbnail": details.get("photo_thumbnail"),
                "rating": details.get("rating"),
                "review_count": details.get("review_count"),
                "status": status,
            })

            # Replace hours
            if "hours" in details and details["hours"]:
                conn.execute("DELETE FROM hours WHERE cid = ?", (cid,))
                for h in details["hours"]:
                    conn.execute("""
                        INSERT INTO hours (cid, day, day_name, formatted_text, is_closed)
                        VALUES (?, ?, ?, ?, ?)
                    """, (cid, h.get("day"), h.get("day_name"), h.get("formatted_text"), 1 if h.get("is_closed") else 0))

            # Replace attributes
            if "attributes" in details and details["attributes"]:
                conn.execute("DELETE FROM attributes WHERE cid = ?", (cid,))
                for a in details["attributes"]:
                    conn.execute("""
                        INSERT INTO attributes (cid, group_id, group_name, attribute_id, attribute_name, is_enabled, display_value)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        cid, a.get("group_id"), a.get("group_name"), a.get("attribute_id"),
                        a.get("attribute_name"), 1 if a.get("is_enabled", True) else 0,
                        a.get("display_value")
                    ))

            # Popular times
            if "popular_times" in details and details["popular_times"]:
                conn.execute("DELETE FROM popular_times WHERE cid = ?", (cid,))
                for pt in details["popular_times"]:
                    conn.execute("""
                        INSERT INTO popular_times (cid, day_idx, hour_idx, popularity_pct)
                        VALUES (?, ?, ?, ?)
                    """, (cid, pt.get("day_idx"), pt.get("hour_idx"), pt.get("popularity_pct")))

            conn.commit()
            self._last_commit_time = datetime.now()

        # Insert child photos if provided
        if "photos" in details and details["photos"]:
            for ph in details["photos"]:
                self.insert_photo(ph)

    def upsert_place_details(self, details: Dict[str, Any], status: str = "details_done"):
        cid = str(details.get("cid", ""))
        if cid:
            self.update_place_details(cid, details, status=status)

    def mark_place_failed(self, cid: str, error: str):
        with self.get_connection() as conn:
            conn.execute("""
                UPDATE places SET
                    status = 'failed',
                    attempts = attempts + 1,
                    error = :error,
                    updated_at = CURRENT_TIMESTAMP
                WHERE cid = :cid
            """, {"cid": cid, "error": error})
            conn.commit()
            self._last_commit_time = datetime.now()

    def insert_review(self, review: Dict[str, Any]):
        defaults = {
            "author_id": None,
            "author_avatar_url": None,
            "author_profile_url": None,
            "relative_date": None,
            "timestamp_ms": None,
            "text": "",
            "language": None,
            "owner_response_text": None,
            "owner_response_date": None,
            "reaction_count": 0,
            "photos_json": None,
        }
        merged = {**defaults, **review}
        sql = """
        INSERT INTO reviews (
            review_id, cid, author_name, author_id, author_avatar_url,
            author_profile_url, rating, relative_date, timestamp_ms,
            text, language, owner_response_text, owner_response_date,
            reaction_count, photos_json, created_at
        ) VALUES (
            :review_id, :cid, :author_name, :author_id, :author_avatar_url,
            :author_profile_url, :rating, :relative_date, :timestamp_ms,
            :text, :language, :owner_response_text, :owner_response_date,
            :reaction_count, :photos_json, CURRENT_TIMESTAMP
        )
        ON CONFLICT(review_id) DO UPDATE SET
            reaction_count = excluded.reaction_count,
            text = excluded.text;
        """
        with self.get_connection() as conn:
            conn.execute(sql, merged)
            conn.commit()
            self._last_commit_time = datetime.now()

    def insert_photo(self, photo: Dict[str, Any]):
        defaults = {
            "review_id": None,
            "local_path": None,
            "attribution": None,
            "file_size": None,
        }
        merged = {**defaults, **photo}
        sql = """
        INSERT INTO photos (cid, review_id, photo_url, local_path, attribution, file_size, created_at)
        VALUES (:cid, :review_id, :photo_url, :local_path, :attribution, :file_size, CURRENT_TIMESTAMP);
        """
        with self.get_connection() as conn:
            conn.execute(sql, merged)
            conn.commit()
            self._last_commit_time = datetime.now()

    def get_place(self, cid: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM places WHERE cid = ?", (cid,)).fetchone()
            if not row:
                return None
            return dict(row)

    def get_place_details_full(self, cid: str) -> Optional[Dict[str, Any]]:
        place = self.get_place(cid)
        if not place:
            return None
        with self.get_connection() as conn:
            hours = [dict(r) for r in conn.execute("SELECT * FROM hours WHERE cid = ? ORDER BY day", (cid,)).fetchall()]
            attributes = [dict(r) for r in conn.execute("SELECT * FROM attributes WHERE cid = ? ORDER BY group_name, attribute_name", (cid,)).fetchall()]
            popular_times = [dict(r) for r in conn.execute("SELECT * FROM popular_times WHERE cid = ? ORDER BY day_idx, hour_idx", (cid,)).fetchall()]
            reviews = [dict(r) for r in conn.execute("SELECT * FROM reviews WHERE cid = ? ORDER BY timestamp_ms DESC", (cid,)).fetchall()]
            photos = [dict(r) for r in conn.execute("SELECT * FROM photos WHERE cid = ? ORDER BY id ASC", (cid,)).fetchall()]

        place["hours"] = hours
        place["attributes"] = attributes
        place["popular_times"] = popular_times
        place["reviews"] = reviews
        place["photos"] = photos

        enrichment = self.get_company_enrichment(cid)
        place["profile"] = enrichment.get("profile")
        place["leaders"] = enrichment.get("leaders") or []
        place["contacts"] = enrichment.get("contacts") or []
        return place

    def get_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
            enriched = conn.execute("SELECT COUNT(*) FROM places WHERE status IN ('details_done', 'reviews_done', 'complete')").fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM places WHERE status = 'pending'").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM places WHERE status = 'failed'").fetchone()[0]
            reviews_count = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
            photos_count = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
            last_run = conn.execute("SELECT * FROM run_state ORDER BY start_time DESC LIMIT 1").fetchone()

            raw_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_places'").fetchone()
            raw_places_count = conn.execute("SELECT COUNT(*) FROM raw_places").fetchone()[0] if raw_check else 0

            # Pipeline 2 Metrics
            with_website = conn.execute("SELECT COUNT(*) FROM places WHERE website IS NOT NULL AND website != ''").fetchone()[0]
            verified_websites = conn.execute("SELECT COUNT(*) FROM places WHERE website_status = 'verified'").fetchone()[0]
            needs_review = conn.execute("SELECT COUNT(*) FROM places WHERE website_status = 'needs_review'").fetchone()[0]
            website_missing = conn.execute("SELECT COUNT(*) FROM places WHERE website_status = 'website_missing'").fetchone()[0]
            website_failed = conn.execute("SELECT COUNT(*) FROM places WHERE website_status = 'failed'").fetchone()[0]
            
            enrichment_completed = conn.execute("SELECT COUNT(*) FROM places WHERE enrichment_status = 'completed'").fetchone()[0]
            enrichment_partial = conn.execute("SELECT COUNT(*) FROM places WHERE enrichment_status = 'partial'").fetchone()[0]
            enrichment_failed = conn.execute("SELECT COUNT(*) FROM places WHERE enrichment_status = 'failed'").fetchone()[0]

            company_profiles_count = conn.execute("SELECT COUNT(*) FROM company_profiles").fetchone()[0]
            company_leaders_count = conn.execute("SELECT COUNT(*) FROM company_leaders").fetchone()[0]
            company_contacts_count = conn.execute("SELECT COUNT(*) FROM company_contacts").fetchone()[0]

            # Country breakdown
            country_rows = conn.execute("""
                SELECT COALESCE(country_code, country, 'Unknown') as c, COUNT(*) as cnt
                FROM places GROUP BY c ORDER BY cnt DESC LIMIT 10
            """).fetchall()
            country_distribution = [{"country": r[0], "count": r[1]} for r in country_rows]

            # Region breakdown
            region_rows = conn.execute("""
                SELECT COALESCE(region, 'Unknown') as reg, COUNT(*) as cnt
                FROM places WHERE region IS NOT NULL AND region != ''
                GROUP BY reg ORDER BY cnt DESC LIMIT 10
            """).fetchall()
            region_distribution = [{"region": r[0], "count": r[1]} for r in region_rows]

            # Rating distribution (1 to 5)
            rating_dist = {f"{i}": 0 for i in range(1, 6)}
            rows = conn.execute("SELECT ROUND(rating) as star, COUNT(*) FROM places WHERE rating IS NOT NULL GROUP BY ROUND(rating)").fetchall()
            for r in rows:
                k = str(int(r[0]))
                if k in rating_dist:
                    rating_dist[k] = r[1]

            # Top 20 categories
            cat_rows = conn.execute("""
                SELECT category, COUNT(*) as cnt
                FROM places
                WHERE category IS NOT NULL AND category != ''
                GROUP BY category
                ORDER BY cnt DESC
                LIMIT 20
            """).fetchall()
            top_categories = [{"category": r[0], "count": r[1]} for r in cat_rows]

            # Status breakdown
            status_rows = conn.execute("SELECT status, COUNT(*) FROM places GROUP BY status").fetchall()
            status_breakdown = {r[0]: r[1] for r in status_rows}

            # Collection Tasks Summary
            task_rows = conn.execute("SELECT status, COUNT(*) FROM collection_tasks GROUP BY status").fetchall()
            tasks_summary = {r[0]: r[1] for r in task_rows}

            # Recent Jobs
            recent_jobs = [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 5").fetchall()]

            return {
                "total_places": total,
                "raw_places_count": raw_places_count,
                "enriched_places": enriched,
                "pending_places": pending,
                "failed_places": failed,
                "total_reviews": reviews_count,
                "total_photos": photos_count,
                "last_run": dict(last_run) if last_run else None,
                "rating_distribution": rating_dist,
                "top_categories": top_categories,
                "status_breakdown": status_breakdown,
                "with_website_count": with_website,
                "verified_websites_count": verified_websites,
                "needs_review_count": needs_review,
                "website_missing_count": website_missing,
                "website_failed_count": website_failed,
                "enrichment_completed_count": enrichment_completed,
                "enrichment_partial_count": enrichment_partial,
                "enrichment_failed_count": enrichment_failed,
                "company_profiles_count": company_profiles_count,
                "company_leaders_count": company_leaders_count,
                "company_contacts_count": company_contacts_count,
                "country_distribution": country_distribution,
                "region_distribution": region_distribution,
                "website_status_breakdown": {
                    "verified": verified_websites,
                    "needs_review": needs_review,
                    "website_missing": website_missing,
                    "failed": website_failed,
                    "unverified": max(0, total - (verified_websites + needs_review + website_missing + website_failed))
                },
                "enrichment_status_breakdown": {
                    "completed": enrichment_completed,
                    "partial": enrichment_partial,
                    "failed": enrichment_failed,
                    "not_enriched": max(0, total - (enrichment_completed + enrichment_partial + enrichment_failed))
                },
                "collection_tasks_summary": tasks_summary,
                "recent_jobs": recent_jobs,
            }

    def _build_filter_clauses(
        self,
        filters: Dict[str, Any],
        exclude_field: Optional[str] = None
    ) -> Tuple[List[str], Dict[str, Any]]:
        where_clauses = []
        params = {}

        if filters.get("q") and exclude_field != "q":
            q = filters["q"]
            where_clauses.append("places.rowid IN (SELECT rowid FROM places_fts WHERE places_fts MATCH :q)")
            params["q"] = f'"{q}"*'

        if filters.get("status") and exclude_field != "status":
            where_clauses.append("places.status = :status")
            params["status"] = filters["status"]

        if filters.get("category") and exclude_field != "category":
            where_clauses.append("places.category = :category")
            params["category"] = filters["category"]

        if filters.get("vertical") and exclude_field != "vertical":
            v_raw = filters["vertical"]
            if isinstance(v_raw, str):
                v_list = [v.strip() for v in v_raw.split(",") if v.strip()]
            elif isinstance(v_raw, (list, tuple, set)):
                v_list = [str(v).strip() for v in v_raw if str(v).strip()]
            else:
                v_list = []
            if v_list:
                placeholders = []
                for i, v in enumerate(v_list):
                    pk = f"v_{i}"
                    placeholders.append(f":{pk}")
                    params[pk] = v
                where_clauses.append(f"places.vertical IN ({','.join(placeholders)})")

        if filters.get("country") and exclude_field != "country":
            c_raw = filters["country"]
            if isinstance(c_raw, str):
                c_list = [c.strip() for c in c_raw.split(",") if c.strip()]
            elif isinstance(c_raw, (list, tuple, set)):
                c_list = [str(c).strip() for c in c_raw if str(c).strip()]
            else:
                c_list = []
            if c_list:
                sub = []
                for i, c in enumerate(c_list):
                    pk = f"c_{i}"
                    params[pk] = c
                    sub.append(f"(places.country_code = :{pk} OR places.country = :{pk})")
                where_clauses.append(f"({' OR '.join(sub)})")

        if filters.get("city") and exclude_field != "city":
            ci_raw = filters["city"]
            if isinstance(ci_raw, str):
                ci_list = [c.strip() for c in ci_raw.split(",") if c.strip()]
            elif isinstance(ci_raw, (list, tuple, set)):
                ci_list = [str(c).strip() for c in ci_raw if str(c).strip()]
            else:
                ci_list = []
            if ci_list:
                placeholders = []
                for i, ci in enumerate(ci_list):
                    pk = f"city_{i}"
                    placeholders.append(f":{pk}")
                    params[pk] = ci
                where_clauses.append(f"places.city IN ({','.join(placeholders)})")

        if filters.get("min_rating") is not None and exclude_field != "min_rating":
            where_clauses.append("places.rating >= :min_rating")
            params["min_rating"] = float(filters["min_rating"])

        if filters.get("min_reviews") is not None and exclude_field != "min_reviews":
            where_clauses.append("places.review_count >= :min_reviews")
            params["min_reviews"] = int(filters["min_reviews"])

        if filters.get("has_phone") is True and exclude_field != "has_phone":
            where_clauses.append("places.phone IS NOT NULL AND places.phone != ''")
        elif filters.get("has_phone") is False and exclude_field != "has_phone":
            where_clauses.append("(places.phone IS NULL OR places.phone = '')")

        if filters.get("has_website") is True and exclude_field != "has_website":
            where_clauses.append("places.website IS NOT NULL AND places.website != ''")
        elif filters.get("has_website") is False and exclude_field != "has_website":
            where_clauses.append("(places.website IS NULL OR places.website = '')")

        if filters.get("has_photos") is True and exclude_field != "has_photos":
            where_clauses.append("places.cid IN (SELECT DISTINCT cid FROM photos)")
        elif filters.get("has_photos") is False and exclude_field != "has_photos":
            where_clauses.append("places.cid NOT IN (SELECT DISTINCT cid FROM photos)")

        if filters.get("claimed") is True and exclude_field != "claimed":
            where_clauses.append("places.claimed_status = 1")
        elif filters.get("claimed") is False and exclude_field != "claimed":
            where_clauses.append("(places.claimed_status = 0 OR places.claimed_status IS NULL)")

        if filters.get("has_reviews") is True and exclude_field != "has_reviews":
            where_clauses.append("(places.review_count > 0 OR places.cid IN (SELECT DISTINCT cid FROM reviews))")
        elif filters.get("has_reviews") is False and exclude_field != "has_reviews":
            where_clauses.append("(places.review_count = 0 AND places.cid NOT IN (SELECT DISTINCT cid FROM reviews))")

        if filters.get("has_hours") is True and exclude_field != "has_hours":
            where_clauses.append("places.cid IN (SELECT DISTINCT cid FROM hours)")
        elif filters.get("has_hours") is False and exclude_field != "has_hours":
            where_clauses.append("places.cid NOT IN (SELECT DISTINCT cid FROM hours)")

        if filters.get("has_attributes") is True and exclude_field != "has_attributes":
            where_clauses.append("places.cid IN (SELECT DISTINCT cid FROM attributes)")
        elif filters.get("has_attributes") is False and exclude_field != "has_attributes":
            where_clauses.append("places.cid NOT IN (SELECT DISTINCT cid FROM attributes)")

        if filters.get("region") and exclude_field != "region":
            reg_raw = filters["region"]
            if isinstance(reg_raw, str):
                reg_list = [r.strip() for r in reg_raw.split(",") if r.strip()]
            elif isinstance(reg_raw, (list, tuple, set)):
                reg_list = [str(r).strip() for r in reg_raw if str(r).strip()]
            else:
                reg_list = []
            if reg_list:
                placeholders = []
                for i, reg in enumerate(reg_list):
                    pk = f"reg_{i}"
                    placeholders.append(f":{pk}")
                    params[pk] = reg
                where_clauses.append(f"places.region IN ({','.join(placeholders)})")

        if filters.get("website_status") and exclude_field != "website_status":
            ws = str(filters["website_status"]).lower().strip()
            if ws == "unverified":
                where_clauses.append("(places.website_status IS NULL OR places.website_status IN ('unverified', 'pending'))")
            else:
                where_clauses.append("places.website_status = :ws_filter")
                params["ws_filter"] = ws

        if filters.get("enrichment_status") and exclude_field != "enrichment_status":
            es = str(filters["enrichment_status"]).lower().strip()
            if es == "not_enriched":
                where_clauses.append("(places.enrichment_status IS NULL OR places.enrichment_status = 'pending')")
            else:
                where_clauses.append("places.enrichment_status = :es_filter")
                params["es_filter"] = es

        if filters.get("missing_info") and exclude_field != "missing_info":
            m_type = str(filters["missing_info"]).lower().strip()
            if m_type == "phone":
                where_clauses.append("(places.phone IS NULL OR places.phone = '')")
            elif m_type == "website":
                where_clauses.append("(places.website IS NULL OR places.website = '')")
            elif m_type == "address":
                where_clauses.append("(places.address IS NULL OR places.address = '')")
            elif m_type == "enrichment":
                where_clauses.append("(places.enrichment_status IS NULL OR places.enrichment_status = 'pending')")

        if (filters.get("job") or filters.get("first_seen_job_id")) and exclude_field not in ("job", "first_seen_job_id"):
            job_val = filters.get("job") or filters.get("first_seen_job_id")
            where_clauses.append("places.first_seen_job_id = :job")
            params["job"] = job_val

        return where_clauses, params

    def query_places(
        self,
        q: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        min_rating: Optional[float] = None,
        min_reviews: Optional[int] = None,
        has_phone: Optional[bool] = None,
        has_website: Optional[bool] = None,
        has_photos: Optional[bool] = None,
        claimed: Optional[bool] = None,
        vertical: Optional[Any] = None,
        country: Optional[Any] = None,
        region: Optional[Any] = None,
        city: Optional[Any] = None,
        website_status: Optional[str] = None,
        enrichment_status: Optional[str] = None,
        missing_info: Optional[str] = None,
        job: Optional[str] = None,
        has_reviews: Optional[bool] = None,
        has_hours: Optional[bool] = None,
        has_attributes: Optional[bool] = None,
        sort: str = "rating_desc",
        page: int = 1,
        page_size: int = 50,
        after_val: Optional[Any] = None,
        after_cid: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Server-side paginated places query with high-performance filters."""
        filters = {
            "q": q,
            "status": status,
            "category": category,
            "min_rating": min_rating,
            "min_reviews": min_reviews,
            "has_phone": has_phone,
            "has_website": has_website,
            "has_photos": has_photos,
            "claimed": claimed,
            "vertical": vertical,
            "country": country,
            "region": region,
            "city": city,
            "website_status": website_status,
            "enrichment_status": enrichment_status,
            "missing_info": missing_info,
            "job": job,
            "has_reviews": has_reviews,
            "has_hours": has_hours,
            "has_attributes": has_attributes,
        }
        where_clauses, params = self._build_filter_clauses(filters)
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Determine sorting
        sort_map = {
            "name_asc": "places.name ASC, places.cid ASC",
            "name_desc": "places.name DESC, places.cid DESC",
            "rating_desc": "places.rating DESC NULLS LAST, places.cid DESC",
            "rating_asc": "places.rating ASC NULLS FIRST, places.cid ASC",
            "reviews_desc": "places.review_count DESC, places.cid DESC",
            "reviews_asc": "places.review_count ASC, places.cid ASC",
            "created_desc": "places.created_at DESC, places.cid DESC",
        }
        order_by = sort_map.get(sort, "places.rating DESC NULLS LAST, places.cid DESC")

        with self.get_connection() as conn:
            # Total count
            count_sql = f"SELECT COUNT(*) FROM places {where_sql}"
            try:
                total_count = conn.execute(count_sql, params).fetchone()[0]
            except sqlite3.OperationalError:
                # If FTS error, fallback to LIKE
                if q:
                    where_clauses.pop(0)
                    where_clauses.append("(places.name LIKE :like_q OR places.address LIKE :like_q OR places.phone LIKE :like_q)")
                    params["like_q"] = f"%{q}%"
                    del params["q"]
                    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                    total_count = conn.execute(f"SELECT COUNT(*) FROM places {where_sql}", params).fetchone()[0]
                else:
                    total_count = 0

            # Pagination
            offset = max(0, (page - 1) * page_size)
            query_sql = f"""
                SELECT places.*,
                    (SELECT COUNT(*) FROM photos WHERE photos.cid = places.cid) as photos_count,
                    (SELECT COUNT(*) FROM company_contacts WHERE company_contacts.cid = places.cid) as contacts_count,
                    (SELECT COUNT(*) FROM company_leaders WHERE company_leaders.cid = places.cid) as leaders_count
                FROM places
                {where_sql}
                ORDER BY {order_by}
                LIMIT :limit OFFSET :offset
            """
            params["limit"] = page_size
            params["offset"] = offset

            rows = conn.execute(query_sql, params).fetchall()
            places = [dict(r) for r in rows]

            return places, total_count

    def get_vertical_facets(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Facet counts for business verticals respecting other active filters."""
        filters = filters or {}
        where_clauses, params = self._build_filter_clauses(filters, exclude_field="vertical")
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        sql = f"""
            SELECT places.vertical, COUNT(*) as cnt
            FROM places
            {where_sql}
            GROUP BY places.vertical
        """
        with self.get_connection() as conn:
            counts_map = {r[0]: r[1] for r in conn.execute(sql, params).fetchall() if r[0]}
            v_rows = conn.execute("SELECT key, label FROM verticals").fetchall()
            results = []
            known_keys = set()
            for r in v_rows:
                k, label = r[0], r[1]
                known_keys.add(k)
                results.append({
                    "key": k,
                    "label": label or k.title(),
                    "count": counts_map.get(k, 0)
                })
            for k, cnt in counts_map.items():
                if k not in known_keys:
                    results.append({"key": k, "label": k.title(), "count": cnt})
            return results

    def get_country_facets(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Facet counts for countries respecting other active filters."""
        filters = filters or {}
        where_clauses, params = self._build_filter_clauses(filters, exclude_field="country")
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        if where_sql:
            where_sql += " AND places.country_code IS NOT NULL AND places.country_code != '' AND places.country_code != 'unknown'"
        else:
            where_sql = "WHERE places.country_code IS NOT NULL AND places.country_code != '' AND places.country_code != 'unknown'"

        sql = f"""
            SELECT places.country_code, places.country, COUNT(*) as cnt
            FROM places
            {where_sql}
            GROUP BY places.country_code, places.country
            ORDER BY cnt DESC
        """
        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "code": r[0],
                    "name": r[1] if r[1] and r[1] != "unknown" else r[0],
                    "count": r[2]
                }
                for r in rows
            ]

    def get_city_facets(self, country: Optional[str] = None, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Facet counts for cities respecting country and other active filters."""
        filters = dict(filters or {})
        if country:
            filters["country"] = country
        where_clauses, params = self._build_filter_clauses(filters, exclude_field="city")
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        if where_sql:
            where_sql += " AND places.city IS NOT NULL AND places.city != ''"
        else:
            where_sql = "WHERE places.city IS NOT NULL AND places.city != ''"

        sql = f"""
            SELECT places.city, COUNT(*) as cnt
            FROM places
            {where_sql}
            GROUP BY places.city
            ORDER BY cnt DESC
        """
        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [{"city": r[0], "count": r[1]} for r in rows]

    def get_quality_report(self) -> Dict[str, Any]:
        """Calculates completeness, extraction coverage, duplicates, coordinate outliers, and failed records."""
        with self.get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
            if total == 0:
                return {
                    "total": 0,
                    "metrics": {
                        "name_completeness": 0.0,
                        "address_completeness": 0.0,
                        "phone_completeness": 0.0,
                        "website_completeness": 0.0,
                        "website_verification_coverage": 0.0,
                        "company_profile_coverage": 0.0,
                        "contact_extraction_coverage": 0.0,
                        "leadership_extraction_coverage": 0.0,
                    },
                    "counts": {
                        "total": 0,
                        "with_name": 0,
                        "with_address": 0,
                        "with_phone": 0,
                        "with_website": 0,
                        "verified_websites": 0,
                        "needs_review": 0,
                        "website_failed": 0,
                        "company_profiles": 0,
                        "with_contacts": 0,
                        "with_leaders": 0,
                        "enrichment_failed": 0,
                    },
                    "null_counts": {},
                    "duplicates_by_cid": [],
                    "duplicates_by_name_address": [],
                    "coordinate_outliers": [],
                    "failed_rows": [],
                    "needs_review_rows": []
                }

            # Null counts per column
            null_cols = ["name", "address", "phone", "website", "category", "rating", "latitude", "longitude"]
            null_counts = {}
            for col in null_cols:
                cnt = conn.execute(f"SELECT COUNT(*) FROM places WHERE {col} IS NULL OR {col} = ''").fetchone()[0]
                null_counts[col] = cnt

            # Counts for pipeline metrics
            with_name = conn.execute("SELECT COUNT(*) FROM places WHERE name IS NOT NULL AND name != ''").fetchone()[0]
            with_address = conn.execute("SELECT COUNT(*) FROM places WHERE address IS NOT NULL AND address != ''").fetchone()[0]
            with_phone = conn.execute("SELECT COUNT(*) FROM places WHERE phone IS NOT NULL AND phone != ''").fetchone()[0]
            with_website = conn.execute("SELECT COUNT(*) FROM places WHERE website IS NOT NULL AND website != ''").fetchone()[0]
            verified_websites = conn.execute("SELECT COUNT(*) FROM places WHERE website_status = 'verified'").fetchone()[0]
            needs_review = conn.execute("SELECT COUNT(*) FROM places WHERE website_status = 'needs_review'").fetchone()[0]
            website_failed = conn.execute("SELECT COUNT(*) FROM places WHERE website_status = 'failed'").fetchone()[0]
            enrichment_failed = conn.execute("SELECT COUNT(*) FROM places WHERE enrichment_status = 'failed'").fetchone()[0]

            # Cross table distinct counts
            company_profiles = conn.execute("SELECT COUNT(DISTINCT cid) FROM company_profiles").fetchone()[0]
            with_contacts = conn.execute("SELECT COUNT(DISTINCT cid) FROM company_contacts").fetchone()[0]
            with_leaders = conn.execute("SELECT COUNT(DISTINCT cid) FROM company_leaders").fetchone()[0]

            def pct(num: int, den: int) -> float:
                return round((num / den * 100.0), 1) if den > 0 else 0.0

            metrics = {
                "name_completeness": pct(with_name, total),
                "address_completeness": pct(with_address, total),
                "phone_completeness": pct(with_phone, total),
                "website_completeness": pct(with_website, total),
                "website_verification_coverage": pct(verified_websites, total),
                "company_profile_coverage": pct(company_profiles, total),
                "contact_extraction_coverage": pct(with_contacts, total),
                "leadership_extraction_coverage": pct(with_leaders, total),
            }

            counts = {
                "total": total,
                "with_name": with_name,
                "with_address": with_address,
                "with_phone": with_phone,
                "with_website": with_website,
                "verified_websites": verified_websites,
                "needs_review": needs_review,
                "website_failed": website_failed,
                "company_profiles": company_profiles,
                "with_contacts": with_contacts,
                "with_leaders": with_leaders,
                "enrichment_failed": enrichment_failed,
            }

            # Duplicates by name + address
            dup_rows = conn.execute("""
                SELECT name, address, COUNT(*) as cnt, GROUP_CONCAT(cid) as cids
                FROM places
                WHERE name IS NOT NULL AND address IS NOT NULL AND address != ''
                GROUP BY name, address
                HAVING cnt > 1
                LIMIT 50
            """).fetchall()
            duplicates_by_name_address = [
                {"name": r[0], "address": r[1], "count": r[2], "cids": r[3].split(",")}
                for r in dup_rows
            ]

            # Coordinate outliers
            outlier_rows = conn.execute("""
                SELECT cid, name, latitude, longitude
                FROM places
                WHERE (latitude IS NOT NULL AND (latitude < -90 OR latitude > 90))
                   OR (longitude IS NOT NULL AND (longitude < -180 OR longitude > 180))
                   OR (latitude = 0.0 AND longitude = 0.0)
                LIMIT 50
            """).fetchall()
            coordinate_outliers = [dict(r) for r in outlier_rows]

            # Failed rows
            failed_rows = conn.execute("""
                SELECT cid, name, attempts, error, updated_at
                FROM places
                WHERE status = 'failed' OR website_status = 'failed' OR enrichment_status = 'failed'
                ORDER BY updated_at DESC
                LIMIT 50
            """).fetchall()

            # Needs review rows
            needs_review_rows = conn.execute("""
                SELECT cid, name, website, website_candidate, website_evidence, updated_at
                FROM places
                WHERE website_status = 'needs_review'
                ORDER BY updated_at DESC
                LIMIT 50
            """).fetchall()

            return {
                "total": total,
                "metrics": metrics,
                "counts": counts,
                "null_counts": null_counts,
                "duplicates_by_cid": [],
                "duplicates_by_name_address": duplicates_by_name_address,
                "coordinate_outliers": coordinate_outliers,
                "failed_rows": [dict(r) for r in failed_rows],
                "needs_review_rows": [dict(r) for r in needs_review_rows]
            }


    def retry_failed_places(self) -> int:
        with self.get_connection() as conn:
            cur = conn.execute("UPDATE places SET status = 'pending', error = NULL WHERE status = 'failed'")
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount

    # ------------------------------------------------------------------
    # Geo Cache Methods
    # ------------------------------------------------------------------
    def get_geo_cache(self, query: str) -> Optional[Dict[str, Any]]:
        norm_q = str(query).strip().lower()
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM geo_cache WHERE query = ?", (norm_q,)).fetchone()
            if not row:
                return None
            return dict(row)

    def set_geo_cache(self, data: Dict[str, Any]):
        norm_q = str(data.get("query", "")).strip().lower()
        if not norm_q:
            return
        sql = """
        INSERT INTO geo_cache (
            query, label, lat, lng, span_deg, country, country_code,
            confidence, resolved_from, feature_class, ts
        ) VALUES (
            :query, :label, :lat, :lng, :span_deg, :country, :country_code,
            :confidence, :resolved_from, :feature_class, CURRENT_TIMESTAMP
        )
        ON CONFLICT(query) DO UPDATE SET
            label = excluded.label,
            lat = excluded.lat,
            lng = excluded.lng,
            span_deg = excluded.span_deg,
            country = excluded.country,
            country_code = excluded.country_code,
            confidence = excluded.confidence,
            resolved_from = excluded.resolved_from,
            feature_class = excluded.feature_class,
            ts = CURRENT_TIMESTAMP;
        """
        params = {
            "query": norm_q,
            "label": data.get("label", ""),
            "lat": float(data.get("lat", 0.0)),
            "lng": float(data.get("lng", 0.0)),
            "span_deg": float(data.get("span_deg", 0.1)),
            "country": data.get("country"),
            "country_code": data.get("country_code"),
            "confidence": float(data.get("confidence", 1.0)),
            "resolved_from": data.get("resolved_from", "search_rpc"),
            "feature_class": data.get("feature_class", "city"),
        }
        with self.get_connection() as conn:
            conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()

    # ------------------------------------------------------------------
    # Live Jobs & Events Methods
    # ------------------------------------------------------------------
    def create_job(self, job_data: Dict[str, Any]) -> str:
        sql = """
        INSERT INTO jobs (
            id, created_at, started_at, status, query, location_input,
            resolved_label, lat, lng, span_deg, country_code, options_json,
            stage, progress_json, places_found, reviews_found, photos_found, error
        ) VALUES (
            :id, CURRENT_TIMESTAMP, NULL, :status, :query, :location_input,
            :resolved_label, :lat, :lng, :span_deg, :country_code, :options_json,
            :stage, :progress_json, 0, 0, 0, NULL
        );
        """
        params = {
            "id": job_data["id"],
            "status": job_data.get("status", "queued"),
            "query": job_data.get("query"),
            "location_input": job_data.get("location_input"),
            "resolved_label": job_data.get("resolved_label"),
            "lat": job_data.get("lat"),
            "lng": job_data.get("lng"),
            "span_deg": job_data.get("span_deg"),
            "country_code": job_data.get("country_code"),
            "options_json": json.dumps(job_data.get("options", {})) if isinstance(job_data.get("options"), dict) else job_data.get("options_json"),
            "stage": job_data.get("stage", "queued"),
            "progress_json": json.dumps(job_data.get("progress", {})) if isinstance(job_data.get("progress"), dict) else job_data.get("progress_json"),
        }
        with self.get_connection() as conn:
            conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
        return job_data["id"]

    def update_job(self, job_id: str, updates: Dict[str, Any]):
        set_parts = []
        params = {"id": job_id}
        for k, v in updates.items():
            if k in ("options", "progress") and isinstance(v, dict):
                k = f"{k}_json"
                v = json.dumps(v)
            set_parts.append(f"{k} = :{k}")
            params[k] = v

        if not set_parts:
            return

        sql = f"UPDATE jobs SET {', '.join(set_parts)} WHERE id = :id"
        with self.get_connection() as conn:
            conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("options_json"):
                try:
                    res["options"] = json.loads(res["options_json"])
                except Exception:
                    res["options"] = {}
            if res.get("progress_json"):
                try:
                    res["progress"] = json.loads(res["progress_json"])
                except Exception:
                    res["progress"] = {}
            return res

    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            jobs = []
            for r in rows:
                item = dict(r)
                if item.get("options_json"):
                    try:
                        item["options"] = json.loads(item["options_json"])
                    except Exception:
                        item["options"] = {}
                if item.get("progress_json"):
                    try:
                        item["progress"] = json.loads(item["progress_json"])
                    except Exception:
                        item["progress"] = {}
                jobs.append(item)
            return jobs

    def add_job_event(
        self,
        job_id: str,
        stage: str,
        message: str,
        level: str = "info",
        payload: Optional[Dict[str, Any]] = None
    ) -> int:
        sql = """
        INSERT INTO job_events (job_id, ts, level, stage, message, payload_json)
        VALUES (:job_id, CURRENT_TIMESTAMP, :level, :stage, :message, :payload_json);
        """
        params = {
            "job_id": job_id,
            "level": level,
            "stage": stage,
            "message": message,
            "payload_json": json.dumps(payload) if payload is not None else None,
        }
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.lastrowid

    def get_job_events(self, job_id: str, limit: int = 200, after_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            if after_id is not None:
                rows = conn.execute(
                    "SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                    (job_id, after_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM job_events WHERE job_id = ? ORDER BY id ASC LIMIT ?",
                    (job_id, limit)
                ).fetchall()
            events = []
            for r in rows:
                ev = dict(r)
                if ev.get("payload_json"):
                    try:
                        ev["payload"] = json.loads(ev["payload_json"])
                    except Exception:
                        ev["payload"] = None
                events.append(ev)
            return events

    def mark_interrupted_jobs(self) -> int:
        with self.get_connection() as conn:
            cur = conn.execute("""
                UPDATE jobs
                SET status = 'interrupted',
                    error = 'Interrupted by process restart',
                    finished_at = CURRENT_TIMESTAMP
                WHERE status NOT IN ('done', 'cancelled', 'failed', 'interrupted')
            """)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount

    # -------------------------------------------------------------------------
    # Regional Collection Tasks (Pipeline 2 Scope & Task Tracking)
    # -------------------------------------------------------------------------

    def create_collection_task(self, task: Dict[str, Any]) -> bool:
        """Idempotently insert or update a geographic collection task."""
        sql = """
        INSERT INTO collection_tasks (
            task_id, country_code, region_code, region_name, category, tile_id,
            status, attempt_count, last_error, run_id, records_collected, updated_at
        ) VALUES (
            :task_id, :country_code, :region_code, :region_name, :category, :tile_id,
            :status, :attempt_count, :last_error, :run_id, :records_collected, CURRENT_TIMESTAMP
        )
        ON CONFLICT(task_id) DO UPDATE SET
            country_code = excluded.country_code,
            region_code = excluded.region_code,
            region_name = excluded.region_name,
            category = excluded.category,
            tile_id = excluded.tile_id,
            run_id = COALESCE(excluded.run_id, collection_tasks.run_id),
            records_collected = COALESCE(excluded.records_collected, collection_tasks.records_collected),
            updated_at = CURRENT_TIMESTAMP;
        """
        params = {
            "task_id": task["task_id"],
            "country_code": task["country_code"].upper(),
            "region_code": task["region_code"].upper(),
            "region_name": task.get("region_name") or task["region_code"].upper(),
            "category": task["category"],
            "tile_id": str(task.get("tile_id", "")),
            "status": task.get("status", "pending"),
            "attempt_count": int(task.get("attempt_count", 0)),
            "last_error": task.get("last_error"),
            "run_id": task.get("run_id"),
            "records_collected": int(task.get("records_collected", 0)),
        }
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount > 0

    def get_collection_tasks(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM collection_tasks WHERE 1=1"
        params: List[Any] = []
        if country_code:
            query += " AND country_code = ?"
            params.append(country_code.upper())
        if region_code:
            query += " AND region_code = ?"
            params.append(region_code.upper())
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at ASC, task_id ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_region_progress(self, country_code: str, region_code: str) -> Dict[str, Any]:
        """
        Calculates region collection progress. A region is ONLY marked complete
        when all configured tasks for that region exist and are status='completed'.
        """
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT status, COUNT(*) as cnt
                FROM collection_tasks
                WHERE country_code = ? AND region_code = ?
                GROUP BY status
            """, (country_code.upper(), region_code.upper())).fetchall()
            counts = {r["status"]: r["cnt"] for r in rows}
            total = sum(counts.values())
            completed = counts.get("completed", 0)
            pending = counts.get("pending", 0)
            failed = counts.get("failed", 0)
            in_progress = counts.get("in_progress", 0)
            is_complete = (total > 0 and completed == total)
            return {
                "country_code": country_code.upper(),
                "region_code": region_code.upper(),
                "total_tasks": total,
                "completed_tasks": completed,
                "pending_tasks": pending,
                "failed_tasks": failed,
                "in_progress_tasks": in_progress,
                "is_complete": is_complete
            }

    def get_next_incomplete_task(
        self,
        country_code: str,
        region_code: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves the next incomplete task (skipping all 'completed' tasks).
        Picks pending or failed tasks ordered by creation time.
        """
        query = """
            SELECT * FROM collection_tasks
            WHERE country_code = ? AND status != 'completed'
        """
        params: List[Any] = [country_code.upper()]
        if region_code:
            query += " AND region_code = ?"
            params.append(region_code.upper())
        query += " ORDER BY created_at ASC, task_id ASC LIMIT 1"

        with self.get_connection() as conn:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

    def update_collection_task_status(
        self,
        task_id: str,
        status: str,
        error: Optional[str] = None,
        records_collected: Optional[int] = None
    ) -> bool:
        """Updates task status, incrementing attempt_count on failure or in_progress."""
        with self.get_connection() as conn:
            if status == "failed":
                cur = conn.execute("""
                    UPDATE collection_tasks
                    SET status = 'failed',
                        attempt_count = CASE WHEN status = 'in_progress' THEN attempt_count ELSE attempt_count + 1 END,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                """, (error, task_id))
            elif status == "completed":
                if records_collected is not None:
                    cur = conn.execute("""
                        UPDATE collection_tasks
                        SET status = 'completed',
                            records_collected = ?,
                            last_error = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE task_id = ?
                    """, (records_collected, task_id))
                else:
                    cur = conn.execute("""
                        UPDATE collection_tasks
                        SET status = 'completed',
                            last_error = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE task_id = ?
                    """, (task_id,))
            elif status == "in_progress":
                cur = conn.execute("""
                    UPDATE collection_tasks
                    SET status = 'in_progress',
                        attempt_count = attempt_count + 1,
                        last_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                """, (task_id,))
            else:
                cur = conn.execute("""
                    UPDATE collection_tasks
                    SET status = ?,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                """, (status, error, task_id))
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount > 0

    def reset_failed_collection_tasks(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        max_attempts: Optional[int] = None
    ) -> int:
        """Resets failed tasks to 'pending' for retry."""
        query = "UPDATE collection_tasks SET status = 'pending', updated_at = CURRENT_TIMESTAMP WHERE status = 'failed'"
        params: List[Any] = []
        if country_code:
            query += " AND country_code = ?"
            params.append(country_code.upper())
        if region_code:
            query += " AND region_code = ?"
            params.append(region_code.upper())
        if max_attempts is not None:
            query += " AND attempt_count < ?"
            params.append(max_attempts)

        with self.get_connection() as conn:
            cur = conn.execute(query, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount

    # -------------------------------------------------------------------------
    # Official Website Discovery & Verification (Pipeline 2 Step 2)
    # -------------------------------------------------------------------------

    def get_businesses_for_website_discovery(
        self,
        country_code: str,
        region_code: Optional[str] = None,
        limit: int = 50,
        retry_failed: bool = False,
        force: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Retrieves eligible business records for official website discovery and verification.
        Filters strictly by target country (e.g. US or MX) and optional state/entity region.
        """
        query = "SELECT * FROM places WHERE country_code = ?"
        params: List[Any] = [country_code.upper()]

        if region_code:
            query += " AND (region = ? OR region = ?)"
            params.extend([region_code.upper(), region_code.lower()])

        if force:
            pass
        elif retry_failed:
            query += " AND (website_status IN ('failed', 'needs_review', 'website_missing') OR website_status IS NULL)"
        else:
            query += " AND (website_status IS NULL OR website_status = 'pending')"

        query += " ORDER BY CASE WHEN website IS NOT NULL THEN 0 ELSE 1 END, created_at ASC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def update_business_website_verification(
        self,
        cid: str,
        status: str,
        website: Optional[str] = None,
        candidate: Optional[str] = None,
        discovery_method: Optional[str] = None,
        evidence: Optional[Any] = None,
        error: Optional[str] = None
    ) -> bool:
        """
        Transactionally updates website verification status, candidate, verified URL,
        discovery method, and supporting evidence JSON for a business record.
        """
        evidence_json = json.dumps(evidence) if isinstance(evidence, (dict, list)) else evidence

        sql = """
        UPDATE places SET
            website_status = :status,
            website_candidate = :candidate,
            website_discovery_method = :discovery_method,
            website_evidence = :evidence,
            website_error = :error,
            website_last_checked = CURRENT_TIMESTAMP,
            official_website_url = CASE
                WHEN :website IS NOT NULL AND :status = 'verified' THEN :website
                WHEN :candidate IS NOT NULL AND :status = 'verified' THEN :candidate
                WHEN official_website_url IS NOT NULL THEN official_website_url
                ELSE :website
            END,
            website = CASE
                WHEN :website IS NOT NULL AND :status = 'verified' THEN :website
                WHEN website IS NOT NULL THEN website
                ELSE :website
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE cid = :cid;
        """
        params = {
            "cid": cid,
            "status": status,
            "candidate": candidate,
            "discovery_method": discovery_method,
            "evidence": evidence_json,
            "error": error,
            "website": website,
        }
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount > 0

    def get_website_discovery_stats(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """Returns aggregation of website discovery and verification statuses."""
        query = "SELECT website_status, count(*) as cnt FROM places WHERE 1=1"
        params: List[Any] = []
        if country_code:
            query += " AND country_code = ?"
            params.append(country_code.upper())
        if region_code:
            query += " AND (region = ? OR region = ?)"
            params.extend([region_code.upper(), region_code.lower()])
        query += " GROUP BY website_status"

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            counts = {r["website_status"] or "unverified": r["cnt"] for r in rows}
            total = sum(counts.values())
            return {
                "total_places": total,
                "verified": counts.get("verified", 0),
                "needs_review": counts.get("needs_review", 0),
                "website_missing": counts.get("website_missing", 0),
                "failed": counts.get("failed", 0),
                "candidate": counts.get("candidate", 0),
                "unverified": counts.get("unverified", 0),
            }

    # -------------------------------------------------------------------------
    # Company Website Enrichment: Profile, Leadership & Contacts (Step 3)
    # -------------------------------------------------------------------------

    def get_verified_businesses_for_enrichment(
        self,
        country_code: str,
        region_code: Optional[str] = None,
        limit: int = 50,
        retry_failed: bool = False,
        force: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Retrieves businesses strictly with verified official websites for Step 3 enrichment.
        Excludes unverified, failed, or missing websites unless forced.
        """
        query = """
            SELECT * FROM places
            WHERE country_code = ?
              AND website_status = 'verified'
              AND (
                  (official_website_url IS NOT NULL AND official_website_url != '')
                  OR
                  (website IS NOT NULL AND website != '')
              )
        """
        params: List[Any] = [country_code.upper()]

        if region_code:
            query += " AND (region = ? OR region = ?)"
            params.extend([region_code.upper(), region_code.lower()])

        if force:
            pass
        elif retry_failed:
            query += " AND (enrichment_status IN ('failed', 'partial', 'no_data') OR enrichment_status IS NULL)"
        else:
            query += " AND (enrichment_status IS NULL OR enrichment_status = 'pending')"

        query += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def save_company_profile(self, cid: str, profile_data: Optional[Dict[str, Any]] = None, **kwargs) -> bool:
        """Upserts company overview, services, locations, and source pages for a business."""
        data = {**(profile_data or {}), **kwargs}
        sql = """
        INSERT INTO company_profiles (
            cid, overview, industry, services_json, locations_json,
            contact_page_url, source_pages_json, extraction_status,
            last_extracted, last_error, created_at, updated_at
        ) VALUES (
            :cid, :overview, :industry, :services_json, :locations_json,
            :contact_page_url, :source_pages_json, :extraction_status,
            CURRENT_TIMESTAMP, :last_error, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT(cid) DO UPDATE SET
            overview = COALESCE(excluded.overview, company_profiles.overview),
            industry = COALESCE(excluded.industry, company_profiles.industry),
            services_json = COALESCE(excluded.services_json, company_profiles.services_json),
            locations_json = COALESCE(excluded.locations_json, company_profiles.locations_json),
            contact_page_url = COALESCE(excluded.contact_page_url, company_profiles.contact_page_url),
            source_pages_json = COALESCE(excluded.source_pages_json, company_profiles.source_pages_json),
            extraction_status = excluded.extraction_status,
            last_extracted = CURRENT_TIMESTAMP,
            last_error = excluded.last_error,
            updated_at = CURRENT_TIMESTAMP;
        """
        params = {
            "cid": cid,
            "overview": data.get("overview"),
            "industry": data.get("industry"),
            "services_json": json.dumps(data.get("services", [])) if isinstance(data.get("services"), (list, dict)) else data.get("services_json"),
            "locations_json": json.dumps(data.get("locations", [])) if isinstance(data.get("locations"), (list, dict)) else data.get("locations_json"),
            "contact_page_url": data.get("contact_page_url"),
            "source_pages_json": json.dumps(data.get("source_pages", [])) if isinstance(data.get("source_pages"), (list, dict)) else data.get("source_pages_json"),
            "extraction_status": data.get("extraction_status", "completed"),
            "last_error": data.get("last_error"),
        }
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            # Update place status
            conn.execute("""
                UPDATE places
                SET enrichment_status = :status,
                    enrichment_last_checked = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE cid = :cid
            """, {"status": data.get("extraction_status", "completed"), "cid": cid})
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount > 0

    def save_company_leaders(self, cid: str, leaders: List[Dict[str, Any]]) -> int:
        """Idempotently saves public company leadership records, preventing duplicates on re-runs."""
        if not leaders:
            return 0

        sql = """
        INSERT INTO company_leaders (
            cid, full_name, title, role_type, biography, source_url, confidence,
            created_at, updated_at
        ) VALUES (
            :cid, :full_name, :title, :role_type, :biography, :source_url, :confidence,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT(cid, full_name, title) DO UPDATE SET
            role_type = COALESCE(excluded.role_type, company_leaders.role_type),
            biography = COALESCE(excluded.biography, company_leaders.biography),
            source_url = COALESCE(excluded.source_url, company_leaders.source_url),
            confidence = excluded.confidence,
            updated_at = CURRENT_TIMESTAMP;
        """
        count = 0
        with self.get_connection() as conn:
            for ldr in leaders:
                conf_raw = ldr.get("confidence", 1.0)
                if isinstance(conf_raw, (int, float)):
                    conf_val = float(conf_raw)
                elif isinstance(conf_raw, str) and conf_raw.replace('.', '', 1).isdigit():
                    conf_val = float(conf_raw)
                elif isinstance(conf_raw, str):
                    conf_map = {"high": 0.95, "medium": 0.75, "low": 0.5}
                    conf_val = conf_map.get(conf_raw.lower(), 0.8)
                else:
                    conf_val = 1.0

                params = {
                    "cid": cid,
                    "full_name": ldr["full_name"],
                    "title": ldr["title"],
                    "role_type": ldr.get("role_type"),
                    "biography": ldr.get("biography"),
                    "source_url": ldr.get("source_url"),
                    "confidence": conf_val,
                }
                cur = conn.execute(sql, params)
                if cur.rowcount > 0:
                    count += 1
            conn.commit()
            self._last_commit_time = datetime.now()
            return count

    def save_company_contacts(self, cid: str, contacts: List[Dict[str, Any]]) -> int:
        """Idempotently saves public company business contacts, preventing duplicates on re-runs."""
        if not contacts:
            return 0

        sql = """
        INSERT INTO company_contacts (
            cid, contact_type, contact_value, label, source_url,
            created_at, updated_at
        ) VALUES (
            :cid, :contact_type, :contact_value, :label, :source_url,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT(cid, contact_type, contact_value) DO UPDATE SET
            label = COALESCE(excluded.label, company_contacts.label),
            source_url = COALESCE(excluded.source_url, company_contacts.source_url),
            updated_at = CURRENT_TIMESTAMP;
        """
        count = 0
        with self.get_connection() as conn:
            for c in contacts:
                params = {
                    "cid": cid,
                    "contact_type": c["contact_type"],
                    "contact_value": c["contact_value"],
                    "label": c.get("label", "general"),
                    "source_url": c.get("source_url"),
                }
                cur = conn.execute(sql, params)
                if cur.rowcount > 0:
                    count += 1
            conn.commit()
            self._last_commit_time = datetime.now()
            return count

    def get_company_enrichment(self, cid: str) -> Dict[str, Any]:
        """Retrieves full company profile, leadership, and contacts for a business."""
        with self.get_connection() as conn:
            prof_row = conn.execute("SELECT * FROM company_profiles WHERE cid = ?", (cid,)).fetchone()
            profile = dict(prof_row) if prof_row else None
            if profile:
                profile["services"] = json.loads(profile["services_json"]) if profile.get("services_json") else []
                profile["locations"] = json.loads(profile["locations_json"]) if profile.get("locations_json") else []
                profile["source_pages"] = json.loads(profile["source_pages_json"]) if profile.get("source_pages_json") else []

            leaders = [dict(r) for r in conn.execute("SELECT * FROM company_leaders WHERE cid = ? ORDER BY id ASC", (cid,)).fetchall()]
            contacts = [dict(r) for r in conn.execute("SELECT * FROM company_contacts WHERE cid = ? ORDER BY id ASC", (cid,)).fetchall()]

            return {
                "cid": cid,
                "profile": profile,
                "leaders": leaders,
                "contacts": contacts
            }

    def get_company_leaders(self, cid: str) -> List[Dict[str, Any]]:
        """Retrieves list of public company leaders for a business."""
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM company_leaders WHERE cid = ? ORDER BY id ASC", (cid,)).fetchall()
            return [dict(r) for r in rows]

    def get_company_contacts(self, cid: str) -> List[Dict[str, Any]]:
        """Retrieves list of public company contacts for a business."""
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM company_contacts WHERE cid = ? ORDER BY id ASC", (cid,)).fetchall()
            return [dict(r) for r in rows]

    def get_enrichment_stats(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """Returns aggregation of company enrichment statuses and counts."""
        query = "SELECT enrichment_status, count(*) as cnt FROM places WHERE 1=1"
        params: List[Any] = []
        if country_code:
            query += " AND country_code = ?"
            params.append(country_code.upper())
        if region_code:
            query += " AND (region = ? OR region = ?)"
            params.extend([region_code.upper(), region_code.lower()])
        query += " GROUP BY enrichment_status"

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            counts = {r["enrichment_status"] or "pending": r["cnt"] for r in rows}
            total = sum(counts.values())

            prof_cnt = conn.execute("SELECT COUNT(DISTINCT cid) FROM company_profiles").fetchone()[0]
            lead_cnt = conn.execute("SELECT COUNT(DISTINCT cid) FROM company_leaders").fetchone()[0]
            contact_cnt = conn.execute("SELECT COUNT(DISTINCT cid) FROM company_contacts").fetchone()[0]

            return {
                "total_places": total,
                "completed": counts.get("completed", 0),
                "partial": counts.get("partial", 0),
                "failed": counts.get("failed", 0),
                "pending": counts.get("pending", 0),
                "no_data": counts.get("no_data", 0),
                "company_profiles_count": prof_cnt,
                "company_leaders_count": lead_cnt,
                "company_contacts_count": contact_cnt,
            }

    # -------------------------------------------------------------------------
    # Automated Pipeline 2 Orchestration Persistence
    # -------------------------------------------------------------------------

    def save_pipeline_config(self, config: Dict[str, Any], config_id: str = "default") -> bool:
        """Persists user pipeline configuration to SQLite so it survives restarts."""
        cfg_str = json.dumps(config)
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO pipeline_configs (id, config_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    config_json = excluded.config_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (config_id, cfg_str))
            conn.commit()
            self._last_commit_time = datetime.now()
            return True

    def get_pipeline_config(self, config_id: str = "default") -> Optional[Dict[str, Any]]:
        """Retrieves persisted user pipeline configuration."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT config_json FROM pipeline_configs WHERE id = ?", (config_id,)).fetchone()
            if not row:
                return None
            try:
                return json.loads(row["config_json"])
            except Exception:
                return None

    def create_pipeline_run(self, run_data: Dict[str, Any]) -> bool:
        """Inserts a new pipeline orchestration run record."""
        sql = """
        INSERT INTO pipeline_runs (
            run_id, status, config_json, current_stage, current_country,
            current_region, current_category, total_tasks, completed_tasks,
            failed_tasks, businesses_collected, websites_verified,
            companies_enriched, dry_run, error_message, started_at, updated_at
        ) VALUES (
            :run_id, :status, :config_json, :current_stage, :current_country,
            :current_region, :current_category, :total_tasks, :completed_tasks,
            :failed_tasks, :businesses_collected, :websites_verified,
            :companies_enriched, :dry_run, :error_message, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """
        params = {
            "run_id": run_data["run_id"],
            "status": run_data.get("status", "running"),
            "config_json": json.dumps(run_data.get("config", {})),
            "current_stage": run_data.get("current_stage", "stage_1_collection"),
            "current_country": run_data.get("current_country"),
            "current_region": run_data.get("current_region"),
            "current_category": run_data.get("current_category"),
            "total_tasks": int(run_data.get("total_tasks", 0)),
            "completed_tasks": int(run_data.get("completed_tasks", 0)),
            "failed_tasks": int(run_data.get("failed_tasks", 0)),
            "businesses_collected": int(run_data.get("businesses_collected", 0)),
            "websites_verified": int(run_data.get("websites_verified", 0)),
            "companies_enriched": int(run_data.get("companies_enriched", 0)),
            "dry_run": 1 if run_data.get("dry_run") else 0,
            "error_message": run_data.get("error_message"),
        }
        with self.get_connection() as conn:
            conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return True

    def update_pipeline_run(self, run_id: str, updates: Dict[str, Any]) -> bool:
        """Updates pipeline run status, progress metrics, and checkpoints."""
        allowed_fields = [
            "status", "current_stage", "current_country", "current_region",
            "current_category", "total_tasks", "completed_tasks", "failed_tasks",
            "businesses_collected", "websites_verified", "companies_enriched",
            "error_message", "started_at", "paused_at", "completed_at"
        ]
        set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = []
        for f in allowed_fields:
            if f in updates:
                set_clauses.append(f"{f} = ?")
                val = updates[f]
                if f == "status" and val == "completed" and "completed_at" not in updates:
                    set_clauses.append("completed_at = CURRENT_TIMESTAMP")
                elif f == "status" and val == "paused" and "paused_at" not in updates:
                    set_clauses.append("paused_at = CURRENT_TIMESTAMP")
                params.append(val)

        if len(set_clauses) == 1:
            return False

        sql = f"UPDATE pipeline_runs SET {', '.join(set_clauses)} WHERE run_id = ?"
        params.append(run_id)

        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount > 0

    def get_pipeline_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["config"] = json.loads(d["config_json"])
            except Exception:
                d["config"] = {}
            return d

    def get_latest_pipeline_run(self) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["config"] = json.loads(d["config_json"])
            except Exception:
                d["config"] = {}
            return d

    def list_pipeline_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["config"] = json.loads(d["config_json"])
                except Exception:
                    d["config"] = {}
                out.append(d)
            return out

    def get_pipeline_task_counts(self) -> Dict[str, int]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT status, count(*) as cnt FROM collection_tasks GROUP BY status").fetchall()
            counts = {r["status"]: r["cnt"] for r in rows}
            return {
                "total": sum(counts.values()),
                "pending": counts.get("pending", 0),
                "in_progress": counts.get("in_progress", 0),
                "completed": counts.get("completed", 0),
                "failed": counts.get("failed", 0),
            }

    def get_all_regions_progress(self, country_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """Aggregates progress for each region across collection_tasks."""
        sql = """
            SELECT country_code, region_code, region_name, status, COUNT(*) as cnt
            FROM collection_tasks
        """
        params: List[Any] = []
        if country_code:
            sql += " WHERE country_code = ?"
            params.append(country_code.upper())
        sql += " GROUP BY country_code, region_code, region_name, status ORDER BY country_code, region_code"

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            region_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for r in rows:
                key = (r["country_code"], r["region_code"])
                if key not in region_map:
                    region_map[key] = {
                        "country_code": r["country_code"],
                        "region_code": r["region_code"],
                        "region_name": r["region_name"],
                        "total": 0,
                        "completed": 0,
                        "pending": 0,
                        "failed": 0,
                        "in_progress": 0,
                        "is_complete": False
                    }
                cnt = r["cnt"]
                st = r["status"]
                region_map[key]["total"] += cnt
                if st in region_map[key]:
                    region_map[key][st] += cnt

            for reg in region_map.values():
                reg["is_complete"] = (reg["total"] > 0 and reg["completed"] == reg["total"])

            return list(region_map.values())

    # -------------------------------------------------------------------------
    # Geographic Tile CRUD and Metrics (Section 3 & Section 8)
    # -------------------------------------------------------------------------

    def create_geographic_tile(self, tile_data: Any) -> bool:
        """Inserts a new geographic tile idempotently."""
        if hasattr(tile_data, "__dataclass_fields__"):
            from dataclasses import asdict
            tile_data = asdict(tile_data)
        elif not isinstance(tile_data, dict):
            tile_data = dict(tile_data)

        sql = """
        INSERT INTO geographic_tiles (
            tile_id, parent_tile_id, task_id, run_id, country_code, region_code,
            category, min_lat, max_lat, min_lng, max_lng, center_lat, center_lng,
            span_deg, depth, status, attempts, retry_count, results_count,
            new_records, updated_records, duplicate_cids, last_error, http_status,
            subdivision_status, last_checkpoint, started_at, completed_at,
            created_at, updated_at
        ) VALUES (
            :tile_id, :parent_tile_id, :task_id, :run_id, :country_code, :region_code,
            :category, :min_lat, :max_lat, :min_lng, :max_lng, :center_lat, :center_lng,
            :span_deg, :depth, :status, :attempts, :retry_count, :results_count,
            :new_records, :updated_records, :duplicate_cids, :last_error, :http_status,
            :subdivision_status, :last_checkpoint, :started_at, :completed_at,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT(tile_id) DO UPDATE SET
            status = excluded.status,
            attempts = excluded.attempts,
            retry_count = excluded.retry_count,
            results_count = excluded.results_count,
            new_records = excluded.new_records,
            updated_records = excluded.updated_records,
            duplicate_cids = excluded.duplicate_cids,
            last_error = excluded.last_error,
            http_status = excluded.http_status,
            subdivision_status = excluded.subdivision_status,
            last_checkpoint = excluded.last_checkpoint,
            started_at = COALESCE(excluded.started_at, geographic_tiles.started_at),
            completed_at = COALESCE(excluded.completed_at, geographic_tiles.completed_at),
            updated_at = CURRENT_TIMESTAMP
        """
        params = {
            "tile_id": tile_data["tile_id"],
            "parent_tile_id": tile_data.get("parent_tile_id"),
            "task_id": tile_data.get("task_id", ""),
            "run_id": tile_data.get("run_id") or "run_default",
            "country_code": tile_data["country_code"].upper(),
            "region_code": tile_data["region_code"].upper(),
            "category": tile_data["category"],
            "min_lat": float(tile_data["min_lat"]),
            "max_lat": float(tile_data["max_lat"]),
            "min_lng": float(tile_data["min_lng"]),
            "max_lng": float(tile_data["max_lng"]),
            "center_lat": float(tile_data["center_lat"]),
            "center_lng": float(tile_data["center_lng"]),
            "span_deg": float(tile_data["span_deg"]),
            "depth": int(tile_data.get("depth", 0)),
            "status": tile_data.get("status", "pending"),
            "attempts": int(tile_data.get("attempts", 0)),
            "retry_count": int(tile_data.get("retry_count", 0)),
            "results_count": int(tile_data.get("results_count", 0)),
            "new_records": int(tile_data.get("new_records", 0)),
            "updated_records": int(tile_data.get("updated_records", 0)),
            "duplicate_cids": int(tile_data.get("duplicate_cids", 0)),
            "last_error": tile_data.get("last_error"),
            "http_status": tile_data.get("http_status"),
            "subdivision_status": tile_data.get("subdivision_status"),
            "last_checkpoint": tile_data.get("last_checkpoint"),
            "started_at": tile_data.get("started_at"),
            "completed_at": tile_data.get("completed_at"),
        }
        with self.get_connection() as conn:
            conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return True

    def update_geographic_tile(self, tile_id: str, updates: Dict[str, Any]) -> bool:
        """Updates fields on an existing geographic tile."""
        allowed = [
            "status", "attempts", "retry_count", "results_count",
            "new_records", "updated_records", "duplicate_cids", "last_error",
            "http_status", "subdivision_status", "last_checkpoint",
            "started_at", "completed_at"
        ]
        set_clauses = ["updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = []
        for f in allowed:
            if f in updates:
                set_clauses.append(f"{f} = ?")
                params.append(updates[f])

        if len(set_clauses) == 1:
            return False

        sql = f"UPDATE geographic_tiles SET {', '.join(set_clauses)} WHERE tile_id = ?"
        params.append(tile_id)
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount > 0

    def get_geographic_tile(self, tile_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM geographic_tiles WHERE tile_id = ?", (tile_id,)).fetchone()
            return dict(row) if row else None

    def get_geographic_tiles(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        limit: int = 2000,
        **kwargs: Any
    ) -> List[Dict[str, Any]]:
        """Retrieves geographic tiles matching filter criteria."""
        cc = country_code or kwargs.get("country")
        rc = region_code or kwargs.get("region")
        conditions = []
        params: List[Any] = []
        if cc:
            conditions.append("country_code = ?")
            params.append(cc.upper())
        if rc:
            conditions.append("region_code = ?")
            params.append(rc.upper())
        if category:
            conditions.append("category = ?")
            params.append(category)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM geographic_tiles {where_clause} ORDER BY depth ASC, tile_id ASC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_pipeline_tile_counts(
        self,
        run_id: Optional[str] = None,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        **kwargs: Any
    ) -> Dict[str, int]:
        """Returns counts of geographic tiles by status."""
        cc = country_code or kwargs.get("country")
        rc = region_code or kwargs.get("region")
        conditions = []
        params: List[Any] = []
        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if cc:
            conditions.append("country_code = ?")
            params.append(cc.upper())
        if rc:
            conditions.append("region_code = ?")
            params.append(rc.upper())

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT status, count(*) as cnt FROM geographic_tiles {where} GROUP BY status"

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            counts = {r["status"]: r["cnt"] for r in rows}
            total = sum(counts.values())
            return {
                "total": total,
                "pending": counts.get("pending", 0),
                "running": counts.get("running", 0),
                "completed": counts.get("completed", 0),
                "subdivided": counts.get("subdivided", 0),
                "retry_pending": counts.get("retry_pending", 0),
                "failed": counts.get("failed", 0),
                "limit_reached": counts.get("limit_reached", 0),
            }

    def reset_stale_running_tiles(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
    ) -> int:
        """Resets tiles stuck in 'running' state (from a crashed/killed session) back to 'pending'."""
        conditions = ["status = 'running'"]
        params: List[Any] = []
        if country_code:
            conditions.append("country_code = ?")
            params.append(country_code.upper())
        if region_code:
            conditions.append("region_code = ?")
            params.append(region_code.upper())
        sql = f"""
            UPDATE geographic_tiles
            SET status = 'pending', updated_at = CURRENT_TIMESTAMP
            WHERE {' AND '.join(conditions)}
        """
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount

    def reset_stale_inprogress_tasks(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
    ) -> int:
        """Resets collection tasks stuck in 'in_progress' (from a crashed session) back to 'pending'."""
        conditions = ["status = 'in_progress'"]
        params: List[Any] = []
        if country_code:
            conditions.append("country_code = ?")
            params.append(country_code.upper())
        if region_code:
            conditions.append("region_code = ?")
            params.append(region_code.upper())
        sql = f"UPDATE collection_tasks SET status = 'pending', updated_at = CURRENT_TIMESTAMP WHERE {' AND '.join(conditions)}"
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount

    def reset_failed_tiles(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        task_id: Optional[str] = None
    ) -> int:
        """Resets failed and retry_pending geographic tiles to 'pending'."""
        conditions = ["status IN ('failed', 'retry_pending')"]
        params: List[Any] = []
        if country_code:
            conditions.append("country_code = ?")
            params.append(country_code.upper())
        if region_code:
            conditions.append("region_code = ?")
            params.append(region_code.upper())
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)

        sql = f"""
            UPDATE geographic_tiles
            SET status = 'pending', attempts = 0, last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE {' AND '.join(conditions)}
        """
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.rowcount

    # -------------------------------------------------------------------------
    # Incremental Business Intelligence Methods
    # -------------------------------------------------------------------------

    def save_incremental_finding(
        self,
        cid: str,
        finding_data: Dict[str, Any]
    ) -> Tuple[int, bool]:
        """
        Saves or updates an incremental business intelligence finding.
        Returns (id, is_new_record: bool).
        """
        sql = """
        INSERT INTO additional_intelligence (
            cid, place_id, company_name, entity_type, person_name, job_title,
            field_name, field_value, normalized_value, match_status,
            verification_status, confidence, conflict_existing_value, match_reason,
            first_seen_at, last_checked_at
        ) VALUES (
            :cid, :place_id, :company_name, :entity_type, :person_name, :job_title,
            :field_name, :field_value, :normalized_value, :match_status,
            :verification_status, :confidence, :conflict_existing_value, :match_reason,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT(cid, entity_type, field_name, normalized_value) DO UPDATE SET
            last_checked_at = CURRENT_TIMESTAMP,
            confidence = excluded.confidence,
            verification_status = excluded.verification_status,
            match_status = excluded.match_status,
            conflict_existing_value = COALESCE(excluded.conflict_existing_value, additional_intelligence.conflict_existing_value),
            match_reason = excluded.match_reason;
        """
        params = {
            "cid": cid,
            "place_id": finding_data.get("place_id"),
            "company_name": finding_data.get("company_name"),
            "entity_type": finding_data.get("entity_type", "generic"),
            "person_name": finding_data.get("person_name"),
            "job_title": finding_data.get("job_title"),
            "field_name": finding_data.get("field_name"),
            "field_value": finding_data.get("field_value"),
            "normalized_value": finding_data.get("normalized_value") or finding_data.get("field_value"),
            "match_status": finding_data.get("match_status", "NEW_INFORMATION"),
            "verification_status": finding_data.get("verification_status", "unverified"),
            "confidence": float(finding_data.get("confidence", 1.0)),
            "conflict_existing_value": finding_data.get("conflict_existing_value"),
            "match_reason": finding_data.get("match_reason"),
        }

        with self.get_connection() as conn:
            # Check if row existed before
            existing_row = conn.execute(
                "SELECT id FROM additional_intelligence WHERE cid = ? AND entity_type = ? AND field_name = ? AND normalized_value = ?",
                (cid, params["entity_type"], params["field_name"], params["normalized_value"])
            ).fetchone()

            conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()

            row = conn.execute(
                "SELECT id FROM additional_intelligence WHERE cid = ? AND entity_type = ? AND field_name = ? AND normalized_value = ?",
                (cid, params["entity_type"], params["field_name"], params["normalized_value"])
            ).fetchone()
            intel_id = row["id"] if row else 0
            is_new = (existing_row is None)
            return intel_id, is_new

    def record_intelligence_source(
        self,
        cid: str,
        source_data: Dict[str, Any],
        intelligence_id: Optional[int] = None
    ) -> int:
        """
        Records source verification/evidence history for an incremental finding.
        """
        sql = """
        INSERT INTO intelligence_sources (
            intelligence_id, cid, source_url, source_type, source_title,
            retrieved_at, evidence_summary, status, error_message
        ) VALUES (
            :intelligence_id, :cid, :source_url, :source_type, :source_title,
            CURRENT_TIMESTAMP, :evidence_summary, :status, :error_message
        )
        """
        params = {
            "intelligence_id": intelligence_id,
            "cid": cid,
            "source_url": source_data.get("source_url", ""),
            "source_type": source_data.get("source_type", "company_website"),
            "source_title": source_data.get("source_title"),
            "evidence_summary": source_data.get("evidence_summary"),
            "status": source_data.get("status", "success"),
            "error_message": source_data.get("error_message"),
        }
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            self._last_commit_time = datetime.now()
            return cur.lastrowid

    def get_additional_intelligence(
        self,
        cid: Optional[str] = None,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        match_status: Optional[str] = None,
        entity_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Retrieves additional intelligence findings with total count and join to places.
        """
        conditions: List[str] = []
        params: List[Any] = []

        if cid:
            conditions.append("ai.cid = ?")
            params.append(cid)
        if country_code:
            conditions.append("p.country_code = ?")
            params.append(country_code.upper())
        if region_code:
            conditions.append("(p.region = ? OR p.region = ?)")
            params.extend([region_code.upper(), region_code.lower()])
        if match_status:
            conditions.append("ai.match_status = ?")
            params.append(match_status)
        if entity_type:
            conditions.append("ai.entity_type = ?")
            params.append(entity_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_sql = f"""
            SELECT COUNT(*) as total
            FROM additional_intelligence ai
            JOIN places p ON ai.cid = p.cid
            {where_clause}
        """

        select_sql = f"""
            SELECT
                ai.*,
                p.name as place_name,
                p.country_code,
                p.region,
                p.address as place_address,
                p.website as place_website,
                p.official_website_url,
                s.source_url,
                s.source_type,
                s.source_title
            FROM additional_intelligence ai
            JOIN places p ON ai.cid = p.cid
            LEFT JOIN intelligence_sources s ON s.intelligence_id = ai.id
            {where_clause}
            GROUP BY ai.id
            ORDER BY ai.last_checked_at DESC
            LIMIT ? OFFSET ?
        """

        with self.get_connection() as conn:
            total = conn.execute(count_sql, params).fetchone()["total"]
            p_params = list(params) + [limit, offset]
            rows = conn.execute(select_sql, p_params).fetchall()
            return [dict(r) for r in rows], total

    def get_intelligence_sources(
        self,
        cid: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Retrieves source access log and evidence history."""
        conditions: List[str] = []
        params: List[Any] = []

        if cid:
            conditions.append("cid = ?")
            params.append(cid)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT * FROM intelligence_sources
            {where_clause}
            ORDER BY retrieved_at DESC
            LIMIT ?
        """
        params.append(limit)
        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_intelligence_summary(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """Calculates KPI summary metrics for incremental intelligence dashboard."""
        conditions: List[str] = []
        params: List[Any] = []

        if country_code:
            conditions.append("p.country_code = ?")
            params.append(country_code.upper())
        if region_code:
            conditions.append("(p.region = ? OR p.region = ?)")
            params.extend([region_code.upper(), region_code.lower()])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.get_connection() as conn:
            # Breakdown by match_status
            status_rows = conn.execute(f"""
                SELECT ai.match_status, count(*) as cnt
                FROM additional_intelligence ai
                JOIN places p ON ai.cid = p.cid
                {where_clause}
                GROUP BY ai.match_status
            """, params).fetchall()
            status_counts = {r["match_status"]: r["cnt"] for r in status_rows}

            # Breakdown by entity_type
            entity_rows = conn.execute(f"""
                SELECT ai.entity_type, count(*) as cnt
                FROM additional_intelligence ai
                JOIN places p ON ai.cid = p.cid
                {where_clause}
                GROUP BY ai.entity_type
            """, params).fetchall()
            entity_counts = {r["entity_type"]: r["cnt"] for r in entity_rows}

            # Count unique businesses with findings
            biz_row = conn.execute(f"""
                SELECT count(DISTINCT ai.cid) as cnt
                FROM additional_intelligence ai
                JOIN places p ON ai.cid = p.cid
                {where_clause}
            """, params).fetchone()
            businesses_with_intel = biz_row["cnt"] if biz_row else 0

            # Source statistics
            src_conditions = []
            src_params = []
            if country_code or region_code:
                src_conditions.append("s.cid IN (SELECT cid FROM places p " + where_clause + ")")
                src_params.extend(params)

            src_where = f"WHERE {' AND '.join(src_conditions)}" if src_conditions else ""
            src_rows = conn.execute(f"""
                SELECT s.status, count(*) as cnt
                FROM intelligence_sources s
                {src_where}
                GROUP BY s.status
            """, src_params).fetchall()
            source_status_counts = {r["status"]: r["cnt"] for r in src_rows}

            # Leaders vs Contacts new counts
            new_leaders_row = conn.execute(f"""
                SELECT count(*) as cnt
                FROM additional_intelligence ai
                JOIN places p ON ai.cid = p.cid
                {where_clause} {'AND' if where_clause else 'WHERE'} ai.entity_type = 'leader' AND ai.match_status = 'NEW_INFORMATION'
            """, params).fetchone()

            new_contacts_row = conn.execute(f"""
                SELECT count(*) as cnt
                FROM additional_intelligence ai
                JOIN places p ON ai.cid = p.cid
                {where_clause} {'AND' if where_clause else 'WHERE'} ai.entity_type = 'contact' AND ai.match_status = 'NEW_INFORMATION'
            """, params).fetchone()

            return {
                "total_findings": sum(status_counts.values()),
                "status_counts": status_counts,
                "entity_counts": entity_counts,
                "businesses_with_intel": businesses_with_intel,
                "new_findings": status_counts.get("NEW_INFORMATION", 0),
                "exact_matches": status_counts.get("EXACT_MATCH", 0),
                "conflicts": status_counts.get("CONFLICT", 0),
                "possible_matches": status_counts.get("POSSIBLE_MATCH", 0),
                "unverified": status_counts.get("UNVERIFIED", 0),
                "new_leaders": new_leaders_row["cnt"] if new_leaders_row else 0,
                "new_contacts": new_contacts_row["cnt"] if new_contacts_row else 0,
                "sources_checked": sum(source_status_counts.values()),
                "sources_succeeded": source_status_counts.get("success", 0),
                "sources_no_data": source_status_counts.get("no_data", 0),
                "sources_failed": source_status_counts.get("failed", 0) + source_status_counts.get("blocked", 0),
            }

    def get_last_commit_timestamp(self) -> Optional[str]:
        """Returns the local time string (HH:MM:SS) of the most recent database commit."""
        if self._last_commit_time is not None:
            return self._last_commit_time.strftime("%H:%M:%S")
        with self.get_connection() as conn:
            row = conn.execute("""
                SELECT MAX(t) as max_t FROM (
                    SELECT datetime(MAX(collected_at), 'localtime') as t FROM raw_places
                    UNION ALL
                    SELECT datetime(MAX(updated_at), 'localtime') as t FROM geographic_tiles
                )
            """).fetchone()
            if row and row["max_t"]:
                parts = str(row["max_t"]).split()
                if len(parts) > 1:
                    return parts[1]
                return str(row["max_t"])
        return None

    def get_collection_progress_stats(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        configured_categories: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Calculates live collection progress statistics:
        - raw_saved_today (based on raw_places.collected_at in local time)
        - total_raw (count in raw_places)
        - total_canonical (count in places)
        - Tile breakdown:
            - planned_root (root tiles)
            - subdivided_parents (status = 'subdivided')
            - child_tiles_awaiting (child tiles awaiting processing)
            - completed, limit_reached, pending, running, retry_pending, failed
            - finished_tiles (completed + limit_reached)
            - active_total (finished_tiles + pending + running + retry_pending + failed)
            - remaining_tiles (pending + running + retry_pending + failed)
            - in_flight_tiles (pending + running + retry_pending)
            - can_grow (in_flight_tiles > 0)
        - Tasks breakdown:
            - total_categories
            - completed_categories
            - in_progress_categories
            - failed_categories
            - pending_categories
        - Category-by-category tile breakdown, raw records, and status
        - last_db_write (HH:MM:SS)
        - active db_path and today_date
        """
        with self.get_connection() as conn:
            today_row = conn.execute(
                "SELECT COUNT(*) FROM raw_places WHERE date(collected_at, 'localtime') = date('now', 'localtime')"
            ).fetchone()
            raw_today = today_row[0] if today_row else 0

            raw_total_row = conn.execute("SELECT COUNT(*) FROM raw_places").fetchone()
            raw_total = raw_total_row[0] if raw_total_row else 0

            can_total_row = conn.execute("SELECT COUNT(*) FROM places").fetchone()
            canonical_total = can_total_row[0] if can_total_row else 0

            tile_rows = conn.execute("""
                SELECT
                    status,
                    COUNT(*) as cnt,
                    SUM(CASE WHEN parent_tile_id IS NOT NULL AND parent_tile_id != '' THEN 1 ELSE 0 END) as child_cnt,
                    SUM(CASE WHEN parent_tile_id IS NULL OR parent_tile_id = '' THEN 1 ELSE 0 END) as root_cnt
                FROM geographic_tiles
                WHERE (? IS NULL OR UPPER(region_code) = UPPER(?))
                  AND (? IS NULL OR UPPER(country_code) = UPPER(?))
                GROUP BY status
            """, (region_code, region_code, country_code, country_code)).fetchall()
            by_status = {r["status"]: dict(r) for r in tile_rows}

            cat_tile_rows = conn.execute("""
                SELECT
                    category,
                    status,
                    COUNT(*) as cnt,
                    SUM(CASE WHEN parent_tile_id IS NOT NULL AND parent_tile_id != '' THEN 1 ELSE 0 END) as child_cnt,
                    SUM(CASE WHEN parent_tile_id IS NULL OR parent_tile_id = '' THEN 1 ELSE 0 END) as root_cnt,
                    SUM(new_records) as new_recs,
                    SUM(duplicate_cids) as dup_cids
                FROM geographic_tiles
                WHERE (? IS NULL OR UPPER(region_code) = UPPER(?))
                  AND (? IS NULL OR UPPER(country_code) = UPPER(?))
                GROUP BY category, status
            """, (region_code, region_code, country_code, country_code)).fetchall()

            task_row = conn.execute("""
                SELECT
                    COUNT(*) as total_categories,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_categories,
                    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress_categories,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_categories,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_categories
                FROM collection_tasks
                WHERE (? IS NULL OR UPPER(region_code) = UPPER(?))
                  AND (? IS NULL OR UPPER(country_code) = UPPER(?))
            """, (region_code, region_code, country_code, country_code)).fetchone()

            cat_task_rows = conn.execute("""
                SELECT category, status, records_collected, last_error
                FROM collection_tasks
                WHERE (? IS NULL OR UPPER(region_code) = UPPER(?))
                  AND (? IS NULL OR UPPER(country_code) = UPPER(?))
            """, (region_code, region_code, country_code, country_code)).fetchall()

            cat_raw_place_rows = conn.execute("""
                SELECT
                    COALESCE(NULLIF(vertical, 'other'), NULLIF(vertical_raw, ''), category) as cat,
                    COUNT(*) as cnt
                FROM raw_places
                WHERE (? IS NULL OR UPPER(region) = UPPER(?))
                  AND (? IS NULL OR UPPER(country_code) = UPPER(?))
                GROUP BY cat
            """, (region_code, region_code, country_code, country_code)).fetchall()
            raw_places_by_cat = {r["cat"]: r["cnt"] for r in cat_raw_place_rows if r["cat"]}

        completed = by_status.get("completed", {}).get("cnt", 0)
        limit_reached = by_status.get("limit_reached", {}).get("cnt", 0)
        pending = by_status.get("pending", {}).get("cnt", 0)
        running = by_status.get("running", {}).get("cnt", 0)
        retry_pending = by_status.get("retry_pending", {}).get("cnt", 0)
        failed = by_status.get("failed", {}).get("cnt", 0)
        subdivided = by_status.get("subdivided", {}).get("cnt", 0)

        planned_root = sum(v.get("root_cnt", 0) for v in by_status.values())
        child_tiles_awaiting = sum(
            by_status.get(s, {}).get("child_cnt", 0)
            for s in ("pending", "running", "retry_pending")
        )

        finished_tiles = completed + limit_reached
        active_total = finished_tiles + pending + running + retry_pending + failed
        remaining_tiles = pending + running + retry_pending + failed
        in_flight_tiles = pending + running + retry_pending
        can_grow = in_flight_tiles > 0

        last_db_write = self.get_last_commit_timestamp() or "N/A"

        # Assemble category-by-category metrics
        default_cats = [
            "healthcare", "hospitality", "food", "retail", "fitness",
            "beauty", "automotive", "finance", "education", "real_estate"
        ]
        target_cats = list(configured_categories or default_cats)
        for r in cat_tile_rows:
            c = r["category"]
            if c and c not in target_cats:
                target_cats.append(c)

        tiles_by_cat_status: Dict[str, Dict[str, Any]] = {}
        for r in cat_tile_rows:
            c = r["category"]
            st = r["status"]
            tiles_by_cat_status.setdefault(c, {})[st] = dict(r)

        tasks_by_cat: Dict[str, Dict[str, Any]] = {}
        for r in cat_task_rows:
            tasks_by_cat[r["category"]] = dict(r)

        cat_by_name: Dict[str, Dict[str, Any]] = {}
        for cat in target_cats:
            st_map = tiles_by_cat_status.get(cat, {})
            c_compl = st_map.get("completed", {}).get("cnt", 0)
            c_limit = st_map.get("limit_reached", {}).get("cnt", 0)
            c_pend = st_map.get("pending", {}).get("cnt", 0)
            c_run = st_map.get("running", {}).get("cnt", 0)
            c_retry = st_map.get("retry_pending", {}).get("cnt", 0)
            c_fail = st_map.get("failed", {}).get("cnt", 0)
            c_sub = st_map.get("subdivided", {}).get("cnt", 0)
            c_children = sum(st_map.get(s, {}).get("child_cnt", 0) for s in ("pending", "running", "retry_pending"))
            c_raw = sum(v.get("new_recs", 0) or 0 for v in st_map.values())
            c_dups = sum(v.get("dup_cids", 0) or 0 for v in st_map.values())

            c_fin = c_compl + c_limit
            c_active = c_fin + c_pend + c_run + c_retry + c_fail
            c_rem = c_pend + c_run + c_retry + c_fail
            c_growing = (c_pend + c_run + c_retry) > 0

            t_info = tasks_by_cat.get(cat, {})
            t_status = t_info.get("status")
            t_saved = t_info.get("records_collected", 0) or 0
            rp_count = raw_places_by_cat.get(cat, 0)
            effective_raw = max(c_raw, t_saved, rp_count)

            if t_status == "completed" or (c_active > 0 and c_rem == 0 and c_fail == 0):
                disp_status = "COMPLETED"
            elif c_fail > 0 and c_rem == c_fail:
                disp_status = "FAILED"
            elif c_run > 0 or t_status == "in_progress":
                disp_status = "RUNNING"
            elif c_active > 0:
                disp_status = "RUNNING"
            else:
                disp_status = "PENDING"

            cat_by_name[cat] = {
                "category": cat,
                "status": disp_status,
                "completed": c_compl,
                "limit_reached": c_limit,
                "pending": c_pend,
                "running": c_run,
                "retry_pending": c_retry,
                "failed": c_fail,
                "subdivided": c_sub,
                "child_tiles_awaiting": c_children,
                "finished_tiles": c_fin,
                "active_total": c_active,
                "remaining_tiles": c_rem,
                "can_grow": c_growing,
                "raw_saved": effective_raw,
                "records_collected": effective_raw,
                "duplicate_cids": c_dups,
                "active_tile_id": "",
                "run_new": 0,
                "run_dups": 0,
                "results_returned": 0,
                "recent_names": [],
            }

        return {
            "today_date": datetime.now().strftime("%Y-%m-%d"),
            "db_path": str(self.db_path),
            "raw_saved_today": raw_today,
            "total_raw": raw_total,
            "total_canonical": canonical_total,
            "planned_root": planned_root,
            "subdivided_parents": subdivided,
            "child_tiles_awaiting": child_tiles_awaiting,
            "completed": completed,
            "limit_reached": limit_reached,
            "pending": pending,
            "running": running,
            "retry_pending": retry_pending,
            "failed": failed,
            "finished_tiles": finished_tiles,
            "active_total": active_total,
            "remaining_tiles": remaining_tiles,
            "in_flight_tiles": in_flight_tiles,
            "can_grow": can_grow,
            "total_categories": (task_row["total_categories"] or 0) if task_row else 0,
            "completed_categories": (task_row["completed_categories"] or 0) if task_row else 0,
            "in_progress_categories": (task_row["in_progress_categories"] or 0) if task_row else 0,
            "failed_categories": (task_row["failed_categories"] or 0) if task_row else 0,
            "last_db_write": last_db_write,
            "categories": cat_by_name,
        }






