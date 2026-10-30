import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from gmaps_scraper.geo.boundaries import (
    get_region_bounding_boxes,
    get_region_composite_bbox,
    US_STATE_BOUNDS,
    MEXICO_ENTITY_BOUNDS,
)
from gmaps_scraper.tiling import (
    GeoTile,
    generate_region_grid_tiles,
)
import httpx
from gmaps_scraper.http import HttpClient
from gmaps_scraper.export import (
    format_region_folder_name,
    resolve_safe_export_path,
    export_state_category_data,
    export_state_complete_package,
    list_all_state_exports,
)
from gmaps_scraper.db import Database


# --------------------------------------------------------------------------
# 1. Geographic Boundaries & Multi-Part Alaska Tests
# --------------------------------------------------------------------------
def test_all_50_us_states_and_32_mexican_entities_have_bounds():
    assert len(US_STATE_BOUNDS) == 50
    assert len(MEXICO_ENTITY_BOUNDS) == 32

    # Check key states
    assert "AK" in US_STATE_BOUNDS
    assert "CA" in US_STATE_BOUNDS
    assert "TX" in US_STATE_BOUNDS
    assert "NY" in US_STATE_BOUNDS

    # Check key Mexican entities
    assert "CMX" in MEXICO_ENTITY_BOUNDS
    assert "MEX" in MEXICO_ENTITY_BOUNDS
    assert "JAL" in MEXICO_ENTITY_BOUNDS
    assert "NLE" in MEXICO_ENTITY_BOUNDS


def test_alaska_multipart_geometry():
    parts = get_region_bounding_boxes("US", "AK")
    assert len(parts) == 3

    # Mainland/Southeast
    mainland = parts[0]
    assert mainland.part_name == "mainland_southeast"
    assert mainland.min_lat >= 50.0 and mainland.max_lat <= 72.0
    assert mainland.min_lng < mainland.max_lng

    # Eastern Aleutians
    aleutian_east = parts[1]
    assert aleutian_east.min_lng == -180.0
    assert aleutian_east.max_lng == -164.0

    # Western Aleutians crossing 180 meridian (Eastern hemisphere positive lng)
    aleutian_west = parts[2]
    assert aleutian_west.min_lng == 172.0
    assert aleutian_west.max_lng == 180.0

    # Full combined bounding box
    bbox = get_region_composite_bbox("US", "AK")
    assert bbox is not None
    assert bbox.min_lat <= 55.0
    assert bbox.max_lat >= 71.0


def test_mexico_city_and_state_of_mexico_bounds():
    cmx_box = get_region_composite_bbox("MX", "CMX")
    assert cmx_box is not None
    assert cmx_box.min_lat >= 19.0 and cmx_box.max_lat <= 19.6
    assert cmx_box.min_lng >= -99.4 and cmx_box.max_lng <= -98.9

    mex_box = get_region_composite_bbox("MX", "MEX")
    assert mex_box is not None
    assert mex_box.min_lat >= 18.3 and mex_box.max_lat <= 20.3



# --------------------------------------------------------------------------
# 2. Tile Generation, Deterministic IDs, and Subdivision
# --------------------------------------------------------------------------
def test_deterministic_tile_generation():
    tiles_1 = generate_region_grid_tiles(
        country_code="US",
        region_code="RI",
        category="healthcare",
        task_id="task_1",
        run_id="run_1",
        step_deg=0.5
    )
    tiles_2 = generate_region_grid_tiles(
        country_code="US",
        region_code="RI",
        category="healthcare",
        task_id="task_1",
        run_id="run_1",
        step_deg=0.5
    )

    assert len(tiles_1) > 0
    assert len(tiles_1) == len(tiles_2)

    # Identical deterministic IDs and bounds
    for t1, t2 in zip(tiles_1, tiles_2):
        assert t1.tile_id == t2.tile_id
        assert t1.min_lat == t2.min_lat
        assert t1.max_lat == t2.max_lat
        assert t1.min_lng == t2.min_lng
        assert t1.max_lng == t2.max_lng
        assert t1.category == "healthcare"
        assert t1.depth == 0


def test_tile_subdivision_quadrants():
    parent = GeoTile(
        tile_id="t_test_parent",
        parent_tile_id=None,
        task_id="task_test",
        run_id="run_test",
        country_code="US",
        region_code="CA",
        category="dentists",
        min_lat=34.0,
        max_lat=36.0,
        min_lng=-118.0,
        max_lng=-116.0,
        center_lat=35.0,
        center_lng=-117.0,
        span_deg=2.0,
        depth=0
    )

    children = parent.subdivide(overlap_pct=0.0)
    assert len(children) == 4

    ids = [c.tile_id for c in children]
    assert "t_test_parent_nw" in ids
    assert "t_test_parent_ne" in ids
    assert "t_test_parent_sw" in ids
    assert "t_test_parent_se" in ids

    for c in children:
        assert c.parent_tile_id == "t_test_parent"
        assert c.depth == 1
        assert c.category == "dentists"
        # Latitudes and longitudes must fall precisely within parent bounds
        assert c.min_lat >= parent.min_lat - 0.01
        assert c.max_lat <= parent.max_lat + 0.01
        assert c.min_lng >= parent.min_lng - 0.01
        assert c.max_lng <= parent.max_lng + 0.01
        # Span must be exactly half
        assert pytest.approx(c.span_deg) == 1.0


def test_tile_subdivision_depth_limit():
    parent = GeoTile(
        tile_id="t_deep",
        parent_tile_id=None,
        task_id="task_test",
        run_id="run_test",
        country_code="US",
        region_code="CA",
        category="dentists",
        min_lat=34.0,
        max_lat=34.005,
        min_lng=-118.0,
        max_lng=-117.995,
        center_lat=34.0025,
        center_lng=-117.9975,
        span_deg=0.005,
        depth=3
    )

    # If depth >= max_depth, subdivide returns empty list
    children = parent.subdivide(max_depth=3)
    assert len(children) == 0



# --------------------------------------------------------------------------
# 3. HTTP Timeout Recovery & Retry Logic
# --------------------------------------------------------------------------
def test_parse_retry_after():
    client = HttpClient(user_agent="test-agent")
    # Integer seconds
    assert client._parse_retry_after("12") == 12.0
    # Clamped to at least 1.0 second minimum retry backoff
    assert client._parse_retry_after("0") == 1.0

    # RFC 2822 date
    date_header = "Wed, 21 Oct 2026 07:28:00 GMT"
    # Will either parse to a non-negative float or None
    parsed = client._parse_retry_after(date_header)
    assert parsed is None or isinstance(parsed, float)

    # Invalid header
    assert client._parse_retry_after("invalid-value") is None
    assert client._parse_retry_after(None) is None


@pytest.mark.asyncio
async def test_http_retryable_vs_permanent_errors():
    client = HttpClient(user_agent="test-agent", max_retries=2, backoff_base=0.01)

    # 1. 404 should NOT retry and should return fast
    with patch.object(client.client, "get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        resp = await client.get("https://example.com/notfound")
        assert resp.status_code == 404
        assert mock_get.call_count == 1

    # 2. Transient timeout retries up to max_retries
    with patch.object(client.client, "get", side_effect=httpx.ReadTimeout("Timed out")) as mock_timeout:
        with pytest.raises(httpx.ReadTimeout):
            await client.get("https://example.com/timeout")
        assert mock_timeout.call_count == 2

    await client.close()


# --------------------------------------------------------------------------
# 4. State-Wise, Date-Wise Exports and Summaries
# --------------------------------------------------------------------------
def test_format_region_folder_name():
    # US states
    assert format_region_folder_name("US", "AK", "Alaska") == "Alaska_AK"
    assert format_region_folder_name("US", "CA", "California") == "California_CA"

    # Mexico entities with diacritics
    assert format_region_folder_name("MX", "CMX", "Ciudad de México") == "Ciudad_de_Mexico_CMX"
    assert format_region_folder_name("MX", "MEX", "Estado de México") == "Estado_de_Mexico_MEX"
    assert format_region_folder_name("MX", "MIC", "Michoacán") == "Michoacan_MIC"


def test_resolve_safe_export_path(tmp_path):
    base_dir = tmp_path / "exports"
    base_dir.mkdir()

    valid_file = base_dir / "2026-10-22" / "US" / "Alaska_AK" / "all_businesses.csv"
    valid_file.parent.mkdir(parents=True)
    valid_file.write_text("cid,name\n")

    # Safe resolution
    resolved = resolve_safe_export_path(base_dir, "2026-10-22/US/Alaska_AK/all_businesses.csv")
    assert resolved == valid_file

    # Path traversal attack detection
    with pytest.raises((ValueError, PermissionError), match="Access denied"):
        resolve_safe_export_path(base_dir, "../../../etc/passwd")

    with pytest.raises((ValueError, PermissionError), match="Access denied"):
        resolve_safe_export_path(base_dir, "2026-10-22/../../secret.txt")


def test_export_state_complete_package_and_summary(tmp_path):
    db_file = tmp_path / "test_gmaps.sqlite"
    db = Database(str(db_file))
    db.init_schema()

    # Insert sample businesses
    db.upsert_place_stub({
        "cid": "cid_ak_1",
        "name": "Alaska Dental Care",
        "category": "dentists",
        "country": "US",
        "region_code": "AK",
        "latitude": 61.2181,
        "longitude": -149.9003,
        "website": "https://alaskadental.com",
        "status": "pending",
    })
    db.upsert_place_stub({
        "cid": "cid_ak_2",
        "name": "Anchorage Auto Repair",
        "category": "car_repair",
        "country": "US",
        "region_code": "AK",
        "latitude": 61.2150,
        "longitude": -149.8800,
        "website": "https://anchorageauto.com",
        "status": "pending",
    })

    # Insert tiles to test completeness tracking
    db.create_geographic_tile(GeoTile(
        tile_id="t_ak_1",
        task_id="task_ak_1",
        run_id="run_1",
        country_code="US",
        region_code="AK",
        category="dentists",
        min_lat=60.0, max_lat=62.0, min_lng=-150.0, max_lng=-148.0,
        center_lat=61.0, center_lng=-149.0, span_deg=2.0,
        status="completed",
        results_count=10,
        new_records=1,
    ))

    exports_dir = tmp_path / "exports"
    summary = export_state_complete_package(
        db=db,
        exports_base_dir=exports_dir,
        country="US",
        region_code="AK",
        run_date="2026-10-22"
    )

    assert summary["country"] == "US"
    assert summary["region_code"] == "AK"
    assert summary["region_name"] == "Alaska"
    assert summary["total_businesses"] == 2
    assert "dentists" in summary["categories"]
    assert "car_repair" in summary["categories"]

    # Check files created on disk
    state_dir = exports_dir / "2026-10-22" / "US" / "Alaska_AK"
    assert state_dir.exists()
    assert (state_dir / "all_businesses.csv").exists()
    assert (state_dir / "dentists.csv").exists()
    assert (state_dir / "car_repair.csv").exists()
    assert (state_dir / "run_summary.json").exists()

    # List exports verification
    all_exports = list_all_state_exports(exports_dir)
    assert len(all_exports) == 1
    assert all_exports[0]["region_code"] == "AK"
    assert all_exports[0]["total_businesses"] == 2
