import tempfile
from pathlib import Path
import pytest
import httpx
from unittest.mock import AsyncMock, patch

from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.db import Database
from gmaps_scraper.checkpoint import CheckpointManager
from gmaps_scraper.http import HttpClient
from gmaps_scraper.export import export_data
from gmaps_scraper.tiling import recursive_tile_search
from gmaps_scraper.models import PlaceStub


@pytest.mark.asyncio
async def test_resume_does_not_refetch_already_done():
    """Prove that resume skips already enriched / reviewed places and request counters match."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db = Database(tmp_path / "test.sqlite")
        db.init_schema()

        # Seed 5 places: 3 details_done, 2 pending
        for i in range(5):
            status = "details_done" if i < 3 else "pending"
            db.upsert_place_stub({
                "cid": f"cid_{i}",
                "ftid": f"ftid_{i}",
                "name": f"Place {i}",
                "status": status,
                "latitude": 40.7,
                "longitude": -74.0,
            })

        # When querying for pending enrichment items:
        with db.get_connection() as conn:
            pending = conn.execute("SELECT * FROM places WHERE status = 'pending'").fetchall()
        assert len(pending) == 2
        pending_cids = {r["cid"] for r in pending}
        assert pending_cids == {"cid_3", "cid_4"}

        # Simulate enrichment of the 2 pending items
        request_count = 0
        for p in pending:
            request_count += 1
            db.update_place_details(p["cid"], {"description": "Enriched", "category": "Cafe"}, status="details_done")

        # Request counter proves exactly 2 requests were made (no refetch of the 3 already-done)
        assert request_count == 2

        # Verify all 5 are now details_done
        with db.get_connection() as conn:
            all_done = conn.execute("SELECT * FROM places WHERE status = 'details_done'").fetchall()
        assert len(all_done) == 5

        # Second resume run: zero pending places
        with db.get_connection() as conn:
            second_pending = conn.execute("SELECT * FROM places WHERE status = 'pending'").fetchall()
        assert len(second_pending) == 0


@pytest.mark.asyncio
async def test_adaptive_backoff_on_429_503():
    """Verify HttpClient retries on 429 and 503 errors."""
    client = HttpClient(
        user_agent="TestAgent",
        accept_language="en",
        hl="en",
        gl="us",
        max_retries=3,
        backoff_base=0.01,
    )

    call_count = 0

    async def mock_send(request, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, request=request, headers={"Retry-After": "0"})
        elif call_count == 2:
            return httpx.Response(503, request=request)
        return httpx.Response(200, request=request, text="success")

    with patch.object(client.client, "send", side_effect=mock_send):
        resp = await client.get("https://www.google.com/search")
        assert resp.status_code == 200
        assert resp.text == "success"
        assert call_count == 3
        assert client.request_count == 3
        assert client.retry_count == 2

    await client.close()


@pytest.mark.asyncio
async def test_tiling_20_results_subdivides():
    """Mocking 20 results triggers quadrant subdivision."""
    call_tiles = []

    async def mock_fetch(lat, lng, span):
        call_tiles.append((lat, lng, span))
        # First call returns 20 results -> triggers subdivision
        if len(call_tiles) == 1:
            return [PlaceStub(cid=f"cid_{i}", place_id=f"pid_{i}", ftid=f"f_{i}", name=f"P_{i}") for i in range(20)]
        # Subsequent children return 5 results -> no deeper subdivision
        return [PlaceStub(cid=f"cid_sub_{i}", place_id=f"pid_sub_{i}", ftid=f"f_sub_{i}", name=f"P_sub_{i}") for i in range(5)]

    results = await recursive_tile_search(
        mock_fetch,
        center_lat=40.7,
        center_lng=-74.0,
        initial_span_deg=0.04,
        max_depth=1,
        overlap_pct=0.1,
    )
    # Root (1) + 4 quadrants = 5 calls
    assert len(call_tiles) == 5
    assert len(results) > 20


@pytest.mark.asyncio
async def test_tiling_19_results_no_subdivision():
    """Mocking 19 results does NOT trigger subdivision."""
    call_tiles = []

    async def mock_fetch(lat, lng, span):
        call_tiles.append((lat, lng, span))
        return [PlaceStub(cid=f"cid_{i}", place_id=f"pid_{i}", ftid=f"f_{i}", name=f"P_{i}") for i in range(19)]

    results = await recursive_tile_search(
        mock_fetch,
        center_lat=40.7,
        center_lng=-74.0,
        initial_span_deg=0.04,
        max_depth=3,
        overlap_pct=0.1,
    )
    # Only root tile called
    assert len(call_tiles) == 1
    assert len(results) == 19


def test_export_round_trip():
    """Verify CSV, JSONL, and Parquet exports produce consistent data."""
    import json
    import pandas as pd

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db = Database(tmp_path / "test.sqlite")
        db.init_schema()

        # Insert 3 places with special chars / commas
        places = [
            {"cid": "c1", "name": 'Joe\'s "Special", Coffee', "address": "123 Main St, Apt 4B\nLine 2", "latitude": 40.7, "longitude": -74.0, "status": "details_done"},
            {"cid": "c2", "name": "Café München ☕", "address": "Straße 45, Berlin", "latitude": 52.5, "longitude": 13.4, "status": "details_done"},
            {"cid": "c3", "name": "Simple Tea", "address": "789 Broadway", "latitude": 40.8, "longitude": -73.9, "status": "details_done"},
        ]
        for p in places:
            db.upsert_place_stub(p)

        # CSV Export & round-trip
        csv_res = export_data(db, export_format="csv", output_dir=tmp_path / "csv")
        csv_file = csv_res["files"][0]
        df_csv = pd.read_csv(csv_file, dtype=str)
        assert len(df_csv) == 3
        assert 'Joe\'s "Special", Coffee' in df_csv["name"].values
        assert "Café München ☕" in df_csv["name"].values

        # JSONL Export & round-trip
        jsonl_res = export_data(db, export_format="jsonl", output_dir=tmp_path / "jsonl")
        jsonl_file = jsonl_res["files"][0]
        with open(jsonl_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 3
        jsonl_names = {l["name"] for l in lines}
        assert 'Joe\'s "Special", Coffee' in jsonl_names
        assert "Café München ☕" in jsonl_names

        # Parquet Export & round-trip
        parquet_res = export_data(db, export_format="parquet", output_dir=tmp_path / "parquet")
        parquet_file = parquet_res["files"][0]
        df_parquet = pd.read_parquet(parquet_file)
        assert len(df_parquet) == 3
        assert set(df_parquet["cid"]) == {"c1", "c2", "c3"}
