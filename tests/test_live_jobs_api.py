import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.db import Database
from gmaps_scraper.jobs import JobManager, JobCancelledError
from gmaps_scraper.web.app import create_app
from gmaps_scraper.models import PlaceStub

@pytest.fixture
def clean_db(tmp_path):
    db_path = tmp_path / "test_jobs.sqlite"
    db = Database(db_path)
    db.init_schema()
    return db

@pytest.fixture
def app_and_client(clean_db, tmp_path):
    cfg = ScraperConfig(
        db_path=clean_db.db_path,
        raw_dir=tmp_path / "raw",
        photos_dir=tmp_path / "photos",
        exports_dir=tmp_path / "exports",
        logs_dir=tmp_path / "logs"
    )
    app = create_app(cfg)
    client = TestClient(app)
    return app, client, clean_db

def test_api_live_parse(app_and_client):
    _, client, _ = app_and_client
    resp = client.post("/api/live/parse", json={"text": "hospitals in Chicago"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "hospitals"
    assert data["location"] == "Chicago"

def test_api_live_resolve_with_cache(app_and_client):
    _, client, db = app_and_client
    # Seed geo cache
    db.set_geo_cache({
        "query": "chicago",
        "label": "Chicago, IL",
        "lat": 41.8832,
        "lng": -87.6324,
        "span_deg": 0.10,
        "country": "United States",
        "country_code": "US",
        "feature_class": "city",
        "resolved_from": "search_rpc"
    })

    resp = client.post("/api/live/resolve", json={"location": "chicago"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "Chicago, IL"
    assert data["span_deg"] == 0.10
    assert data["country_code"] == "US"
    assert "estimated_requests" in data

def test_api_live_start_and_get_job(app_and_client):
    _, client, db = app_and_client
    # Seed geo cache so start can resolve without external HTTP
    db.set_geo_cache({
        "query": "austin, tx",
        "label": "Austin, TX",
        "lat": 30.267,
        "lng": -97.743,
        "span_deg": 0.10,
        "country": "United States",
        "country_code": "US",
        "feature_class": "city"
    })

    resp = client.post("/api/live/start", json={
        "query": "coffee",
        "location": "austin, tx",
        "options": {"max_places": 10, "enrich_details": False, "fetch_reviews": False}
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    job_id = data["job_id"]

    # Verify get_job
    job_resp = client.get(f"/api/live/job/{job_id}")
    assert job_resp.status_code == 200
    job_data = job_resp.json()
    assert job_data["job"]["id"] == job_id
    assert job_data["job"]["query"] == "coffee"

    # Verify list jobs
    jobs_resp = client.get("/api/live/jobs")
    assert jobs_resp.status_code == 200
    jobs_list = jobs_resp.json()
    assert any(j["id"] == job_id for j in jobs_list)

def test_job_cancellation_and_retry(app_and_client):
    _, client, db = app_and_client
    mgr = JobManager.get_instance(db)

    # Create job
    job_id = mgr.create_job(query="bakery", location="Paris, France")
    assert db.get_job(job_id)["status"] == "queued"

    # Cancel via API
    cancel_resp = client.post(f"/api/live/cancel/{job_id}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"
    assert db.get_job(job_id)["status"] == "cancelled"

    # Retry via API
    retry_resp = client.post(f"/api/live/retry/{job_id}")
    assert retry_resp.status_code == 200
    assert retry_resp.json()["status"] == "requeued"
    assert db.get_job(job_id)["status"] in ("queued", "resolving")

def test_mark_interrupted_jobs_on_restart(clean_db):
    # Insert jobs with various statuses
    clean_db.create_job({"id": "j1", "status": "discovering", "query": "q1"})
    clean_db.create_job({"id": "j2", "status": "enriching", "query": "q2"})
    clean_db.create_job({"id": "j3", "status": "done", "query": "q3"})
    clean_db.create_job({"id": "j4", "status": "cancelled", "query": "q4"})

    count = clean_db.mark_interrupted_jobs()
    assert count == 2

    assert clean_db.get_job("j1")["status"] == "interrupted"
    assert clean_db.get_job("j2")["status"] == "interrupted"
    assert clean_db.get_job("j3")["status"] == "done"
    assert clean_db.get_job("j4")["status"] == "cancelled"

def test_integration_live_job_places_and_filtering(app_and_client):
    _, client, db = app_and_client
    job_id = "job_test_123"
    db.create_job({"id": job_id, "status": "done", "query": "hospital"})

    # Ingest place with first_seen_job_id
    stub = {
        "cid": "999888777",
        "name": "St Jude Medical Center",
        "address": "123 Health Ave, Chicago, IL 60601, United States",
        "category": "General Hospital",
        "rating": 4.8,
        "review_count": 150,
        "first_seen_job_id": job_id
    }
    db.upsert_place_stub(stub)

    # Place should have derived dimensions: vertical = healthcare, country = US
    place = db.get_place("999888777")
    assert place["vertical"] == "healthcare"
    assert place["country_code"] == "US"
    assert place["first_seen_job_id"] == job_id

    # Filter /places by job
    resp = client.get(f"/places?job={job_id}")
    assert resp.status_code == 200
    assert "St Jude Medical Center" in resp.text

    # Filter by vertical + job
    resp_v = client.get(f"/places?job={job_id}&vertical=healthcare")
    assert resp_v.status_code == 200
    assert "St Jude Medical Center" in resp_v.text

    # Filter by non-matching vertical
    resp_mismatch = client.get(f"/places?job={job_id}&vertical=food")
    assert resp_mismatch.status_code == 200
    assert "No Places Found" in resp_mismatch.text or "St Jude Medical Center" not in resp_mismatch.text

    # Export by job
    exp_resp = client.get(f"/api/export?format=csv&job={job_id}")
    assert exp_resp.status_code == 200
    assert "St Jude Medical Center" in exp_resp.text

def test_sse_stream_framing_and_events(app_and_client):
    _, client, db = app_and_client
    job_id = "job_sse_test"
    db.create_job({"id": job_id, "status": "done", "query": "cafe"})
    db.add_job_event(job_id, stage="resolving", message="Resolved area", level="info")
    db.add_job_event(job_id, stage="place", message="Found Cafe", payload={"cid": "123", "name": "Best Cafe"})
    db.add_job_event(job_id, stage="done", message="Completed", payload={"job_id": job_id, "status": "done"})

    # Connect to stream
    resp = client.get(f"/api/live/stream?job_id={job_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    content = resp.text

    # Assert proper SSE framing: event: ...\ndata: ...\n\n
    assert "event: " in content
    assert "data: " in content
    assert "Best Cafe" in content
    assert '"status": "done"' in content
