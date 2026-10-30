import pytest
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from gmaps_scraper.db import Database
from gmaps_scraper.orchestrator import PipelineOrchestrator, PipelineConfig


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_gmaps.sqlite"
    db = Database(db_file)
    db.init_schema()
    return db


def test_today_saved_records_and_persistence_timestamp(temp_db: Database):
    """
    Verifies:
    - raw_saved_today counts only records collected today (local time).
    - records from yesterday are counted in total_raw but not today.
    - last successful commit timestamp is updated.
    - new inserts return True, duplicates/updates return False.
    """
    # 1. Initially 0
    stats = temp_db.get_collection_progress_stats()
    assert stats["raw_saved_today"] == 0
    assert stats["total_raw"] == 0
    assert stats["total_canonical"] == 0

    # 2. Insert record collected yesterday
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d 10:00:00")
    with temp_db.get_connection() as conn:
        conn.execute("""
            INSERT INTO raw_places (
                raw_id, cid, name, country_code, region, category, collected_at
            ) VALUES (
                'task1:tile1:cid_old', 'cid_old', 'Old Place', 'US', 'NV', 'food', ?
            )
        """, (yesterday,))
        conn.commit()

    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV")
    assert stats["total_raw"] == 1
    assert stats["raw_saved_today"] == 0

    # 3. Insert record collected today via insert_raw_place
    new_record = {
        "raw_id": "task1:tile1:cid_new",
        "cid": "cid_new",
        "name": "New Place",
        "country_code": "US",
        "region": "NV",
        "category": "food",
        "search_task_id": "task1",
        "tile_id": "tile1",
    }
    was_new = temp_db.insert_raw_place(new_record)
    assert was_new is True

    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV")
    assert stats["total_raw"] == 2
    assert stats["raw_saved_today"] == 1
    assert stats["last_db_write"] != "N/A"

    # 4. Insert duplicate/upsert of existing record
    was_new_dup = temp_db.insert_raw_place(new_record)
    assert was_new_dup is False  # Already exists, so was_new is False!

    # Counts remain 2 total, 1 today
    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV")
    assert stats["total_raw"] == 2
    assert stats["raw_saved_today"] == 1


def test_tile_accounting_subdivision_and_growing_totals(temp_db: Database):
    """
    Verifies:
    - Root tiles initially generated.
    - Subdivided parent tiles are NOT counted as unfinished work.
    - Child tiles created by subdivision are counted.
    - Counts by status: pending, running, retry_pending, failed, completed, limit_reached.
    - can_grow is True while in_flight tiles exist.
    - Completion is never 100% while unfinished or failed tiles exist.
    """
    # 1. Create a root tile
    root_tile = {
        "tile_id": "t_root_1",
        "parent_tile_id": None,
        "task_id": "task_nv_food",
        "run_id": "run_1",
        "country_code": "US",
        "region_code": "NV",
        "category": "food",
        "min_lat": 36.0,
        "max_lat": 37.0,
        "min_lng": -115.0,
        "max_lng": -114.0,
        "center_lat": 36.5,
        "center_lng": -114.5,
        "span_deg": 1.0,
        "depth": 0,
        "status": "pending",
    }
    temp_db.create_geographic_tile(root_tile)

    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV")
    assert stats["planned_root"] == 1
    assert stats["pending"] == 1
    assert stats["active_total"] == 1
    assert stats["remaining_tiles"] == 1
    assert stats["can_grow"] is True
    assert stats["subdivided_parents"] == 0

    # 2. Mark root tile as subdivided and create 4 children
    temp_db.update_geographic_tile("t_root_1", {"status": "subdivided"})
    for i in range(4):
        child_tile = dict(root_tile)
        child_tile["tile_id"] = f"t_root_1_c{i}"
        child_tile["parent_tile_id"] = "t_root_1"
        child_tile["depth"] = 1
        child_tile["status"] = "pending"
        temp_db.create_geographic_tile(child_tile)

    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV")
    # Subdivided parent is tracked separately, NOT counted as active unfinished work!
    assert stats["subdivided_parents"] == 1
    assert stats["child_tiles_awaiting"] == 4
    assert stats["pending"] == 4
    # Active total = 4 children (parent is excluded from active collection work items)
    assert stats["active_total"] == 4
    assert stats["remaining_tiles"] == 4
    assert stats["can_grow"] is True

    # 3. Complete 2 child tiles, 1 limit_reached, 1 running
    temp_db.update_geographic_tile("t_root_1_c0", {"status": "completed"})
    temp_db.update_geographic_tile("t_root_1_c1", {"status": "completed"})
    temp_db.update_geographic_tile("t_root_1_c2", {"status": "limit_reached"})
    temp_db.update_geographic_tile("t_root_1_c3", {"status": "running"})

    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV")
    assert stats["completed"] == 2
    assert stats["limit_reached"] == 1
    assert stats["finished_tiles"] == 3
    assert stats["running"] == 1
    assert stats["pending"] == 0
    assert stats["remaining_tiles"] == 1
    assert stats["can_grow"] is True  # 1 running tile can still subdivide!

    # 4. Fail the remaining tile
    temp_db.update_geographic_tile("t_root_1_c3", {"status": "failed"})
    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV")
    assert stats["failed"] == 1
    assert stats["finished_tiles"] == 3
    assert stats["remaining_tiles"] == 1  # Unresolved failure is remaining!
    assert stats["in_flight_tiles"] == 0
    assert stats["can_grow"] is False
    # Never 100% complete because failed > 0
    pct = (stats["finished_tiles"] / stats["active_total"]) * 100
    assert pct < 100.0


def test_orchestrator_live_collection_stats_integration(temp_db: Database):
    """
    Verifies that PipelineOrchestrator.get_live_collection_stats() integrates
    real DB counts and active run metrics.
    """
    cfg = PipelineConfig()
    orch = PipelineOrchestrator.get_instance(temp_db, cfg)

    # Simulate active run data
    orch._is_running = True
    orch._current_run_data = {
        "current_country": "US",
        "current_region": "NV",
        "current_category": "retail",
        "current_tile": "t_nv_retail_001",
        "run_new_records": 42,
        "run_duplicate_records": 10,
        "start_time": datetime.now().timestamp(),
    }

    stats = orch.get_live_collection_stats()
    assert stats["country"] == "US"
    assert stats["region"] == "NV"
    assert stats["category"] == "retail"
    assert stats["active_tile_id"] == "t_nv_retail_001"
    assert stats["run_new_records"] == 42
    assert stats["run_duplicate_records"] == 10


def test_resume_preserves_completed_and_avoids_double_counting(temp_db: Database):
    """
    Verifies that restarting/resuming collection properly recognizes existing completed tiles
    and does not inflate total counts or reset finished progress.
    """
    for i in range(5):
        temp_db.create_geographic_tile({
            "tile_id": f"tile_nv_{i}",
            "parent_tile_id": None,
            "task_id": "task_nv_retail",
            "run_id": "run_initial",
            "country_code": "US",
            "region_code": "NV",
            "category": "retail",
            "min_lat": 36.0 + i*0.1,
            "max_lat": 36.1 + i*0.1,
            "min_lng": -115.0,
            "max_lng": -114.9,
            "center_lat": 36.05 + i*0.1,
            "center_lng": -114.95,
            "span_deg": 0.1,
            "depth": 0,
            "status": "completed" if i < 3 else "pending",
        })

    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV")
    assert stats["completed"] == 3
    assert stats["pending"] == 2
    assert stats["finished_tiles"] == 3
    assert stats["active_total"] == 5
    assert stats["remaining_tiles"] == 2

    # Resume on a new run: completed tiles remain intact, remaining remains 2
    cfg = PipelineConfig()
    orch = PipelineOrchestrator.get_instance(temp_db, cfg)
    orch._is_running = True
    orch._current_run_data = {
        "current_country": "US",
        "current_region": "NV",
        "current_category": "retail",
        "run_new_records": 0,
        "run_duplicate_records": 0,
    }
    stats = orch.get_live_collection_stats()
    assert stats["completed"] == 3
    assert stats["remaining_tiles"] == 2
    assert stats["run_new_records"] == 0


def test_multi_category_dashboard_all_categories_visible(temp_db: Database):
    """
    Verifies:
    - All 10 configured categories appear in dashboard stats even when unstarted.
    - Statuses reflect PENDING, RUNNING, COMPLETED appropriately.
    - Category-level tile counts and remaining numbers match accurately.
    """
    categories = [
        "healthcare", "hospitality", "food", "retail", "fitness",
        "beauty", "automotive", "finance", "education", "real_estate"
    ]
    # Create tasks for healthcare and food
    temp_db.create_collection_task({
        "task_id": "task_healthcare",
        "run_id": "run_test",
        "country_code": "US",
        "region_code": "NV",
        "region_name": "Nevada",
        "category": "healthcare",
        "tile_id": "tile_health_1",
        "status": "in_progress",
        "records_collected": 150,
    })
    temp_db.create_collection_task({
        "task_id": "task_food",
        "run_id": "run_test",
        "country_code": "US",
        "region_code": "NV",
        "region_name": "Nevada",
        "category": "food",
        "tile_id": "tile_food_1",
        "status": "completed",
        "records_collected": 300,
    })

    # Create tiles for healthcare (1 completed, 1 pending)
    temp_db.create_geographic_tile({
        "tile_id": "tile_health_1",
        "task_id": "task_healthcare",
        "run_id": "run_test",
        "country_code": "US",
        "region_code": "NV",
        "category": "healthcare",
        "min_lat": 36.0, "max_lat": 36.5, "min_lng": -115.5, "max_lng": -115.0,
        "center_lat": 36.25, "center_lng": -115.25, "span_deg": 0.5, "depth": 0,
        "status": "completed", "results_count": 25, "new_records": 25, "duplicate_cids": 0,
    })
    temp_db.create_geographic_tile({
        "tile_id": "tile_health_2",
        "task_id": "task_healthcare",
        "run_id": "run_test",
        "country_code": "US",
        "region_code": "NV",
        "category": "healthcare",
        "min_lat": 36.5, "max_lat": 37.0, "min_lng": -115.5, "max_lng": -115.0,
        "center_lat": 36.75, "center_lng": -115.25, "span_deg": 0.5, "depth": 0,
        "status": "pending", "results_count": 0, "new_records": 0, "duplicate_cids": 0,
    })

    cfg = PipelineConfig(categories=categories)
    orch = PipelineOrchestrator.get_instance(temp_db, cfg)
    orch._is_running = True
    orch._category_states["healthcare"] = {"status": "RUNNING", "active_tile_id": "tile_health_2"}
    orch._category_states["food"] = {"status": "COMPLETED"}

    live_stats = orch.get_live_collection_stats()
    cat_stats = live_stats.get("categories", {})

    # All 10 categories present
    for cat in categories:
        assert cat in cat_stats, f"Category '{cat}' missing from dashboard categories!"

    # Healthcare: 1 completed, 1 pending, status RUNNING
    assert cat_stats["healthcare"]["status"] == "RUNNING"
    assert cat_stats["healthcare"]["active_tile_id"] == "tile_health_2"
    assert cat_stats["healthcare"]["finished_tiles"] == 1
    assert cat_stats["healthcare"]["remaining_tiles"] == 1

    # Food: status COMPLETED
    assert cat_stats["food"]["status"] == "COMPLETED"

    # Other unstarted categories: status PENDING
    assert cat_stats["retail"]["status"] == "PENDING"
    assert cat_stats["finance"]["status"] == "PENDING"


def test_live_business_names_per_category(temp_db: Database):
    """
    Verifies that parsed business names from real results are tracked per category
    without duplicates or fabrication.
    """
    cfg = PipelineConfig(categories=["healthcare", "hospitality"])
    orch = PipelineOrchestrator.get_instance(temp_db, cfg)

    orch.record_category_recent_names("healthcare", ["Sunrise Hospital", "Desert Clinic", "Sunrise Hospital"])
    orch.record_category_recent_names("hospitality", ["Bellagio Resort", "Wynn Las Vegas"])

    # Duplicates inside same category deduped, preserves order
    assert orch._category_recent_names["healthcare"] == ["Sunrise Hospital", "Desert Clinic"]
    assert orch._category_recent_names["hospitality"] == ["Bellagio Resort", "Wynn Las Vegas"]

    stats = orch.get_live_collection_stats()
    cats = stats["categories"]
    assert cats["healthcare"]["recent_names"] == ["Sunrise Hospital", "Desert Clinic"]
    assert cats["hospitality"]["recent_names"] == ["Bellagio Resort", "Wynn Las Vegas"]


def test_periodic_live_raw_export_csv_and_jsonl(temp_db: Database, tmp_path: Path):
    """
    Verifies:
    - Periodic export creates both CSV and JSONL files atomically.
    - Correct columns and row count written.
    - Per-category files generated.
    - Summary metadata recorded.
    """
    from gmaps_scraper.export import export_live_raw_places
    import csv
    import json

    # Insert 3 records across 2 categories
    records = [
        {"raw_id": "r1", "cid": "c1", "name": "Vegas Health", "country_code": "US", "region": "NV", "category": "healthcare", "latitude": 36.1, "longitude": -115.1, "collected_at": "2026-10-29 10:00:00"},
        {"raw_id": "r2", "cid": "c2", "name": "Nevada Clinic", "country_code": "US", "region": "NV", "category": "healthcare", "latitude": 36.2, "longitude": -115.2, "collected_at": "2026-10-29 10:01:00"},
        {"raw_id": "r3", "cid": "c3", "name": "Caesars Palace", "country_code": "US", "region": "NV", "category": "hospitality", "latitude": 36.12, "longitude": -115.17, "collected_at": "2026-10-29 10:02:00"},
    ]
    for r in records:
        temp_db.insert_raw_place(r)

    export_dir = str(tmp_path / "live_exports")
    res = export_live_raw_places(
        db=temp_db,
        country_code="US",
        region_code="NV",
        base_dir=export_dir
    )

    assert res["status"] == "success"
    assert res["records_exported"] == 3
    exp_path = Path(res["export_dir"])
    assert exp_path.exists()

    # Check all_raw_places.csv
    csv_file = exp_path / "all_raw_places.csv"
    assert csv_file.exists()
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 3
        names = {row["name"] for row in reader}
        assert names == {"Vegas Health", "Nevada Clinic", "Caesars Palace"}

    # Check all_raw_places.jsonl
    jsonl_file = exp_path / "all_raw_places.jsonl"
    assert jsonl_file.exists()
    with open(jsonl_file, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 3

    # Check category-specific exports
    assert (exp_path / "healthcare.csv").exists()
    assert (exp_path / "healthcare.jsonl").exists()
    assert (exp_path / "hospitality.csv").exists()
    assert (exp_path / "hospitality.jsonl").exists()

    # Check export_summary.json
    summary_file = exp_path / "export_summary.json"
    assert summary_file.exists()
    with open(summary_file, "r", encoding="utf-8") as f:
        summary_data = json.load(f)
        assert summary_data["total_records"] == 3
        assert summary_data["by_category"]["healthcare"] == 2
        assert summary_data["by_category"]["hospitality"] == 1


def test_atomic_live_export_leaves_sqlite_intact(temp_db: Database, tmp_path: Path):
    """
    Verifies that even if live export encounters a filesystem error (e.g., write permission on destination),
    SQLite database records remain completely unaffected and persistent.
    """
    from unittest.mock import patch
    from gmaps_scraper.export import export_live_raw_places

    record = {
        "raw_id": "safe_r1", "cid": "safe_c1", "name": "Safe Hospital",
        "country_code": "US", "region": "NV", "category": "healthcare",
        "collected_at": "2026-10-29 10:00:00"
    }
    temp_db.insert_raw_place(record)

    # Simulate an OS error during write
    with patch("builtins.open", side_effect=PermissionError("Mock read-only filesystem error")):
        res = export_live_raw_places(
            db=temp_db,
            base_exports_dir=str(tmp_path / "err_export"),
            country_code="US",
            region_code="NV"
        )
        assert res["status"] == "error"

    # SQLite is still completely intact!
    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV")
    assert stats["total_raw"] == 1
    assert stats["raw_saved_today"] == 1


def test_category_raw_saved_from_database_and_dynamic_inmem(temp_db: Database):
    """
    Verifies:
    - Committed SQLite data (tiles new_records, task records_collected, and raw_places count)
      correctly populates category-wise 'raw_saved' and 'records_collected' (never 0 when data exists).
    - In-memory run tracking (run_new) increments the counter dynamically in real-time.
    """
    categories = ["healthcare", "food"]
    temp_db.create_collection_task({
        "task_id": "task_health",
        "country_code": "US",
        "region_code": "NV",
        "category": "healthcare",
        "status": "in_progress",
        "records_collected": 150,
    })
    temp_db.create_geographic_tile({
        "tile_id": "tile_health_1",
        "task_id": "task_health",
        "country_code": "US",
        "region_code": "NV",
        "category": "healthcare",
        "status": "completed",
        "new_records": 120,
        "duplicate_cids": 5,
        "min_lat": 36.0, "max_lat": 36.5, "min_lng": -115.5, "max_lng": -115.0,
        "center_lat": 36.25, "center_lng": -115.25, "span_deg": 0.5, "depth": 0,
    })

    # Also insert a record into raw_places
    temp_db.insert_raw_place({
        "raw_id": "r_food_1",
        "cid": "c_food_1",
        "name": "Tasty Burger",
        "country_code": "US",
        "region": "NV",
        "category": "food",
        "vertical": "food",
        "collected_at": "2026-10-29 11:00:00"
    })

    stats = temp_db.get_collection_progress_stats(country_code="US", region_code="NV", configured_categories=categories)
    health_stat = stats["categories"]["healthcare"]
    food_stat = stats["categories"]["food"]

    # Verify healthcare: max(120, 150) = 150
    assert health_stat["raw_saved"] == 150
    assert health_stat["records_collected"] == 150

    # Verify food: raw_places has 1
    assert food_stat["raw_saved"] == 1
    assert food_stat["records_collected"] == 1

    # Verify live orchestrator overlay
    cfg = PipelineConfig(categories=categories)
    orch = PipelineOrchestrator.get_instance(temp_db, cfg)
    orch._is_running = True
    orch._current_run_data = {
        "current_country": "US",
        "current_region": "NV",
        "run_new_records": 10,
        "run_duplicate_records": 2,
    }
    orch._category_states["healthcare"] = {
        "status": "RUNNING",
        "run_new": 25,
        "run_dups": 3,
    }

    live_stats = orch.get_live_collection_stats()
    l_health = live_stats["categories"]["healthcare"]

    # Base was 150, live run added 25 -> 175
    assert l_health["raw_saved"] == 175
    assert l_health["records_collected"] == 175
    assert l_health["run_new"] == 25
    assert l_health["run_dups"] == 3


@pytest.mark.asyncio
async def test_failed_tasks_block_region_transition(temp_db: Database):
    """
    Verifies that the orchestrator enforces completion rules:
    it will NOT silently advance to the next state if unresolved failed tasks exist in the current state.
    """
    from unittest.mock import AsyncMock, patch

    cfg = PipelineConfig(
        country="US",
        regions=["NV", "UT"],
        categories=["healthcare"],
        resume_checkpoint=True,
        retry_failed=False,
        dry_run=False,
    )
    orch = PipelineOrchestrator.get_instance(temp_db, cfg)

    # Seed NV with a failed task
    temp_db.create_collection_task({
        "task_id": "col_us_nv_healthcare",
        "country_code": "US",
        "region_code": "NV",
        "region_name": "Nevada",
        "category": "healthcare",
        "tile_id": "tile_nv",
        "status": "failed",
        "attempt_count": 2,
        "last_error": "Connection refused",
    })

    # Running orchestrator loop should record failure and block state transition
    plan = orch.preview_plan(cfg)
    run_id = "test_block_run"
    orch._is_running = True
    orch._current_run_data = {"run_id": run_id, "status": "running"}

    await orch._orchestrator_loop(run_id, cfg, plan)

    assert orch._current_run_data["status"] == "failed"
    assert "cannot advance to next state" in orch._current_run_data["error_message"]
    assert "failed and remain unresolved" in orch._current_run_data["error_message"]

