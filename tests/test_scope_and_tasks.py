import tempfile
from pathlib import Path
import pytest
from typer.testing import CliRunner

from gmaps_scraper.scope import (
    load_scope_config,
    get_us_states,
    get_mexico_entities,
    validate_target_country,
    get_regions_for_country,
    get_region,
    ALLOWED_COUNTRIES
)
from gmaps_scraper.db import Database
from gmaps_scraper.cli import app

runner = CliRunner()

def test_scope_exactly_50_us_states():
    """Requirement 1: Exactly 50 US states are configured with official two-letter codes."""
    states = get_us_states()
    assert len(states) == 50, f"Expected exactly 50 US states, got {len(states)}"
    
    # Official list of 50 states
    expected_states = {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"
    }
    assert set(states.keys()) == expected_states
    
    # Assert Washington DC and territories are NOT present
    for excluded in ["DC", "PR", "GU", "VI", "AS", "MP"]:
        assert excluded not in states, f"{excluded} should not be treated as a US state"

def test_scope_exactly_32_mexico_entities():
    """Requirement 2: Exactly 32 Mexican federal entities are configured."""
    entities = get_mexico_entities()
    assert len(entities) == 32, f"Expected exactly 32 Mexican entities, got {len(entities)}"
    
    expected_entities = {
        "AGU", "BCN", "BCS", "CAM", "CHP", "CHH", "CMX", "COA", "COL", "DUR",
        "GUA", "GRO", "HID", "JAL", "MEX", "MIC", "MOR", "NAY", "NLE", "OAX",
        "PUE", "QUE", "ROO", "SLP", "SIN", "SON", "TAB", "TAM", "TLA", "VER",
        "YUC", "ZAC"
    }
    assert set(entities.keys()) == expected_entities

def test_no_country_outside_us_and_mx():
    """Requirement 3: No country outside US and MX is permitted for Pipeline 2."""
    assert set(ALLOWED_COUNTRIES) == {"US", "MX"}
    
    # Valid
    assert validate_target_country("US") == "US"
    assert validate_target_country("us") == "US"
    assert validate_target_country("MX") == "MX"
    assert validate_target_country("mx") == "MX"

    # Disallowed countries must raise ValueError
    for invalid in ["GB", "CA", "FR", "DE", "IN", "BR", "JP", "AU", "ES", "CN", "GLOBAL"]:
        with pytest.raises(ValueError) as excinfo:
            validate_target_country(invalid)
        assert "not supported for Pipeline 2" in str(excinfo.value)
        
        with pytest.raises(ValueError):
            get_regions_for_country(invalid)

def test_region_codes_unique_within_country():
    """Requirement 4: Region codes are strictly unique within each country."""
    us_states = get_us_states()
    assert len(us_states) == len(set(us_states.keys()))
    
    mx_entities = get_mexico_entities()
    assert len(mx_entities) == len(set(mx_entities.keys()))

def test_mexico_city_and_estado_de_mexico_distinguished():
    """Requirement 5: Mexico City (CMX) and Estado de México (MEX) are separate entities."""
    cmx = get_region("MX", "CMX")
    mex = get_region("MX", "MEX")
    
    assert cmx.code == "CMX"
    assert cmx.name == "Ciudad de México"
    
    assert mex.code == "MEX"
    assert mex.name == "Estado de México"
    
    assert cmx.code != mex.code
    assert cmx.name != mex.name

def test_task_progress_tracked_independently_by_region():
    """Requirement 6: Task progress is tracked independently per region; state not complete until all tasks succeed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test_tasks.sqlite")
        db.init_schema()

        # Seed 3 tasks for US state TX, 2 tasks for US state CA
        db.create_collection_task({
            "task_id": "task_tx_1", "country_code": "US", "region_code": "TX",
            "region_name": "Texas", "category": "restaurant", "tile_id": "tile_1"
        })
        db.create_collection_task({
            "task_id": "task_tx_2", "country_code": "US", "region_code": "TX",
            "region_name": "Texas", "category": "restaurant", "tile_id": "tile_2"
        })
        db.create_collection_task({
            "task_id": "task_tx_3", "country_code": "US", "region_code": "TX",
            "region_name": "Texas", "category": "restaurant", "tile_id": "tile_3"
        })

        db.create_collection_task({
            "task_id": "task_ca_1", "country_code": "US", "region_code": "CA",
            "region_name": "California", "category": "restaurant", "tile_id": "tile_ca_1"
        })
        db.create_collection_task({
            "task_id": "task_ca_2", "country_code": "US", "region_code": "CA",
            "region_name": "California", "category": "restaurant", "tile_id": "tile_ca_2"
        })

        # Initial check
        prog_tx = db.get_region_progress("US", "TX")
        assert prog_tx["total_tasks"] == 3
        assert prog_tx["completed_tasks"] == 0
        assert prog_tx["pending_tasks"] == 3
        assert prog_tx["is_complete"] is False

        prog_ca = db.get_region_progress("US", "CA")
        assert prog_ca["total_tasks"] == 2
        assert prog_ca["is_complete"] is False

        # Complete CA tasks
        db.update_collection_task_status("task_ca_1", "completed")
        db.update_collection_task_status("task_ca_2", "completed")

        prog_ca_after = db.get_region_progress("US", "CA")
        assert prog_ca_after["completed_tasks"] == 2
        assert prog_ca_after["is_complete"] is True

        # TX should still NOT be complete (independent tracking)
        prog_tx_after = db.get_region_progress("US", "TX")
        assert prog_tx_after["completed_tasks"] == 0
        assert prog_tx_after["is_complete"] is False

        # Complete 2 of 3 TX tasks: state must STILL NOT be complete
        db.update_collection_task_status("task_tx_1", "completed")
        db.update_collection_task_status("task_tx_2", "completed")
        prog_tx_partial = db.get_region_progress("US", "TX")
        assert prog_tx_partial["completed_tasks"] == 2
        assert prog_tx_partial["is_complete"] is False

        # Fail 3rd task
        db.update_collection_task_status("task_tx_3", "failed", error="Rate limit exceeded")
        prog_tx_failed = db.get_region_progress("US", "TX")
        assert prog_tx_failed["failed_tasks"] == 1
        assert prog_tx_failed["is_complete"] is False

        # Finally complete 3rd task
        db.update_collection_task_status("task_tx_3", "completed")
        prog_tx_final = db.get_region_progress("US", "TX")
        assert prog_tx_final["completed_tasks"] == 3
        assert prog_tx_final["is_complete"] is True

def test_resume_skips_completed_and_retries_failed():
    """Requirement 7: Resume skips completed tasks and retries eligible failed tasks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test_resume.sqlite")
        db.init_schema()

        # Seed tasks for Jalisco (JAL)
        db.create_collection_task({
            "task_id": "jal_01", "country_code": "MX", "region_code": "JAL",
            "region_name": "Jalisco", "category": "hotel", "tile_id": "tile_j1"
        })
        db.create_collection_task({
            "task_id": "jal_02", "country_code": "MX", "region_code": "JAL",
            "region_name": "Jalisco", "category": "hotel", "tile_id": "tile_j2"
        })
        db.create_collection_task({
            "task_id": "jal_03", "country_code": "MX", "region_code": "JAL",
            "region_name": "Jalisco", "category": "hotel", "tile_id": "tile_j3"
        })

        # Mark jal_01 as completed
        db.update_collection_task_status("jal_01", "completed")
        # Mark jal_02 as failed
        db.update_collection_task_status("jal_02", "failed", error="HTTP 503")

        # Resume next incomplete task: must skip jal_01 and return jal_02 or jal_03
        next_task = db.get_next_incomplete_task("MX", "JAL")
        assert next_task is not None
        assert next_task["task_id"] in ["jal_02", "jal_03"]
        assert next_task["task_id"] != "jal_01"

        # Retry failed tasks
        reset_count = db.reset_failed_collection_tasks(country_code="MX", region_code="JAL")
        assert reset_count == 1
        
        # Verify jal_02 is now pending
        tasks = db.get_collection_tasks(country_code="MX", region_code="JAL", status="pending")
        task_ids = [t["task_id"] for t in tasks]
        assert "jal_02" in task_ids
        assert "jal_03" in task_ids
        assert "jal_01" not in task_ids

def test_database_and_cli_backward_compatibility():
    """Requirement 8: Existing database schema, tables, and CLI commands remain compatible."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "compat.sqlite"
        db = Database(db_path)
        db.init_schema()

        # Check all core existing tables exist alongside collection_tasks
        with db.get_connection() as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for expected in ["places", "hours", "attributes", "reviews", "photos", "popular_times", "run_state", "collection_tasks"]:
                assert expected in tables, f"Expected table {expected} not found"

        # Verify CLI commands exist and run without breaking
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ["init", "search", "enrich", "reviews", "photos", "export", "run", "resume", "stats", "serve", "scope"]:
            assert cmd in result.output

        # Verify CLI scope command runs
        res_scope = runner.invoke(app, ["scope", "--summary"])
        assert res_scope.exit_code == 0
        assert "50 States" in res_scope.output
        assert "32 Federal Entities" in res_scope.output
