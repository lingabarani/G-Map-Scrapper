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
"""

class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_website_status ON places(website_status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_official_url ON places(official_website_url);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_enrichment_status ON places(enrichment_status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_city ON places(city);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_job ON places(first_seen_job_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_website_status ON places(website_status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_enrichment_status ON places(enrichment_status);")

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
            return {"total": len(rows), "changed": changed}

    def upsert_place_stub(self, place: Dict[str, Any]) -> bool:
        """Insert or ignore place stub by cid. Returns True if inserted."""
        from .taxonomy import enrich_place_dimensions
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
            return cur.rowcount > 0

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

            return {
                "total_places": total,
                "enriched_places": enriched,
                "pending_places": pending,
                "failed_places": failed,
                "total_reviews": reviews_count,
                "total_photos": photos_count,
                "last_run": dict(last_run) if last_run else None,
                "rating_distribution": rating_dist,
                "top_categories": top_categories,
                "status_breakdown": status_breakdown,
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
        city: Optional[Any] = None,
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
            "city": city,
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
                SELECT places.*, (SELECT COUNT(*) FROM photos WHERE photos.cid = places.cid) as photos_count
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
        """Calculates null counts, duplicates, coordinate outliers, and failed rows."""
        with self.get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
            if total == 0:
                return {
                    "total": 0, "null_counts": {}, "duplicates_by_cid": [],
                    "duplicates_by_name_address": [], "coordinate_outliers": [], "failed_rows": []
                }

            # Null counts per column
            null_cols = ["address", "phone", "website", "category", "rating", "latitude", "longitude"]
            null_counts = {}
            for col in null_cols:
                cnt = conn.execute(f"SELECT COUNT(*) FROM places WHERE {col} IS NULL OR {col} = ''").fetchone()[0]
                null_counts[col] = cnt

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

            # Coordinate outliers (lat not between -90 and 90 or lng not between -180 and 180 or exactly 0,0)
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
                WHERE status = 'failed'
                ORDER BY updated_at DESC
                LIMIT 50
            """).fetchall()

            return {
                "total": total,
                "null_counts": null_counts,
                "duplicates_by_cid": [],
                "duplicates_by_name_address": duplicates_by_name_address,
                "coordinate_outliers": coordinate_outliers,
                "failed_rows": [dict(r) for r in failed_rows]
            }

    def retry_failed_places(self) -> int:
        with self.get_connection() as conn:
            cur = conn.execute("UPDATE places SET status = 'pending', error = NULL WHERE status = 'failed'")
            conn.commit()
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
            return cur.rowcount

    # -------------------------------------------------------------------------
    # Regional Collection Tasks (Pipeline 2 Scope & Task Tracking)
    # -------------------------------------------------------------------------

    def create_collection_task(self, task: Dict[str, Any]) -> bool:
        """Idempotently insert or update a geographic collection task."""
        sql = """
        INSERT INTO collection_tasks (
            task_id, country_code, region_code, region_name, category, tile_id,
            status, attempt_count, last_error, updated_at
        ) VALUES (
            :task_id, :country_code, :region_code, :region_name, :category, :tile_id,
            :status, :attempt_count, :last_error, CURRENT_TIMESTAMP
        )
        ON CONFLICT(task_id) DO UPDATE SET
            country_code = excluded.country_code,
            region_code = excluded.region_code,
            region_name = excluded.region_name,
            category = excluded.category,
            tile_id = excluded.tile_id,
            updated_at = CURRENT_TIMESTAMP;
        """
        params = {
            "task_id": task["task_id"],
            "country_code": task["country_code"].upper(),
            "region_code": task["region_code"].upper(),
            "region_name": task["region_name"],
            "category": task["category"],
            "tile_id": str(task["tile_id"]),
            "status": task.get("status", "pending"),
            "attempt_count": int(task.get("attempt_count", 0)),
            "last_error": task.get("last_error"),
        }
        with self.get_connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
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
        error: Optional[str] = None
    ) -> bool:
        """Updates task status, incrementing attempt_count on failure or in_progress."""
        with self.get_connection() as conn:
            if status == "failed":
                cur = conn.execute("""
                    UPDATE collection_tasks
                    SET status = 'failed',
                        attempt_count = attempt_count + 1,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                """, (error, task_id))
            elif status == "completed":
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



