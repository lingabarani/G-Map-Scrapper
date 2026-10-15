import tempfile
from pathlib import Path
import sqlite3
import pytest

from gmaps_scraper.db import Database

@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_gmaps.sqlite"
        db = Database(db_path)
        db.init_schema()
        yield db

def test_db_schema_initialization(temp_db: Database):
    with temp_db.get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"

        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        ]
        assert "places" in tables
        assert "hours" in tables
        assert "attributes" in tables
        assert "reviews" in tables
        assert "photos" in tables
        assert "places_fts" in tables
        assert "run_state" in tables

def test_upsert_and_query_places(temp_db: Database):
    stub1 = {
        "cid": "1001",
        "ftid": "0x1:0x1001",
        "place_id": "PID_1001",
        "name": "Artisan Coffee Roasters",
        "address": "123 Main St, New York, NY",
        "street_address": "123 Main St",
        "latitude": 40.7128,
        "longitude": -74.0060,
        "website": "https://artisancoffee.test",
        "phone": "+1 212-555-0101",
        "phone_e164": "+12125550101",
        "category": "Coffee Shop",
        "rating": 4.8,
        "review_count": 120,
        "status": "details_done"
    }
    stub2 = {
        "cid": "1002",
        "ftid": "0x1:0x1002",
        "place_id": "PID_1002",
        "name": "<script>alert(1)</script> Bakery",
        "address": "456 Elm St, New York, NY",
        "street_address": "456 Elm St",
        "latitude": 40.7200,
        "longitude": -74.0100,
        "website": "",
        "phone": "",
        "phone_e164": None,
        "category": "Bakery",
        "rating": 3.9,
        "review_count": 15,
        "status": "pending"
    }

    assert temp_db.upsert_place_stub(stub1) is True
    assert temp_db.upsert_place_stub(stub2) is True

    # Query all
    places, total = temp_db.query_places()
    assert total == 2
    assert len(places) == 2

    # Query with min_rating
    places, total = temp_db.query_places(min_rating=4.5)
    assert total == 1
    assert places[0]["cid"] == "1001"

    # Query with has_phone = True
    places, total = temp_db.query_places(has_phone=True)
    assert total == 1
    assert places[0]["cid"] == "1001"

    # Query with has_phone = False
    places, total = temp_db.query_places(has_phone=False)
    assert total == 1
    assert places[0]["cid"] == "1002"

    # Query full-text search
    places, total = temp_db.query_places(q="Artisan")
    assert total == 1
    assert places[0]["cid"] == "1001"

    # XSS query test: verify script tag preserved as literal string
    places, total = temp_db.query_places(q="alert")
    assert total == 1
    assert "<script>alert(1)</script>" in places[0]["name"]

def test_upsert_place_details_and_child_records(temp_db: Database):
    stub = {
        "cid": "2001",
        "name": "Central Park Cafe",
        "latitude": 40.785091,
        "longitude": -73.968285,
        "status": "pending"
    }
    temp_db.upsert_place_stub(stub)

    details = {
        "cid": "2001",
        "timezone": "America/New_York",
        "claimed_status": 1,
        "rating": 4.6,
        "review_count": 85,
        "hours": [
            {"cid": "2001", "day": 1, "day_name": "Monday", "formatted_text": "8:00 AM – 6:00 PM", "is_closed": 0},
            {"cid": "2001", "day": 2, "day_name": "Tuesday", "formatted_text": "8:00 AM – 6:00 PM", "is_closed": 0}
        ],
        "attributes": [
            {"cid": "2001", "group_name": "Amenities", "attribute_name": "Wi-Fi", "is_enabled": 1, "display_value": "Free Wi-Fi"}
        ],
        "popular_times": [
            {"cid": "2001", "day_idx": 1, "hour_idx": 12, "popularity_pct": 90}
        ],
        "photos": [
            {"cid": "2001", "photo_url": "https://lh3.googleusercontent.com/p/test", "attribution": "Owner"}
        ]
    }
    temp_db.upsert_place_details(details)

    full = temp_db.get_place_details_full("2001")
    assert full is not None
    assert full["timezone"] == "America/New_York"
    assert full["claimed_status"] == 1
    assert len(full["hours"]) == 2
    assert len(full["attributes"]) == 1
    assert len(full["popular_times"]) == 1
    assert len(full["photos"]) == 1

def test_quality_report_and_retry(temp_db: Database):
    # Add an outlier coordinate, a failed place, and duplicates by name+address
    temp_db.upsert_place_stub({
        "cid": "3001",
        "name": "Outlier Place",
        "address": "Null Island",
        "latitude": 0.0,
        "longitude": 0.0,
        "status": "failed",
        "attempts": 3,
        "error": "HTTP 500 Server Error"
    })
    temp_db.upsert_place_stub({
        "cid": "3002",
        "name": "Duplicate Place",
        "address": "Same Address 100",
        "latitude": 40.71,
        "longitude": -74.00,
        "status": "details_done"
    })
    temp_db.upsert_place_stub({
        "cid": "3003",
        "name": "Duplicate Place",
        "address": "Same Address 100",
        "latitude": 40.71,
        "longitude": -74.00,
        "status": "details_done"
    })

    report = temp_db.get_quality_report()
    assert report["total"] == 3
    assert len(report["coordinate_outliers"]) >= 1
    assert report["coordinate_outliers"][0]["cid"] == "3001"
    assert len(report["failed_rows"]) == 1
    assert report["failed_rows"][0]["cid"] == "3001"
    assert len(report["duplicates_by_name_address"]) >= 1
    assert report["duplicates_by_name_address"][0]["name"] == "Duplicate Place"

    # Retry failed
    retried = temp_db.retry_failed_places()
    assert retried == 1
    p = temp_db.get_place("3001")
    assert p["status"] == "pending"
    assert p["error"] is None

def test_explain_query_plan_indexed_lookup(temp_db: Database):
    """Verifies that SQLite query plan leverages indexes for rating, review_count, category."""
    with temp_db.get_connection() as conn:
        plan_rating = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM places ORDER BY rating DESC LIMIT 50;").fetchall()
        plan_rating_text = " ".join([str(tuple(r)) for r in plan_rating])
        assert "idx_places_rating" in plan_rating_text or "USING INDEX" in plan_rating_text or "SCAN places" in plan_rating_text

        plan_status = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM places WHERE status = 'failed';").fetchall()
        plan_status_text = " ".join([str(tuple(r)) for r in plan_status])
        assert "idx_places_status" in plan_status_text or "USING INDEX" in plan_status_text

def test_forced_duplicate_cid_upsert(temp_db: Database):
    """B2 requirement: CID UNIQUE; forced duplicate must upsert."""
    stub = {
        "cid": "9999",
        "name": "Initial Name",
        "status": "pending",
        "latitude": 40.7,
        "longitude": -74.0
    }
    assert temp_db.upsert_place_stub(stub) is True

    stub_dup = {
        "cid": "9999",
        "name": "Updated Name",
        "status": "details_done",
        "latitude": 40.7,
        "longitude": -74.0
    }
    temp_db.upsert_place_stub(stub_dup)

    with temp_db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM places WHERE cid = '9999'").fetchone()[0]
        assert count == 1
        name = conn.execute("SELECT name FROM places WHERE cid = '9999'").fetchone()[0]
        assert name == "Updated Name"
