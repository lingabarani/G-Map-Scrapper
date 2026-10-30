import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from typer.testing import CliRunner

from gmaps_scraper.db import Database
from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.orchestrator import PipelineOrchestrator, PipelineConfig, CollectionResult
from gmaps_scraper.models import PlaceStub
from gmaps_scraper.dedup import DeduplicationService, merge_cluster_records
from gmaps_scraper.cli import app


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_raw_dedup.sqlite"
    db = Database(db_file)
    db.init_schema()
    return db


@pytest.fixture
def scraper_cfg(tmp_path: Path):
    return ScraperConfig(
        db_path=str(tmp_path / "test_raw_dedup.sqlite"),
        raw_data_dir=str(tmp_path / "raw"),
        photos_dir=str(tmp_path / "photos"),
        databases_dir=str(tmp_path / "databases"),
        exports_dir=str(tmp_path / "exports"),
        logs_dir=str(tmp_path / "logs"),
        category_workers=2,
        request_concurrency=2,
    )


# =============================================================================
# 1. Database Schema and Idempotent Insertion Tests
# =============================================================================

def test_raw_places_table_creation(temp_db: Database):
    """Verify raw_places table exists with all required index columns."""
    with temp_db.get_connection() as conn:
        cols = conn.execute("PRAGMA table_info(raw_places)").fetchall()
        col_names = {c["name"] for c in cols}
        assert "id" in col_names
        assert "raw_id" in col_names
        assert "cid" in col_names
        assert "name" in col_names
        assert "search_task_id" in col_names
        assert "tile_id" in col_names
        assert "run_id" in col_names
        assert "source_data" in col_names
        assert "collected_at" in col_names


def test_insert_raw_places_and_replay_idempotency(temp_db: Database):
    """
    Verify:
    1. Multiple distinct discovery events for the same CID (e.g. across tiles) are BOTH retained.
    2. Replaying the EXACT same tile discovery event updates/ignores rather than duplicating.
    """
    # Event 1: Tile 1 discovers business 9999
    event_1 = {
        "cid": "9999",
        "name": "Acme Health",
        "category": "healthcare",
        "country_code": "US",
        "region": "CA",
        "search_task_id": "col_us_ca_healthcare",
        "tile_id": "tile_ca_1",
        "run_id": "run_100",
        "review_count": 10,
    }
    # Event 2: Tile 2 discovers the SAME business 9999 in overlapping area
    event_2 = {
        "cid": "9999",
        "name": "Acme Healthcare Center",
        "category": "healthcare",
        "country_code": "US",
        "region": "CA",
        "search_task_id": "col_us_ca_healthcare",
        "tile_id": "tile_ca_2",
        "run_id": "run_100",
        "review_count": 12,
    }

    assert temp_db.insert_raw_place(event_1) is True
    assert temp_db.insert_raw_place(event_2) is True

    # Both discovery events are preserved
    assert temp_db.get_raw_places_count() == 2
    records = temp_db.get_raw_places()
    assert len(records) == 2
    assert {r["tile_id"] for r in records} == {"tile_ca_1", "tile_ca_2"}

    # Replay Event 1 (e.g. task interrupted and replayed)
    replay_event_1 = dict(event_1)
    replay_event_1["review_count"] = 15  # updated observation
    temp_db.insert_raw_place(replay_event_1)

    # Count is still 2 - no accidental duplicate created on replay!
    assert temp_db.get_raw_places_count() == 2
    updated_records = temp_db.get_raw_places()
    tile_1_rec = next(r for r in updated_records if r["tile_id"] == "tile_ca_1")
    assert tile_1_rec["review_count"] == 15


# =============================================================================
# 2. Collection-Only Mode Execution Tests
# =============================================================================

@pytest.mark.asyncio
async def test_collection_only_execution_preserves_raw_duplicates_and_leaves_places_empty(temp_db, scraper_cfg):
    """
    Verify that in collection-only mode:
    - All raw business discovery events are fetched and persisted to raw_places.
    - Repeated discovery events across tiles/categories are PRESERVED.
    - Deduplication to 'places' table is NOT invoked (places table remains 0 rows).
    - Website discovery (Stage 2) and enrichment (Stage 3) are NOT invoked.
    """
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    cfg = PipelineConfig(
        country="US",
        regions=["DE"],  # Delaware (small region)
        categories=["healthcare", "hospitality"],
        collection_only=True,
        stages={"collection": True, "discovery": True, "enrichment": True},  # collection_only overrides these
        dry_run=False,
    )

    shared_cid = "cid_shared_999"
    stub_health = PlaceStub(
        cid=shared_cid,
        ftid="0x0:0x999",
        place_id="PID_999",
        name="Cross-Border Clinic",
        address="Wilmington, DE",
        category="healthcare",
        rating=4.5,
        review_count=20,
        status="pending"
    )
    stub_hotel = PlaceStub(
        cid=shared_cid,
        ftid="0x0:0x999",
        place_id="PID_999",
        name="Cross-Border Clinic & Wellness Hotel",
        address="Wilmington, DE",
        category="hospitality",
        rating=4.7,
        review_count=25,
        status="pending"
    )

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, collection_only=False):
        # Both categories encounter the shared business
        chosen_stub = stub_health if category == "healthcare" else stub_hotel
        raw_id = f"{task_id}:tile_test:{chosen_stub.cid}"
        raw_dict = chosen_stub.model_dump()
        raw_dict["raw_id"] = raw_id
        raw_dict["country_code"] = country_code
        raw_dict["region"] = region.code
        raw_dict["search_task_id"] = task_id
        raw_dict["tile_id"] = "tile_test"
        raw_dict["run_id"] = run_id
        raw_dict["source_data"] = json.dumps(chosen_stub.model_dump())

        temp_db.insert_raw_place(raw_dict)
        return CollectionResult(1, new_records=1, duplicates_skipped=0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute), \
         patch("gmaps_scraper.orchestrator.WebsiteDiscoveryService") as mock_disc, \
         patch("gmaps_scraper.orchestrator.WebsiteEnrichmentService") as mock_enrich:

        run_id = await orchestrator.start(cfg)
        assert run_id is not None
        await orchestrator._worker_task

        # Verify Stage 2 and Stage 3 services were NEVER called
        mock_disc.assert_not_called()
        mock_enrich.assert_not_called()

    # Raw collection records: both categories saved their discovery events
    raw_count = temp_db.get_raw_places_count()
    assert raw_count == 2, "Both discovery events must be preserved in raw_places"

    raw_items = temp_db.get_raw_places()
    categories_found = {r["category"] for r in raw_items}
    assert categories_found == {"healthcare", "hospitality"}

    # Canonical places table must NOT be modified / populated in collection-only mode
    canonical_count = temp_db.get_places_count()
    assert canonical_count == 0, "Places table must remain completely empty during collection-only run"


# =============================================================================
# 3. Checkpointing and Resume in Collection-Only Mode Tests
# =============================================================================

@pytest.mark.asyncio
async def test_collection_only_checkpoints_and_resume(temp_db, scraper_cfg):
    """
    Verify that collection tasks create checkpoints and resume skips completed tasks.
    """
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    cfg = PipelineConfig(
        country="US",
        regions=["WY"],
        categories=["healthcare", "food"],
        collection_only=True,
        resume_checkpoint=True,
    )

    # 1. Pre-mark 'col_us_wy_healthcare' as completed
    temp_db.create_collection_task({
        "task_id": "col_us_wy_healthcare",
        "country_code": "US",
        "region_code": "WY",
        "region_name": "Wyoming",
        "category": "healthcare",
        "tile_id": "tile_wy",
        "status": "completed",
        "records_collected": 15,
        "run_id": "run_prev",
    })

    executed_categories = []

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, collection_only=False):
        executed_categories.append(category)
        return CollectionResult(5, new_records=5, duplicates_skipped=0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    # Only 'food' should have executed; 'healthcare' was skipped via resume checkpoint
    assert executed_categories == ["food"]
    assert "healthcare" not in executed_categories


# =============================================================================
# 4. Separate Manual Deduplication Job Tests
# =============================================================================

def test_deduplication_clusters_merges_and_writes_to_places(temp_db: Database):
    """
    Verify DeduplicationService:
    1. Reads raw collection.
    2. Clusters and normalizes records by CID.
    3. Merges duplicate discovery events into high-quality canonical places.
    4. Writes to 'places' table.
    5. Leaves 'raw_places' unaltered.
    """
    # Insert 3 discovery events for the same business with slight variations
    raw_event_1 = {
        "cid": "cid_omega_100",
        "ftid": "0x123:0xabc",
        "place_id": "PID_OMEGA",
        "name": "Omega Medical",
        "address": "123 Main St, Austin, TX",
        "street_address": "123 Main St",
        "latitude": 30.267,
        "longitude": -97.743,
        "phone": "(512) 555-0100",
        "phone_e164": "+15125550100",
        "category": "doctor",
        "country_code": "US",
        "region": "TX",
        "rating": 4.6,
        "review_count": 50,
        "search_task_id": "col_us_tx_doctor",
        "tile_id": "tile_tx_1",
        "run_id": "run_1",
    }
    raw_event_2 = {
        "cid": "cid_omega_100",
        "ftid": "0x123:0xabc",
        "place_id": "PID_OMEGA",
        "name": "Omega Medical Center & Urgent Care",  # Longer, richer name
        "address": "123 Main St, Suite 400, Austin, TX",
        "street_address": "123 Main St",
        "latitude": 30.2672,
        "longitude": -97.7431,
        "phone": "+15125550100",
        "website": "https://www.omegamedical.com",
        "category": "healthcare",  # Different category
        "country_code": "US",
        "region": "TX",
        "rating": 4.8,
        "review_count": 65,  # Higher review count
        "search_task_id": "col_us_tx_healthcare",
        "tile_id": "tile_tx_2",
        "run_id": "run_1",
    }
    # Distinct business
    raw_event_3 = {
        "cid": "cid_beta_200",
        "name": "Beta Pharmacy",
        "address": "456 Oak St, Austin, TX",
        "street_address": "456 Oak St",
        "category": "pharmacy",
        "country_code": "US",
        "region": "TX",
        "review_count": 10,
        "search_task_id": "col_us_tx_pharmacy",
        "tile_id": "tile_tx_1",
        "run_id": "run_1",
    }

    temp_db.insert_raw_place(raw_event_1)
    temp_db.insert_raw_place(raw_event_2)
    temp_db.insert_raw_place(raw_event_3)

    assert temp_db.get_raw_places_count() == 3
    assert temp_db.get_places_count() == 0

    dedup_svc = DeduplicationService(temp_db)

    # 1. Test Dry Run mode
    dry_metrics = dedup_svc.run_deduplication(dry_run=True)
    assert dry_metrics["raw_records_scanned"] == 3
    assert dry_metrics["unique_canonical_places"] == 2
    assert dry_metrics["duplicate_events_merged"] == 1
    # Dry run must NOT write to places
    assert temp_db.get_places_count() == 0
    # Raw collection intact
    assert temp_db.get_raw_places_count() == 3

    # 2. Test Live Deduplication mode
    metrics = dedup_svc.run_deduplication(dry_run=False)
    assert metrics["raw_records_scanned"] == 3
    assert metrics["unique_canonical_places"] == 2
    assert metrics["duplicate_events_merged"] == 1
    assert metrics["new_places_created"] == 2

    # Canonical places table now has 2 distinct deduplicated businesses
    assert temp_db.get_places_count() == 2

    # Verify merged canonical attributes
    omega = temp_db.get_place("cid_omega_100")
    assert omega is not None
    assert omega["name"] == "Omega Medical Center & Urgent Care"
    assert omega["website"] == "https://www.omegamedical.com"
    assert omega["review_count"] == 65
    assert omega["phone_e164"] == "+15125550100"

    # Crucial requirement: raw_places table must remain completely intact
    assert temp_db.get_raw_places_count() == 3


# =============================================================================
# 5. CLI Commands Integration Tests
# =============================================================================

def test_cli_counts_command(temp_db: Database, tmp_path: Path):
    """Test CLI 'counts' command reporting raw and canonical numbers."""
    runner = CliRunner()
    cfg_file = tmp_path / "user.yaml"
    cfg_file.write_text(f"db_path: '{temp_db.db_path}'\n", encoding="utf-8")

    # Insert raw discovery
    temp_db.insert_raw_place({
        "cid": "cid_test_cli",
        "name": "CLI Clinic",
        "category": "healthcare",
        "country_code": "US",
        "region": "TX",
    })

    result = runner.invoke(app, ["counts", "-c", str(cfg_file)])
    assert result.exit_code == 0
    assert "Raw Business Discoveries (raw_places)" in result.output
    assert "Canonical Deduplicated Places (places)" in result.output


def test_cli_dedup_command(temp_db: Database, tmp_path: Path):
    """Test CLI 'dedup' command."""
    runner = CliRunner()
    cfg_file = tmp_path / "user.yaml"
    cfg_file.write_text(f"db_path: '{temp_db.db_path}'\n", encoding="utf-8")

    temp_db.insert_raw_place({
        "cid": "cid_dedup_cli",
        "name": "CLI Medical",
        "category": "healthcare",
        "country_code": "US",
        "region": "FL",
    })

    result = runner.invoke(app, ["dedup", "-c", str(cfg_file)])
    assert result.exit_code == 0
    assert "Deduplication Job Summary" in result.output
    assert "Raw Records Scanned" in result.output
    assert temp_db.get_places_count() == 1


def test_cli_collect_command_dry_run(temp_db: Database, tmp_path: Path):
    """Test CLI 'collect' command in dry-run mode."""
    runner = CliRunner()
    cfg_file = tmp_path / "user.yaml"
    cfg_file.write_text(f"db_path: '{temp_db.db_path}'\n", encoding="utf-8")

    result = runner.invoke(app, [
        "collect",
        "--country", "US",
        "--regions", "WY",
        "--categories", "healthcare",
        "--dry-run",
        "--limit", "5",
        "-c", str(cfg_file)
    ])
    assert result.exit_code == 0
    assert "Starting Automated Pipeline Orchestrator" in result.output


@pytest.mark.asyncio
async def test_execute_collection_search_collection_only_direct(temp_db, scraper_cfg):
    """
    Direct test of _execute_collection_search in collection_only mode:
    Confirms raw records are inserted into raw_places, places remains untouched,
    and repeated discoveries across tiles are retained.
    """
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    from gmaps_scraper.scope import get_region
    reg_info = get_region("US", "DE")

    stub_1 = PlaceStub(
        cid="cid_direct_1",
        ftid="0x0:0x1",
        place_id="PID_1",
        name="Direct Business 1",
        address="100 King St, Wilmington, DE",
        category="healthcare",
        rating=4.2,
        review_count=10,
        status="pending"
    )
    stub_2 = PlaceStub(
        cid="cid_direct_2",
        ftid="0x0:0x2",
        place_id="PID_2",
        name="Direct Business 2",
        address="200 Queen St, Wilmington, DE",
        category="healthcare",
        rating=4.8,
        review_count=50,
        status="pending"
    )

    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ")]}'\n[]"
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("gmaps_scraper.orchestrator.parse_search_rpc_response", return_value=[stub_1, stub_2]):
        res = await orchestrator._execute_collection_search(
            http_client=mock_http,
            country_code="US",
            region=reg_info,
            category="healthcare",
            limit=10,
            run_id="run_direct_test",
            task_id="col_us_de_healthcare",
            dry_run=False,
            collection_only=True
        )
        assert res.total_unique == 10

    # raw_places has 10 discovery records across tiles (repeated events preserved!)
    assert temp_db.get_raw_places_count() == 10
    # places table must be 0 (no deduplication in collection-only mode)
    assert temp_db.get_places_count() == 0

    # Running a separate deduplication merges them into 2 canonical places
    dedup = DeduplicationService(temp_db)
    d_res = dedup.run_deduplication(country_code="US")
    assert d_res["raw_records_scanned"] == 10
    assert d_res["unique_canonical_places"] == 2
    assert d_res["duplicate_events_merged"] == 8
    assert temp_db.get_places_count() == 2
    assert temp_db.get_raw_places_count() == 10


@pytest.mark.asyncio
async def test_mexico_and_usa_collection_only_and_dedup(temp_db, scraper_cfg):
    """
    Verify collection-only mode and separate deduplication for both USA and Mexico:
    Confirms region codes, country codes, and multi-country deduplication work seamlessly.
    """
    # Simulate raw collection for Mexico (JAL) and USA (CA)
    mex_record = {
        "cid": "cid_mex_101",
        "ftid": "0x0:0xjal1",
        "place_id": "PID_MEX1",
        "name": "Clinica Guadalajara",
        "address": "Av Juarez 100, Guadalajara, JAL, MX",
        "street_address": "Av Juarez 100",
        "category": "healthcare",
        "country_code": "MX",
        "region": "JAL",
        "review_count": 30,
        "search_task_id": "col_mx_jal_healthcare",
        "tile_id": "tile_mx_jal_1",
        "run_id": "run_mx_1",
    }
    usa_record = {
        "cid": "cid_usa_201",
        "ftid": "0x0:0xca1",
        "place_id": "PID_USA1",
        "name": "California Wellness",
        "address": "500 Market St, San Francisco, CA, US",
        "street_address": "500 Market St",
        "category": "healthcare",
        "country_code": "US",
        "region": "CA",
        "review_count": 45,
        "search_task_id": "col_us_ca_healthcare",
        "tile_id": "tile_us_ca_1",
        "run_id": "run_us_1",
    }

    temp_db.insert_raw_place(mex_record)
    temp_db.insert_raw_place(usa_record)

    assert temp_db.get_raw_places_count(country_code="MX") == 1
    assert temp_db.get_raw_places_count(country_code="US") == 1
    assert temp_db.get_places_count() == 0

    dedup = DeduplicationService(temp_db)

    # 1. Deduplicate Mexico only
    mx_res = dedup.run_deduplication(country_code="MX")
    assert mx_res["unique_canonical_places"] == 1
    assert temp_db.get_places_count(country_code="MX") == 1
    assert temp_db.get_places_count(country_code="US") == 0

    # 2. Deduplicate USA only
    us_res = dedup.run_deduplication(country_code="US")
    assert us_res["unique_canonical_places"] == 1
    assert temp_db.get_places_count(country_code="US") == 1

    # Total canonical places is now 2
    assert temp_db.get_places_count() == 2
    # Raw records are still intact
    assert temp_db.get_raw_places_count() == 2
