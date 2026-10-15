import io
import json
import sqlite3
import tempfile
from pathlib import Path
import pytest
import pandas as pd
from fastapi.testclient import TestClient

from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.db import Database
from gmaps_scraper.taxonomy import classify_vertical, parse_address, enrich_place_dimensions, load_verticals, load_countries
from gmaps_scraper.export import export_data, export_stream_bytes, SCHEMA_COLUMNS, COMPACT_COLUMNS
from gmaps_scraper.web.app import create_app

# ==============================================================================
# 1. UNIT TESTS: TAXONOMY MAPPING
# ==============================================================================

def test_taxonomy_every_vertical():
    """Positive cases for every taxonomy key."""
    cases = [
        ("General Hospital", "healthcare"),
        ("Dental Clinic", "healthcare"),
        ("Pharmacy", "healthcare"),
        ("Boutique Hotel", "hospitality"),
        ("Youth Hostel", "hospitality"),
        ("Italian Restaurant", "food"),
        ("Espresso Bar", "food"),
        ("Bakery & Cafe", "food"),
        ("Convenience Store", "retail"),
        ("Supermarket", "retail"),
        ("Crossfit Gym", "fitness"),
        ("Yoga Studio", "fitness"),
        ("Hair Salon", "beauty"),
        ("Day Spa", "beauty"),
        ("Car Repair Garage", "automotive"),
        ("Gas Station", "automotive"),
        ("National Bank", "finance"),
        ("ATM", "finance"),
        ("High School", "education"),
        ("City Library", "education"),
        ("Police Station", "government"),
        ("City Hall", "government"),
        ("Real Estate Agency", "real_estate"),
    ]
    for raw_cat, expected_vertical in cases:
        vert, ret_raw = classify_vertical(raw_cat)
        assert vert == expected_vertical, f"Expected {expected_vertical} for {raw_cat}, got {vert}"
        assert ret_raw == raw_cat

def test_taxonomy_negative_and_fallback():
    """Negative cases and fallback to 'other'."""
    assert classify_vertical("Space Station Launchpad")[0] == "other"
    assert classify_vertical("Quantum Physics Research")[0] == "other"
    assert classify_vertical("")[0] == "other"
    assert classify_vertical(None)[0] == "other"

def test_taxonomy_first_match_wins():
    """Verify first match in ordered taxonomy wins."""
    # 'pharmacy' is in healthcare; 'pharmacy retail' is in retail. Healthcare is listed first.
    vert, _ = classify_vertical("Pharmacy retail")
    assert vert == "healthcare"

# ==============================================================================
# 2. UNIT TESTS: ADDRESS EXTRACTION
# ==============================================================================

def test_parse_address_real_strings():
    """Extraction from real-world addresses across different countries."""
    # US standard
    us_full = parse_address("123 Main St, New York, NY 10001, United States")
    assert us_full["country_code"] == "US"
    assert us_full["country"] == "United States"
    assert us_full["city"] == "New York"
    assert us_full["region"] == "NY"
    assert us_full["postal_code"] == "10001"

    # US short with state + zip as last component
    us_short = parse_address("20 Stone St, New York, NY 10004")
    assert us_short["country_code"] == "US"
    assert us_short["city"] == "New York"
    assert us_short["region"] == "NY"
    assert us_short["postal_code"] == "10004"

    # UK address
    uk_addr = parse_address("Westminster Bridge Rd, London SE1 7EH, United Kingdom")
    assert uk_addr["country_code"] == "GB"
    assert uk_addr["city"] == "London"
    assert uk_addr["postal_code"] == "SE1 7EH"

    # France address
    fr_addr = parse_address("10 Rue de la Paix, 75002 Paris, France")
    assert fr_addr["country_code"] == "FR"
    assert fr_addr["city"] == "Paris"
    assert fr_addr["postal_code"] == "75002"

    # Australia address
    au_addr = parse_address("Shop 4, 100 George St, Sydney NSW 2000, Australia")
    assert au_addr["country_code"] == "AU"
    assert au_addr["city"] == "Sydney"
    assert au_addr["region"] == "NSW"
    assert au_addr["postal_code"] == "2000"

def test_parse_address_malformed_and_empty():
    """Robustness against empty, null, or malformed addresses."""
    empty = parse_address("")
    assert empty["country"] == "unknown"
    assert empty["country_code"] == "unknown"

    none_res = parse_address(None)
    assert none_res["country"] == "unknown"
    assert none_res["country_code"] == "unknown"

    malformed = parse_address("Totally Bogus Address Without Country Or Commas")
    assert malformed["country"] == "unknown"
    assert malformed["country_code"] == "unknown"

# ==============================================================================
# 3. FIXTURE: SEEDED MIXED MULTI-COUNTRY DATASET
# ==============================================================================

@pytest.fixture
def seeded_db(tmp_path):
    """Creates a temporary SQLite DB seeded with mixed verticals across at least 3 countries."""
    db_file = tmp_path / "test_multi.sqlite"
    db = Database(db_file)
    db.init_schema()

    places = [
        # Place 1: Healthcare in US (New York)
        {
            "cid": "1001",
            "name": "Manhattan General Hospital",
            "address": "500 E 77th St, New York, NY 10075, United States",
            "category": "Hospital",
            "rating": 4.6,
            "review_count": 320,
            "phone": "+1 212-555-0101",
            "website": "https://manhattanhospital.org",
            "status": "complete"
        },
        # Place 2: Food in US (New York)
        {
            "cid": "1002",
            "name": "Hudson Roastery Cafe",
            "address": "40 Wall St, New York, NY 10005, United States",
            "category": "Cafe",
            "rating": 4.8,
            "review_count": 150,
            "phone": "+1 212-555-0102",
            "website": "https://hudsonroastery.com",
            "status": "complete"
        },
        # Place 3: Healthcare in UK (London)
        {
            "cid": "1003",
            "name": "St Thomas Medical Clinic",
            "address": "Westminster Bridge Rd, London SE1 7EH, United Kingdom",
            "category": "Medical Clinic",
            "rating": 4.2,
            "review_count": 85,
            "phone": "+44 20 7188 7188",
            "website": "https://stthomasclinic.uk",
            "status": "complete"
        },
        # Place 4: Food in France (Paris)
        {
            "cid": "1004",
            "name": "Bistro de Paris",
            "address": "15 Rue de Rivoli, 75001 Paris, France",
            "category": "French Restaurant",
            "rating": 4.5,
            "review_count": 210,
            "phone": "+33 1 42 68 00 00",
            "website": "https://bistrorivoli.fr",
            "status": "complete"
        },
        # Place 5: Hospitality in UK (London)
        {
            "cid": "1005",
            "name": "The Londoner Hotel",
            "address": "38 Leicester Square, London WC2H 7DX, United Kingdom",
            "category": "Hotel",
            "rating": 4.7,
            "review_count": 450,
            "phone": "+44 20 7451 0101",
            "website": "https://thelondoner.com",
            "status": "complete"
        },
        # Place 6: Retail in US (Los Angeles)
        {
            "cid": "1006",
            "name": "Beverly Hills Electronics Store",
            "address": "9500 Wilshire Blvd, Beverly Hills, CA 90212, United States",
            "category": "Electronics Store",
            "rating": 3.9,
            "review_count": 40,
            "phone": "+1 310-555-0199",
            "website": "https://bhelectronics.com",
            "status": "details_done"
        }
    ]

    for p in places:
        db.upsert_place_stub(p)

    # Insert sample hours & attributes for place 1001
    db.update_place_details("1001", {
        "hours": [{"day": 1, "day_name": "Monday", "formatted_text": "Open 24 hours", "is_closed": False}],
        "attributes": [{"group_id": "acc", "group_name": "Accessibility", "attribute_id": "wh", "attribute_name": "Wheelchair Accessible", "is_enabled": True}],
        "popular_times": [{"day_idx": 1, "hour_idx": 12, "popularity_pct": 80}]
    }, status="complete")

    # Insert review for place 1001
    db.insert_review({
        "review_id": "rev_1001_1",
        "cid": "1001",
        "author_name": "Jane Doe",
        "rating": 5,
        "text": "Exceptional healthcare and fast emergency intake."
    })

    return db

@pytest.fixture
def test_client(seeded_db, tmp_path):
    cfg = ScraperConfig(
        db_path=seeded_db.db_path,
        raw_dir=tmp_path / "raw",
        photos_dir=tmp_path / "photos",
        exports_dir=tmp_path / "exports",
        logs_dir=tmp_path / "logs"
    )
    app = create_app(cfg)
    return TestClient(app)

# ==============================================================================
# 4. API & FILTER TESTS
# ==============================================================================

def test_api_filters_individual_and_combined(test_client):
    """Tests each new filter individually and in combination."""
    # Filter vertical=healthcare -> 1001 (US) and 1003 (UK)
    r = test_client.get("/api/places?vertical=healthcare")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert {it["cid"] for it in items} == {"1001", "1003"}

    # Filter country=US -> 1001, 1002, 1006
    r = test_client.get("/api/places?country=US")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 3
    assert {it["cid"] for it in items} == {"1001", "1002", "1006"}

    # Filter city=London -> 1003, 1005
    r = test_client.get("/api/places?city=London")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    assert {it["cid"] for it in items} == {"1003", "1005"}

    # Combined: vertical=healthcare + country=US -> 1001 only
    r = test_client.get("/api/places?vertical=healthcare&country=US")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["cid"] == "1001"

    # Combined: vertical=food + country=FR -> 1004 only
    r = test_client.get("/api/places?vertical=food&country=FR")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["cid"] == "1004"

    # Boolean filters: has_reviews=true -> 1001
    r = test_client.get("/api/places?has_reviews=true")
    assert r.status_code == 200
    cids = {it["cid"] for it in r.json()["items"]}
    assert "1001" in cids

    # has_hours=true -> 1001
    r = test_client.get("/api/places?has_hours=true")
    assert r.status_code == 200
    assert any(it["cid"] == "1001" for it in r.json()["items"])

def test_api_faceted_counts_shrink(test_client):
    """Facet counts shrink live as active filters change."""
    # When filtering vertical=healthcare:
    # Countries with healthcare are US (1001) and GB (1003). France has no healthcare.
    r = test_client.get("/api/countries?vertical=healthcare")
    assert r.status_code == 200
    countries = r.json()
    codes = {c["code"]: c["count"] for c in countries}
    assert "US" in codes and codes["US"] == 1
    assert "GB" in codes and codes["GB"] == 1
    assert "FR" not in codes or codes["FR"] == 0

    # When filtering country=FR:
    # France only contains Bistro de Paris (food).
    r = test_client.get("/api/verticals?country=FR")
    assert r.status_code == 200
    verts = {v["key"]: v["count"] for v in r.json()}
    assert verts.get("food", 0) == 1
    assert verts.get("healthcare", 0) == 0

    # When querying cities with country=GB:
    # Only London exists in GB.
    r = test_client.get("/api/cities?country=GB")
    assert r.status_code == 200
    cities = r.json()
    assert len(cities) >= 1
    assert any(c["city"] == "London" for c in cities)

def test_api_full_place_record_and_no_null_leak(test_client):
    """GET /api/places/{cid}/full returns every schema key and HTML has no 'None'/'null'."""
    r = test_client.get("/api/places/1001/full")
    assert r.status_code == 200
    data = r.json()

    # Check all schema keys present
    for col in SCHEMA_COLUMNS:
        assert col in data, f"Missing expected column {col} in full place record"

    assert "hours" in data
    assert "attributes" in data
    assert "reviews" in data
    assert "popular_times" in data
    assert len(data["hours"]) > 0
    assert len(data["attributes"]) > 0
    assert len(data["reviews"]) > 0

    # Test HTML view /places/1001 does not leak Python 'None' or JS 'null' into user text
    r_html = test_client.get("/places/1001")
    assert r_html.status_code == 200
    html = r_html.text
    assert ">None<" not in html
    assert ">null<" not in html
    assert "Identity" in html
    assert "Location" in html
    assert "Contact" in html
    assert "Provenance" in html

def test_api_full_export_columns_match_schema(test_client):
    """GET /api/export with full=true returns exactly schema columns."""
    r = test_client.get("/api/export?format=csv&full=true")
    assert r.status_code == 200
    csv_bytes = r.content
    df = pd.read_csv(io.BytesIO(csv_bytes), dtype=str)

    # Column count == schema column count
    assert len(df.columns) == len(SCHEMA_COLUMNS)
    assert list(df.columns) == SCHEMA_COLUMNS

def test_reclassify_idempotency(seeded_db):
    """Reclassifying twice yields identical results with zero changes on second run."""
    r1 = seeded_db.reclassify_all()
    assert r1["total"] == 6

    r2 = seeded_db.reclassify_all()
    assert r2["total"] == 6
    assert r2["changed"] == 0
