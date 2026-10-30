import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.db import Database
from gmaps_scraper.web.demo_data import seed_demo_data
from gmaps_scraper.web.app import create_app

@pytest.fixture
def web_client():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "data" / "gmaps.sqlite"
        photos_path = tmp_path / "data" / "photos"
        logs_path = tmp_path / "logs"

        cfg = ScraperConfig(
            data_dir=tmp_path / "data",
            db_path=db_path,
            photos_dir=photos_path,
            logs_dir=logs_path,
            output_dir=tmp_path / "output",
        )

        db = Database(cfg.absolute_db_path)
        db.init_schema()
        seed_demo_data(db, photos_dir=cfg.absolute_photos_dir, force=True)

        app = create_app(cfg)
        client = TestClient(app)
        yield client, cfg

def test_dashboard_view(web_client):
    client, _ = web_client
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "GMaps Scraper" in html
    assert "100% Offline" in html
    assert "Total Places" in html
    assert "ratingChart" in html
    assert "categoriesChart" in html

def test_places_view_and_xss_protection(web_client):
    client, _ = web_client
    resp = client.get("/places")
    assert resp.status_code == 200
    html = resp.text
    assert "Places Explorer" in html

    # Verify XSS payload is strictly HTML-escaped and cannot execute
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # Ensure there is NO raw unescaped script tag with alert(1) inside HTML body
    assert "<script>alert(1)</script>" not in html

def test_place_detail_view(web_client):
    client, _ = web_client
    resp = client.get("/places/11785376804253324318")
    assert resp.status_code == 200
    html = resp.text
    assert "787 Coffee" in html
    assert "Operating Hours" in html
    assert "Attributes & Features" in html

    # Non-existent place
    resp_404 = client.get("/places/9999999999999999")
    assert resp_404.status_code == 404

def test_map_view(web_client):
    client, _ = web_client
    resp = client.get("/map")
    assert resp.status_code == 200
    html = resp.text
    assert "Spatial Coordinate Map" in html
    assert "scatterMap" in html

def test_runs_view(web_client):
    client, _ = web_client
    resp = client.get("/runs")
    assert resp.status_code == 200
    html = resp.text
    assert "Pipeline Execution &amp; Live Monitoring" in html
    assert "Live Log Tail" in html

def test_quality_view(web_client):
    client, _ = web_client
    resp = client.get("/quality")
    assert resp.status_code == 200
    html = resp.text
    assert "Data Quality &amp; Integrity Audit" in html
    assert "Field Completeness &amp; Null Counts" in html

# -------------------------------------------------------------
# JSON API Tests
# -------------------------------------------------------------

def test_api_stats(web_client):
    client, _ = web_client
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_places" in data
    assert data["total_places"] >= 6
    assert "rating_distribution" in data
    assert "top_categories" in data

def test_api_places(web_client):
    client, _ = web_client
    resp = client.get("/api/places")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data
    assert len(data["items"]) >= 6

    # Test filtering
    resp_filtered = client.get("/api/places?min_rating=4.5")
    assert resp_filtered.status_code == 200
    items = resp_filtered.json()["items"]
    assert all(item["rating"] >= 4.5 for item in items if item.get("rating") is not None)

def test_api_place_detail_and_reviews(web_client):
    client, _ = web_client
    cid = "11785376804253324318"
    resp = client.get(f"/api/places/{cid}")
    assert resp.status_code == 200
    place = resp.json()
    assert place["name"] == "787 Coffee"
    assert "hours" in place

    resp_reviews = client.get(f"/api/places/{cid}/reviews")
    assert resp_reviews.status_code == 200
    rev_data = resp_reviews.json()
    assert "items" in rev_data
    assert len(rev_data["items"]) > 0

    resp_photos = client.get(f"/api/places/{cid}/photos")
    assert resp_photos.status_code == 200
    assert isinstance(resp_photos.json(), list)

def test_api_map(web_client):
    client, _ = web_client
    resp = client.get("/api/map")
    assert resp.status_code == 200
    points = resp.json()
    assert isinstance(points, list)
    assert len(points) > 0
    p0 = points[0]
    assert "lat" in p0
    assert "lng" in p0
    assert "cid" in p0

def test_api_progress_and_logs(web_client):
    client, _ = web_client
    resp = client.get("/api/progress")
    assert resp.status_code == 200
    assert "current_step" in resp.json()

    resp_logs = client.get("/api/logs?tail=50")
    assert resp_logs.status_code == 200
    assert "logs" in resp_logs.json()

def test_api_run_actions(web_client):
    client, _ = web_client
    # Valid action
    resp = client.post("/api/run/search")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"

    # Invalid action
    resp_invalid = client.post("/api/run/invalid_action_xyz")
    assert resp_invalid.status_code == 400

    # Resume
    resp_resume = client.post("/api/resume")
    assert resp_resume.status_code == 200
    assert resp_resume.json()["status"] == "resumed"

    # Retry failed
    resp_retry = client.post("/api/retry-failed")
    assert resp_retry.status_code == 200
    assert resp_retry.json()["status"] == "retried"
    assert resp_retry.json()["reset_count"] >= 1

def test_api_export(web_client):
    client, cfg = web_client
    resp_csv = client.get("/api/export?format=csv")
    assert resp_csv.status_code == 200
    assert "text/csv" in resp_csv.headers.get("content-type", "")
    assert "cid" in resp_csv.text and "name" in resp_csv.text

    resp_jsonl = client.get("/api/export?format=jsonl")
    assert resp_jsonl.status_code == 200
    assert "ndjson" in resp_jsonl.headers.get("content-type", "")
    lines = resp_jsonl.text.strip().split("\n")
    assert len(lines) >= 6

    # Test that API export with filters produces byte-identical output to direct export function
    from gmaps_scraper.db import Database
    from gmaps_scraper.export import export_stream_bytes
    db = Database(cfg.absolute_db_path)
    filters = {"min_rating": 4.5}
    expected_bytes, _ = export_stream_bytes(db, export_format="csv", filters=filters)
    resp_filtered = client.get("/api/export?format=csv&min_rating=4.5")
    assert resp_filtered.content == expected_bytes

def test_local_photo_media_serving(web_client):
    client, cfg = web_client
    cid = "11785376804253324318"

    # Non-existent photo gives 404
    resp_404 = client.get(f"/media/photos/{cid}/missing.jpg")
    assert resp_404.status_code == 404

    # Create dummy photo on local disk
    photo_folder = cfg.absolute_photos_dir / cid
    photo_folder.mkdir(parents=True, exist_ok=True)
    dummy_file = photo_folder / "test_thumb.jpg"
    dummy_file.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20)

    resp_200 = client.get(f"/media/photos/{cid}/test_thumb.jpg")
    assert resp_200.status_code == 200
    assert resp_200.headers.get("content-type") == "image/jpeg"

def test_offline_zero_external_cdns(web_client):
    """Verifies that all views contain zero external CDN/font/script URLs."""
    client, _ = web_client
    endpoints = ["/", "/places", "/map", "/runs", "/quality"]
    external_domains = [
        "cdnjs.cloudflare.com", "unpkg.com", "cdn.jsdelivr.net",
        "fonts.googleapis.com", "fonts.gstatic.com", "stackpath.bootstrapcdn.com",
        "maxcdn.bootstrapcdn.com", "code.jquery.com"
    ]

    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 200
        text = resp.text
        for domain in external_domains:
            assert domain not in text, f"Found external CDN domain '{domain}' in endpoint {ep}"

def test_pagination_edge_cases_and_shapes(web_client):
    """H2 requirement: test page 0/1/last/out-of-range/size 1/size max."""
    client, _ = web_client

    # page 0 (degrades safely to first page)
    r_p0 = client.get("/api/places?page=0&page_size=2")
    assert r_p0.status_code == 200
    assert len(r_p0.json()["items"]) == 2

    # page 1
    r_p1 = client.get("/api/places?page=1&page_size=2")
    assert r_p1.status_code == 200
    assert len(r_p1.json()["items"]) == 2

    # size 1
    r_s1 = client.get("/api/places?page=1&page_size=1")
    assert r_s1.status_code == 200
    assert len(r_s1.json()["items"]) == 1

    # size max (e.g. 1000)
    r_smax = client.get("/api/places?page=1&page_size=1000")
    assert r_smax.status_code == 200
    assert len(r_smax.json()["items"]) >= 6

    # out of range page
    r_out = client.get("/api/places?page=99999&page_size=10")
    assert r_out.status_code == 200
    assert len(r_out.json()["items"]) == 0
    assert r_out.json()["total"] >= 6

def test_pipeline_control_view(web_client):
    client, _ = web_client
    resp = client.get("/pipeline")
    assert resp.status_code == 200
    html = resp.text
    assert "Pipeline Control" in html
    assert "STAGE 1" in html
    assert "STAGE 2" in html
    assert "STAGE 3" in html
    assert "Planned Pipeline Roadmap" in html
    assert "Step 4" in html

def test_website_enrichment_view(web_client):
    client, _ = web_client
    resp = client.get("/enrichment")
    assert resp.status_code == 200
    html = resp.text
    assert "Website Discovery &amp; Company Enrichment" in html
    assert "Verified Websites" in html
    assert "Candidates" in html

def test_scope_regions_api(web_client):
    client, _ = web_client
    # All
    r_all = client.get("/api/scope/regions")
    assert r_all.status_code == 200
    data_all = r_all.json()
    assert "US" in data_all["countries"]
    assert "MX" in data_all["countries"]
    assert len(data_all["us_states"]) == 50
    assert len(data_all["mexico_entities"]) == 32

    # US filter
    r_us = client.get("/api/scope/regions?country_code=US")
    assert r_us.status_code == 200
    assert len(r_us.json()["regions"]) == 50

    # MX filter
    r_mx = client.get("/api/scope/regions?country_code=MX")
    assert r_mx.status_code == 200
    assert len(r_mx.json()["regions"]) == 32

    # Invalid country
    r_err = client.get("/api/scope/regions?country_code=CA")
    assert r_err.status_code == 400

def test_enrichment_stats_and_items_api(web_client):
    client, _ = web_client
    r_stats = client.get("/api/enrichment/stats")
    assert r_stats.status_code == 200
    stats = r_stats.json()
    assert "discovery" in stats
    assert "enrichment" in stats

    r_items = client.get("/api/enrichment/items?tab=all&page=1&page_size=10")
    assert r_items.status_code == 200
    items_data = r_items.json()
    assert "items" in items_data
    assert "total" in items_data

def test_pipeline_run_discovery_api_dry_run(web_client):
    client, _ = web_client
    resp = client.post("/api/pipeline/run-discovery", json={
        "country_code": "US",
        "limit": 5,
        "dry_run": True
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("dry_run") is True
    assert "counts" in data

def test_pipeline_run_enrichment_api_dry_run(web_client):
    client, _ = web_client
    resp = client.post("/api/pipeline/run-enrichment", json={
        "country_code": "US",
        "limit": 5,
        "max_pages": 3,
        "dry_run": True
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("dry_run") is True
    assert "counts" in data

def test_empty_database_dashboard():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        cfg = ScraperConfig(
            data_dir=tmp_path / "data",
            db_path=tmp_path / "data" / "empty.sqlite",
            photos_dir=tmp_path / "data" / "photos",
            logs_dir=tmp_path / "logs",
            output_dir=tmp_path / "output",
        )
        db = Database(cfg.absolute_db_path)
        db.init_schema()
        app = create_app(cfg)
        client = TestClient(app)

        resp = client.get("/")
        assert resp.status_code == 200
        assert "No Business Records in SQLite Database" in resp.text

