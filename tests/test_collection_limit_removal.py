import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock

from gmaps_scraper.orchestrator import PipelineOrchestrator, PipelineConfig, CollectionResult
from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.db import Database
from gmaps_scraper.models import PlaceStub
from gmaps_scraper.http import HttpClient


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_gmaps.sqlite"
    db = Database(str(db_file))
    db.init_schema()
    return db


@pytest.fixture
def orchestrator(temp_db):
    cfg = ScraperConfig(db_path=str(temp_db.db_path))
    return PipelineOrchestrator(temp_db, cfg)


def test_pipeline_config_default_limit_is_none():
    """Confirms PipelineConfig defaults collection_limit to None (unlimited)."""
    cfg = PipelineConfig(country="US", regions=["KS"], categories=["healthcare"])
    assert cfg.collection_limit is None
    assert cfg.batch_size == 50  # Requirement 8: discovery/enrichment limits unchanged


@pytest.mark.asyncio
async def test_collection_search_collects_more_than_50_places_when_limit_is_none(orchestrator, temp_db):
    """
    Directly tests that _execute_collection_search does not stop at 50 records
    when collection_limit is None, successfully collecting all available places (> 50).
    """
    region_info = MagicMock()
    region_info.code = "KS"
    region_info.name = "Kansas"

    http_client = MagicMock(spec=HttpClient)

    # Run in dry_run mode where mock tiles generate place stubs
    result = await orchestrator._execute_collection_search(
        http_client=http_client,
        country_code="US",
        region=region_info,
        category="healthcare",
        limit=None,  # No artificial limit
        run_id="pipe_test_ks_unlimited",
        dry_run=True
    )

    # Confirm it collected more than 50 unique records
    assert isinstance(result, CollectionResult)
    assert result.total_unique > 50, f"Expected > 50 records, got {result.total_unique}"
    assert result.new_records > 50
    assert int(result) == result.total_unique
    assert int(result) > 50


@pytest.mark.asyncio
async def test_collection_search_honors_explicit_limit_when_configured(orchestrator, temp_db):
    """
    Confirms that when an explicit limit is configured (e.g. limit=50 or limit=25),
    it is honored as a safety control.
    """
    region_info = MagicMock()
    region_info.code = "KS"
    region_info.name = "Kansas"

    http_client = MagicMock(spec=HttpClient)

    result_capped = await orchestrator._execute_collection_search(
        http_client=http_client,
        country_code="US",
        region=region_info,
        category="healthcare",
        limit=25,
        run_id="pipe_test_ks_capped",
        dry_run=True
    )

    assert result_capped.total_unique == 25
    assert result_capped.new_records == 25


@pytest.mark.asyncio
async def test_one_state_one_category_pipeline_run_yields_over_50_records(orchestrator, temp_db):
    """
    Executes a complete pipeline run for one state (KS) and one category (healthcare)
    with collection_limit=None (the new default), verifying that over 50 unique
    records are collected, task events report correct metrics, and terminal fields are present.
    """
    cfg = PipelineConfig(
        country="US",
        regions=["KS"],
        categories=["healthcare"],
        collection_limit=None,  # Unlimited
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True
    )

    events = []
    q = orchestrator.subscribe()

    async def collect_events():
        while True:
            msg = await q.get()
            events.append(msg)
            if msg.get("event") in ("completed", "cancelled", "failed"):
                break

    collector = asyncio.create_task(collect_events())

    run_id = await orchestrator.start(cfg)
    await orchestrator._worker_task
    await collector
    orchestrator.unsubscribe(q)

    # Verify task completed with > 50 places
    task_completed_events = [e for e in events if e.get("event") == "task_completed"]
    assert len(task_completed_events) == 1
    t_data = task_completed_events[0]["data"]

    assert t_data["country"] == "US"
    assert t_data["region"] == "KS"
    assert t_data["category"] == "healthcare"
    assert t_data["records_collected"] > 50
    assert t_data["new_records"] > 50
    assert "duplicates_skipped" in t_data
    assert "total_unique" in t_data
    assert "remaining_tasks" in t_data
    assert "elapsed_seconds" in t_data
    assert t_data["remaining_tasks"] == 0  # Only 1 task in this run

    # Verify database persistence
    tasks = temp_db.get_collection_tasks(country_code="US", region_code="KS")
    assert len(tasks) == 1
    assert tasks[0]["status"] == "completed"
    assert tasks[0]["records_collected"] > 50
