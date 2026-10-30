import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.db import Database
from gmaps_scraper.orchestrator import (
    PipelineOrchestrator,
    PipelineConfig,
    DEFAULT_CATEGORIES,
)
from gmaps_scraper.scope import get_us_states, get_mexico_entities, get_region
from gmaps_scraper.web.app import create_app


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_pipeline.sqlite"
        db = Database(db_path)
        db.init_schema()
        yield db


@pytest.fixture
def orchestrator(temp_db):
    cfg = ScraperConfig(db_path=str(temp_db.db_path))
    return PipelineOrchestrator(temp_db, cfg)


# -----------------------------------------------------------------------------
# 1. Deterministic Region & Category Ordering Tests
# -----------------------------------------------------------------------------

def test_deterministic_ordering_us_states(orchestrator):
    """USA: exactly 50 states processed in deterministic alphabetical order."""
    scope = orchestrator.resolve_ordered_scope("US")
    assert len(scope) == 50
    codes = [item[1].code for item in scope]
    assert codes == sorted(codes)
    assert codes[0] == "AK" or codes[0] == "AL"
    assert "TX" in codes
    assert "CA" in codes
    assert "WY" in codes
    # Exclude DC or territories
    assert "DC" not in codes
    assert "PR" not in codes


def test_deterministic_ordering_mexico_entities(orchestrator):
    """Mexico: exactly 32 federal entities in deterministic alphabetical order."""
    scope = orchestrator.resolve_ordered_scope("MX")
    assert len(scope) == 32
    codes = [item[1].code for item in scope]
    assert codes == sorted(codes)
    # CDMX vs Estado de Mexico
    assert "CMX" in codes
    assert "MEX" in codes
    cmx_info = next(item[1] for item in scope if item[1].code == "CMX")
    mex_info = next(item[1] for item in scope if item[1].code == "MEX")
    assert cmx_info.name == "Ciudad de México"
    assert mex_info.name == "Estado de México"
    assert cmx_info.code != mex_info.code


def test_deterministic_scope_both_countries(orchestrator):
    """Both: US first (50 states) followed deterministically by Mexico (32 entities). Total = 82."""
    scope = orchestrator.resolve_ordered_scope("both")
    assert len(scope) == 82
    us_part = scope[:50]
    mx_part = scope[50:]
    assert all(p[0] == "US" for p in us_part)
    assert all(p[0] == "MX" for p in mx_part)
    assert [p[1].code for p in us_part] == sorted([p[1].code for p in us_part])
    assert [p[1].code for p in mx_part] == sorted([p[1].code for p in mx_part])


def test_deterministic_scope_selected_subset(orchestrator):
    """When a subset of regions is provided, respect deterministic ordering."""
    scope = orchestrator.resolve_ordered_scope("both", regions=["TX", "CA", "JAL", "CMX"])
    codes = [(p[0], p[1].code) for p in scope]
    assert codes == [("US", "CA"), ("US", "TX"), ("MX", "CMX"), ("MX", "JAL")]


# -----------------------------------------------------------------------------
# 2. Plan Generation Tests
# -----------------------------------------------------------------------------

def test_full_pipeline_plan_generation(orchestrator):
    """Full execution plan generates structured tasks with existing status."""
    cfg = PipelineConfig(
        country="US",
        regions=["WY", "VT"],
        categories=["healthcare", "food"],
        collection_limit=25,
        stages={"collection": True, "discovery": True, "enrichment": True}
    )
    plan = orchestrator.preview_plan(cfg)
    assert plan["counts"]["total_stage1_tasks"] == 4  # 2 regions * 2 categories
    assert len(plan["stage_1_tasks"]) == 4

    tasks = plan["stage_1_tasks"]
    assert tasks[0]["region_code"] == "VT"
    assert tasks[0]["category"] == "healthcare"
    assert tasks[0]["task_id"] == "col_us_vt_healthcare"
    assert tasks[0]["status"] == "pending"

    assert tasks[1]["region_code"] == "VT"
    assert tasks[1]["category"] == "food"

    assert tasks[2]["region_code"] == "WY"
    assert tasks[2]["category"] == "healthcare"

    assert len(plan["stage_2_regions"]) == 2
    assert len(plan["stage_3_regions"]) == 2


# -----------------------------------------------------------------------------
# 3. Stage Dependency Handling
# -----------------------------------------------------------------------------

def test_stage_dependency_enrichment_requires_verified_website(temp_db):
    """Stage 3 enrichment MUST only run on records with verified official websites."""
    # Insert one unverified business and one verified business
    temp_db.upsert_place_stub({
        "cid": "biz_unverified",
        "name": "Unverified Shop",
        "country_code": "US",
        "region": "TX",
        "website_status": "needs_review",
        "website": "http://shop.com"
    })
    temp_db.upsert_place_stub({
        "cid": "biz_verified",
        "name": "Verified Clinic",
        "country_code": "US",
        "region": "TX",
        "website_status": "verified",
        "official_website_url": "https://verifiedclinic.com"
    })

    # Query businesses for enrichment
    eligible = temp_db.get_verified_businesses_for_enrichment(country_code="US", region_code="TX")
    assert len(eligible) == 1
    assert eligible[0]["cid"] == "biz_verified"
    assert eligible[0]["official_website_url"] == "https://verifiedclinic.com"


# -----------------------------------------------------------------------------
# 4. State-to-State & Category Progression Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_to_state_and_category_progression(orchestrator, temp_db):
    """Orchestrator moves category by category, then region by region."""
    cfg = PipelineConfig(
        country="US",
        regions=["WY", "VT"],
        categories=["healthcare", "food"],
        collection_limit=10,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True  # Dry run for unit test speed and purity
    )

    run_id = await orchestrator.start(cfg)
    assert run_id.startswith("pipe_")

    # Wait for orchestrator task to complete
    await orchestrator._worker_task

    status = orchestrator.get_status()
    assert status["status"] == "completed"
    assert status["task_counts"]["completed"] == 4
    assert status["task_counts"]["failed"] == 0

    # Verify all tasks in collection_tasks are completed
    tasks = temp_db.get_collection_tasks(country_code="US")
    assert len(tasks) == 4
    for t in tasks:
        assert t["status"] == "completed"

    # Region progress check
    prog_vt = temp_db.get_region_progress("US", "VT")
    assert prog_vt["is_complete"] is True
    prog_wy = temp_db.get_region_progress("US", "WY")
    assert prog_wy["is_complete"] is True


# -----------------------------------------------------------------------------
# 5. Checkpoint Resume After Interruption
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkpoint_resume_skips_completed(orchestrator, temp_db):
    """Resume skips completed tasks and executes only pending/incomplete tasks."""
    # Pre-seed one completed task and one pending task for WY
    temp_db.create_collection_task({
        "task_id": "col_us_wy_healthcare",
        "country_code": "US",
        "region_code": "WY",
        "region_name": "Wyoming",
        "category": "healthcare",
        "tile_id": "tile_wy",
        "status": "completed",
        "records_collected": 15
    })

    cfg = PipelineConfig(
        country="US",
        regions=["WY"],
        categories=["healthcare", "food"],
        stages={"collection": True, "discovery": False, "enrichment": False},
        resume_checkpoint=True,
        dry_run=True
    )

    run_id = await orchestrator.start(cfg)
    await orchestrator._worker_task

    # Both should now be completed
    tasks = temp_db.get_collection_tasks(country_code="US", region_code="WY")
    assert len(tasks) == 2
    for t in tasks:
        assert t["status"] == "completed"

    # col_us_wy_healthcare was preserved with original records_collected=15
    t_hc = next(t for t in tasks if t["task_id"] == "col_us_wy_healthcare")
    assert t_hc["records_collected"] == 15


# -----------------------------------------------------------------------------
# 6. Idempotency & Conflict Prevention
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prevent_conflicting_simultaneous_runs(orchestrator):
    """Starting a pipeline while another is running raises RuntimeError / 409."""
    cfg = PipelineConfig(
        country="US",
        regions=["WY"],
        categories=["healthcare"],
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True
    )

    run_id = await orchestrator.start(cfg)
    assert orchestrator._is_running is True

    # Attempt to start a second run while active
    with pytest.raises(RuntimeError) as excinfo:
        await orchestrator.start(cfg)
    assert "already active" in str(excinfo.value)

    # Clean up
    await orchestrator._worker_task


# -----------------------------------------------------------------------------
# 7. Pause, Resume, and Cancel
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pause_resume_cancel_controls(orchestrator):
    """Pause, resume, and cancel control execution state cleanly."""
    cfg = PipelineConfig(
        country="US",
        regions=["WY", "VT", "ME"],
        categories=["healthcare", "food", "retail"],
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True
    )

    await orchestrator.start(cfg)
    assert orchestrator._is_running is True

    # Pause
    paused = orchestrator.pause()
    assert paused is True
    status = orchestrator.get_status()
    assert status["status"] == "paused"

    # Resume
    resumed = orchestrator.resume()
    assert resumed is True
    status_res = orchestrator.get_status()
    assert status_res["status"] == "running"

    # Cancel
    cancelled = orchestrator.cancel()
    assert cancelled is True
    await orchestrator._worker_task

    status_after = orchestrator.get_status()
    assert status_after["status"] == "cancelled"
    assert orchestrator._is_running is False


# -----------------------------------------------------------------------------
# 8. Retry Behavior and Terminal Failures
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_behavior_and_terminal_failures(orchestrator, temp_db):
    """Failed tasks increment attempt count and mark terminal failed when attempts exceed limit."""
    cfg = PipelineConfig(
        country="US",
        regions=["WY"],
        categories=["healthcare"],
        stages={"collection": True, "discovery": False, "enrichment": False},
        max_retries=2,
        dry_run=False
    )

    # Mock _execute_collection_search to raise exception
    with patch.object(orchestrator, "_execute_collection_search", side_effect=Exception("HTTP 503 Service Unavailable")):
        await orchestrator.start(cfg)
        await orchestrator._worker_task

    tasks = temp_db.get_collection_tasks(country_code="US", region_code="WY")
    assert len(tasks) == 1
    t = tasks[0]
    assert t["status"] == "failed"
    assert t["attempt_count"] == 2
    assert "503" in (t["last_error"] or "")

    # Reset failed tasks
    reset_cnt = orchestrator.retry_failed()
    assert reset_cnt == 1

    tasks_after = temp_db.get_collection_tasks(country_code="US", region_code="WY")
    assert tasks_after[0]["status"] == "pending"


# -----------------------------------------------------------------------------
# 9. SQLite Persistence of Configuration and Runs
# -----------------------------------------------------------------------------

def test_sqlite_persistence_config_and_runs(temp_db):
    """Configuration and runs persist across database connections."""
    sample_cfg = {
        "country": "both",
        "regions": ["CA", "TX", "JAL"],
        "categories": ["healthcare", "food"],
        "collection_limit": 100,
        "stages": {"collection": True, "discovery": True, "enrichment": False}
    }

    temp_db.save_pipeline_config(sample_cfg)
    loaded_cfg = temp_db.get_pipeline_config()
    assert loaded_cfg == sample_cfg

    temp_db.create_pipeline_run({
        "run_id": "pipe_test_persist",
        "status": "running",
        "config": sample_cfg,
        "current_stage": "stage_1_collection",
        "current_country": "US",
        "current_region": "CA",
        "total_tasks": 10,
        "completed_tasks": 3,
        "businesses_collected": 150,
    })

    run = temp_db.get_pipeline_run("pipe_test_persist")
    assert run is not None
    assert run["run_id"] == "pipe_test_persist"
    assert run["current_region"] == "CA"
    assert run["businesses_collected"] == 150

    # Update run
    temp_db.update_pipeline_run("pipe_test_persist", {"status": "completed", "completed_tasks": 10})
    updated = temp_db.get_pipeline_run("pipe_test_persist")
    assert updated["status"] == "completed"
    assert updated["completed_tasks"] == 10


# -----------------------------------------------------------------------------
# 10. Web API & UI Endpoints Integration Tests
# -----------------------------------------------------------------------------

def test_api_pipeline_routes_integration(temp_db):
    """Tests all /api/pipeline/* endpoints via FastAPI TestClient."""
    cfg = ScraperConfig(db_path=str(temp_db.db_path))
    app = create_app(cfg)
    client = TestClient(app)

    # 1. GET /pipeline page
    page_resp = client.get("/pipeline")
    assert page_resp.status_code == 200
    assert "Pipeline Control &amp; Orchestrator" in page_resp.text
    assert "Start Pipeline" in page_resp.text

    # 2. GET /api/pipeline/config
    get_cfg = client.get("/api/pipeline/config")
    assert get_cfg.status_code == 200
    assert "country" in get_cfg.json()

    # 3. POST /api/pipeline/config
    save_cfg = client.post("/api/pipeline/config", json={
        "country": "US",
        "regions": ["CA"],
        "categories": ["healthcare"],
        "collection_limit": 25,
        "stages": {"collection": True, "discovery": True, "enrichment": True}
    })
    assert save_cfg.status_code == 200
    assert save_cfg.json()["status"] == "saved"

    # 4. POST /api/pipeline/plan
    plan_resp = client.post("/api/pipeline/plan", json={
        "country": "US",
        "regions": ["CA"],
        "categories": ["healthcare"],
        "stages": {"collection": True, "discovery": True, "enrichment": True}
    })
    assert plan_resp.status_code == 200
    plan_data = plan_resp.json()
    assert plan_data["counts"]["total_stage1_tasks"] == 1
    assert plan_data["stage_1_tasks"][0]["region_code"] == "CA"

    # 5. GET /api/pipeline/status
    st_resp = client.get("/api/pipeline/status")
    assert st_resp.status_code == 200
    assert "status" in st_resp.json()
    assert "task_counts" in st_resp.json()

    # 6. POST /api/pipeline/start with dry_run
    start_resp = client.post("/api/pipeline/start", json={
        "country": "US",
        "regions": ["CA"],
        "categories": ["healthcare"],
        "dry_run": True,
        "stages": {"collection": True, "discovery": False, "enrichment": False}
    })
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "started"
    assert "run_id" in start_resp.json()

    # 7. POST /api/pipeline/retry-failed
    retry_resp = client.post("/api/pipeline/retry-failed")
    assert retry_resp.status_code == 200
    assert "reset_count" in retry_resp.json()


def test_api_pipeline_error_handling_and_json_responses(temp_db):
    """
    Regression test ensuring all /api/pipeline/* endpoints return consistent JSON
    responses (never plain text or HTML 500) for invalid inputs, exceptions, and conflicts.
    """
    cfg = ScraperConfig(db_path=str(temp_db.db_path))
    app = create_app(cfg)
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Non-integer collection_limit -> 422 JSON
    resp = client.post("/api/pipeline/start", json={"collection_limit": "not_an_int"})
    assert resp.status_code == 422
    assert "application/json" in resp.headers.get("content-type", "")
    data = resp.json()
    assert "detail" in data
    assert "collection_limit" in data["detail"]

    # 2. Non-list categories -> 422 JSON
    resp = client.post("/api/pipeline/plan", json={"categories": "not_a_list"})
    assert resp.status_code == 422
    assert "application/json" in resp.headers.get("content-type", "")
    assert "categories" in resp.json()["detail"]

    # 3. Non-dict stages -> 422 JSON
    resp = client.post("/api/pipeline/config", json={"stages": "not_a_dict"})
    assert resp.status_code == 422
    assert "application/json" in resp.headers.get("content-type", "")
    assert "stages" in resp.json()["detail"]

    # 4. Unsupported country code -> 400 JSON
    resp = client.post("/api/pipeline/plan", json={"country": "FR"})
    assert resp.status_code == 400
    assert "application/json" in resp.headers.get("content-type", "")
    assert "FR" in resp.json()["detail"]

    # 5. Pause when no run active -> 400 JSON
    resp = client.post("/api/pipeline/pause")
    assert resp.status_code == 400
    assert "application/json" in resp.headers.get("content-type", "")
    assert "No active pipeline run" in resp.json()["detail"]

    # 6. Resume when no run paused -> 400 JSON
    resp = client.post("/api/pipeline/resume")
    assert resp.status_code == 400
    assert "application/json" in resp.headers.get("content-type", "")
    assert "No paused pipeline run" in resp.json()["detail"]

    # 7. Cancel when no run active -> 400 JSON
    resp = client.post("/api/pipeline/cancel")
    assert resp.status_code == 400
    assert "application/json" in resp.headers.get("content-type", "")
    assert "No active pipeline run" in resp.json()["detail"]

    # 8. Unhandled backend exception returns 500 JSON without leaking internals
    with patch.object(PipelineOrchestrator, "get_status", side_effect=Exception("Database disk failure")):
        resp = client.get("/api/pipeline/status")
        assert resp.status_code == 500
        assert "application/json" in resp.headers.get("content-type", "")
        assert resp.json()["detail"] == "An internal server error occurred while processing the request."


def test_pipeline_cancelled_state_transitions(temp_db):
    """
    Verifies that a cancelled pipeline run is not treated as running or completed,
    and accurately reflects cancelled status in get_status and UI models.
    """
    cfg = ScraperConfig(db_path=str(temp_db.db_path))
    app = create_app(cfg)
    client = TestClient(app)

    # Insert a cancelled run simulating previous user cancellation
    temp_db.create_pipeline_run({
        "run_id": "pipe_test_cancelled",
        "status": "cancelled",
        "config": {"country": "US", "regions": ["AZ"], "categories": ["fitness"]},
        "current_stage": "stage_1_collection",
        "current_country": "US",
        "current_region": "AZ",
        "current_category": "fitness",
        "total_tasks": 10,
        "completed_tasks": 3,
        "failed_tasks": 0,
        "businesses_collected": 150,
        "websites_verified": 0,
        "companies_enriched": 0,
        "dry_run": 1,
    })

    # Check status endpoint
    st_resp = client.get("/api/pipeline/status")
    assert st_resp.status_code == 200
    st = st_resp.json()
    assert st["is_running"] is False
    assert st["status"] == "cancelled"
    assert st["current_stage"] == "stage_1_collection"
    assert st["current_country"] == "US"
    assert st["current_region"] == "AZ"

    # Calling resume on a cancelled run should return 400 (only paused can be resumed)
    res_resp = client.post("/api/pipeline/resume")
    assert res_resp.status_code == 400
    assert "No paused pipeline run" in res_resp.json()["detail"]

    # Starting a new run from checkpoint works
    start_resp = client.post("/api/pipeline/start", json={
        "country": "US",
        "regions": ["AZ"],
        "categories": ["fitness"],
        "dry_run": True,
        "resume_checkpoint": True,
    })
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "started"


@pytest.mark.asyncio
async def test_all_seven_pipeline_control_buttons_lifecycle(temp_db):
    """
    Verifies Start, Preview Plan, Status, Pause, Resume, Cancel, and Retry Failed Tasks
    individually and in sequence, confirming that each button calls its intended endpoint
    and correctly updates state without throwing non-JSON or unhandled errors.
    """
    import httpx
    cfg = ScraperConfig(db_path=str(temp_db.db_path))
    app = create_app(cfg)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Preview Plan
        r_plan = await client.post("/api/pipeline/plan", json={
            "country": "US",
            "regions": ["WY", "VT"],
            "categories": ["fitness", "healthcare"],
        })
        assert r_plan.status_code == 200
        plan = r_plan.json()
        assert plan["counts"]["total_stage1_tasks"] == 4

        # 2. Config auto-save
        r_cfg = await client.post("/api/pipeline/config", json={
            "country": "US",
            "regions": ["WY", "VT"],
            "categories": ["fitness", "healthcare"],
            "collection_limit": 25,
        })
        assert r_cfg.status_code == 200
        assert r_cfg.json()["status"] == "saved"

        # 3. Start Pipeline
        r_start = await client.post("/api/pipeline/start", json={
            "country": "US",
            "regions": ["WY", "VT", "ME", "NH"],
            "categories": ["healthcare", "food", "retail"],
            "dry_run": True,
        })
        assert r_start.status_code == 200
        assert r_start.json()["status"] == "started"

        # 4. Status Check while running
        r_st = await client.get("/api/pipeline/status")
        assert r_st.status_code == 200
        st = r_st.json()
        assert st["is_running"] is True
        assert st["status"] == "running"

        # 5. Pause Pipeline
        r_pause = await client.post("/api/pipeline/pause")
        assert r_pause.status_code == 200
        assert r_pause.json()["status"] == "paused"
        st_p = await client.get("/api/pipeline/status")
        assert st_p.json()["status"] == "paused"

        # 6. Resume Pipeline
        r_resume = await client.post("/api/pipeline/resume")
        assert r_resume.status_code == 200
        assert r_resume.json()["status"] == "resumed"
        st_r = await client.get("/api/pipeline/status")
        assert st_r.json()["status"] in ("running", "completed")

        # 7. Cancel Pipeline
        r_cancel = await client.post("/api/pipeline/cancel")
        assert r_cancel.status_code == 200
        assert r_cancel.json()["status"] == "cancelled"
        await asyncio.sleep(0.1)
        st_c = await client.get("/api/pipeline/status")
        assert st_c.json()["status"] == "cancelled"

        # 8. Retry Failed Tasks
        r_retry = await client.post("/api/pipeline/retry-failed")
        assert r_retry.status_code == 200
        assert "reset_count" in r_retry.json()
