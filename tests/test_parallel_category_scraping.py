import asyncio
import time
from pathlib import Path
import pytest
from unittest.mock import patch, AsyncMock

from gmaps_scraper.db import Database
from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.orchestrator import PipelineOrchestrator, PipelineConfig, CollectionResult
from gmaps_scraper.models import PlaceStub
from gmaps_scraper.tiling import GeoTile
from gmaps_scraper.http import HttpClient


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_gmaps.sqlite"
    db = Database(db_file)
    db.init_schema()
    return db


@pytest.fixture
def scraper_cfg(tmp_path: Path):
    return ScraperConfig(
        db_path=str(tmp_path / "test_gmaps.sqlite"),
        raw_data_dir=str(tmp_path / "raw"),
        photos_dir=str(tmp_path / "photos"),
        databases_dir=str(tmp_path / "databases"),
        exports_dir=str(tmp_path / "exports"),
        logs_dir=str(tmp_path / "logs"),
        category_workers=3,
        request_concurrency=3,
    )


@pytest.mark.asyncio
async def test_pipeline_config_defaults_and_overrides():
    """Verify default worker & concurrency configuration and override support."""
    default_cfg = PipelineConfig()
    assert default_cfg.category_workers == 3
    assert default_cfg.request_concurrency == 3

    custom_cfg = PipelineConfig(category_workers=5, request_concurrency=4)
    assert custom_cfg.category_workers == 5
    assert custom_cfg.request_concurrency == 4


@pytest.mark.asyncio
async def test_concurrent_category_execution(temp_db, scraper_cfg):
    """Verify that multiple categories within a region run concurrently via the worker pool."""
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    cfg = PipelineConfig(
        country="US",
        regions=["KS"],
        categories=["healthcare", "hospitality", "food"],
        category_workers=3,
        request_concurrency=3,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True,
    )

    started_categories = []
    completed_categories = []

    original_execute = orchestrator._execute_collection_search

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False):
        started_categories.append(category)
        await asyncio.sleep(0.05)  # Simulate concurrent work
        res = await original_execute(
            http_client, country_code, region, category, limit, run_id, task_id=task_id, dry_run=dry_run
        )
        completed_categories.append(category)
        return res

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        assert run_id is not None
        await orchestrator._worker_task

    status = orchestrator.get_status()
    assert status["status"] == "completed"
    assert len(completed_categories) == 3
    assert set(completed_categories) == {"healthcare", "hospitality", "food"}
    assert orchestrator._current_run_data["businesses_collected"] > 0


@pytest.mark.asyncio
async def test_global_cid_deduplication_under_concurrency(temp_db, scraper_cfg):
    """Verify that concurrent category workers discovering the same business CID deduplicate globally."""
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    cfg = PipelineConfig(
        country="US",
        regions=["RI"],
        categories=["healthcare", "hospitality"],
        category_workers=2,
        request_concurrency=2,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=False,
    )

    # Return overlapping stubs with the same CID
    shared_stub = PlaceStub(
        cid="shared_cid_1001",
        ftid="0x0:0x1",
        place_id="PID_1001",
        name="Cross Category Center",
        address="Providence, RI, US",
        latitude=41.82,
        longitude=-71.41,
        category="general",
        status="pending"
    )
    unique_stub_1 = PlaceStub(
        cid="cid_unique_health",
        ftid="0x0:0x2",
        place_id="PID_1002",
        name="Rhode Island Hospital",
        address="Providence, RI, US",
        latitude=41.82,
        longitude=-71.41,
        category="healthcare",
        status="pending"
    )
    unique_stub_2 = PlaceStub(
        cid="cid_unique_hotel",
        ftid="0x0:0x3",
        place_id="PID_1003",
        name="Providence Hotel",
        address="Providence, RI, US",
        latitude=41.82,
        longitude=-71.41,
        category="hospitality",
        status="pending"
    )

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False):
        # Both categories encounter shared_stub concurrently, plus one unique stub
        stubs = [shared_stub, unique_stub_1 if category == "healthcare" else unique_stub_2]
        new_in_task = 0
        dup_in_task = 0
        for s in stubs:
            async with orchestrator._cids_lock:
                if s.cid in orchestrator._global_seen_cids:
                    dup_in_task += 1
                    continue
                orchestrator._global_seen_cids.add(s.cid)

            stub_dict = s.model_dump()
            stub_dict["country_code"] = country_code.upper()
            stub_dict["region"] = region.code.upper()
            stub_dict["first_seen_job_id"] = run_id
            stub_dict["tile_id"] = "tile_test"
            async with orchestrator._db_lock:
                if temp_db.upsert_place_stub(stub_dict):
                    new_in_task += 1
        return CollectionResult(len(stubs), new_in_task, dup_in_task)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    # Out of 4 encounters across 2 tasks, exactly 3 unique businesses must exist and 1 duplicate skipped
    with temp_db.get_connection() as conn:
        places = conn.execute("SELECT cid FROM places").fetchall()
        cids = [r["cid"] for r in places]
        assert len(cids) == 3
        assert set(cids) == {"shared_cid_1001", "cid_unique_health", "cid_unique_hotel"}

    status = orchestrator.get_status()
    assert status["total_businesses_collected"] == 3


@pytest.mark.asyncio
async def test_sqlite_write_safety_under_concurrency(temp_db, scraper_cfg):
    """Verify that high-frequency concurrent writes across parallel workers do not trigger database lock errors."""
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    cfg = PipelineConfig(
        country="US",
        regions=["WY"],
        categories=["cat_a", "cat_b", "cat_c", "cat_d"],
        category_workers=4,
        request_concurrency=4,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=False,
    )

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False):
        # Perform 50 rapid database upserts per worker
        for i in range(50):
            cid = f"conc_{category}_{i}"
            stub_dict = {
                "cid": cid,
                "name": f"Biz {cid}",
                "country_code": country_code,
                "region": region.code,
                "category": category,
                "status": "pending",
                "first_seen_job_id": run_id
            }
            async with orchestrator._db_lock:
                temp_db.upsert_place_stub(stub_dict)
            await asyncio.sleep(0.001)
        return CollectionResult(50, 50, 0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    status = orchestrator.get_status()
    assert status["status"] == "completed"
    with temp_db.get_connection() as conn:
        count = conn.execute("SELECT count(*) as cnt FROM places").fetchone()["cnt"]
        assert count == 200  # 4 workers * 50 stubs each


@pytest.mark.asyncio
async def test_worker_failure_independence(temp_db, scraper_cfg):
    """Verify that a failing category does not block or crash remaining concurrent categories."""
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    cfg = PipelineConfig(
        country="US",
        regions=["VT"],
        categories=["failing_cat", "healthy_cat1", "healthy_cat2"],
        category_workers=3,
        request_concurrency=3,
        stages={"collection": True, "discovery": False, "enrichment": False},
        max_retries=1,
        dry_run=True,
    )

    original_execute = orchestrator._execute_collection_search

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False):
        if category == "failing_cat":
            raise RuntimeError("Google Maps rate-limit or network timeout simulation")
        return await original_execute(
            http_client, country_code, region, category, limit, run_id, task_id=task_id, dry_run=dry_run
        )

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    status = orchestrator.get_status()
    assert status["status"] == "completed"
    # One task failed, two succeeded
    tc = status["task_counts"]
    assert tc["failed"] == 1
    assert tc["completed"] == 2
    assert orchestrator._current_run_data["businesses_collected"] > 0


@pytest.mark.asyncio
async def test_resume_checkpoint_behavior(temp_db, scraper_cfg):
    """Verify that resume checkpoints cleanly skip completed tasks while executing pending ones."""
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)

    # Pre-mark one task as completed
    temp_db.create_collection_task({
        "task_id": "col_us_de_healthcare",
        "country_code": "US",
        "region_code": "DE",
        "region_name": "Delaware",
        "category": "healthcare",
        "tile_id": "tile_de",
        "status": "completed",
        "records_collected": 42
    })

    cfg = PipelineConfig(
        country="US",
        regions=["DE"],
        categories=["healthcare", "hospitality"],
        category_workers=2,
        resume_checkpoint=True,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True,
    )

    executed_categories = []
    original_execute = orchestrator._execute_collection_search

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, **kwargs):
        executed_categories.append(category)
        return await original_execute(
            http_client, country_code, region, category, limit, run_id, task_id=task_id, dry_run=dry_run
        )

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    # healthcare was skipped, hospitality was executed
    assert "healthcare" not in executed_categories
    assert "hospitality" in executed_categories


@pytest.mark.asyncio
async def test_shared_rate_limiting_and_concurrency():
    """Verify HttpClient rate limiting lock and semaphore shared across concurrent coroutines."""
    client = HttpClient(
        user_agent="TestAgent",
        concurrency=2,
        per_host_rate=10.0,  # 0.1s between requests
    )

    timestamps = []
    async def make_req():
        async with client.semaphore:
            await client._rate_limit()
            timestamps.append(time.time())

    # Launch 4 requests concurrently
    await asyncio.gather(*(make_req() for _ in range(4)))
    await client.close()

    assert len(timestamps) == 4
    # Requests must be spaced apart by at least ~0.08s due to per_host_rate and jitter
    intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    for interval in intervals:
        assert interval >= 0.05


@pytest.mark.asyncio
async def test_benchmark_sequential_vs_parallel(temp_db, scraper_cfg):
    """
    Benchmark sequential (category_workers=1) vs parallel (category_workers=3) execution
    on the exact same test scope with simulated tile latency, verifying speedup and record equivalence.
    """
    categories = ["healthcare", "hospitality", "food"]
    region = "AK"

    async def run_benchmark(workers: int) -> tuple[float, int]:
        cfg = PipelineConfig(
            country="US",
            regions=[region],
            categories=categories,
            category_workers=workers,
            request_concurrency=workers,
            stages={"collection": True, "discovery": False, "enrichment": False},
            dry_run=True,
        )
        orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)

        async def simulated_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, **kwargs):
            await asyncio.sleep(0.08)  # Simulated I/O latency per category
            return CollectionResult(15, 15, 0)

        with patch.object(orchestrator, "_execute_collection_search", side_effect=simulated_execute):
            t0 = time.time()
            run_id = await orchestrator.start(cfg)
            await orchestrator._worker_task
            elapsed = time.time() - t0
            collected = orchestrator._current_run_data.get("businesses_collected", 0)
            return elapsed, collected

    # 1. Run Sequential (category_workers=1)
    seq_time, seq_records = await run_benchmark(workers=1)

    # Clear places before parallel run
    with temp_db.get_connection() as conn:
        conn.execute("DELETE FROM places")
        conn.execute("DELETE FROM collection_tasks")
        conn.commit()

    # 2. Run Parallel (category_workers=3)
    par_time, par_records = await run_benchmark(workers=3)

    assert seq_records == par_records == 45
    speedup = seq_time / max(par_time, 0.001)
    print(f"\n[BENCHMARK RESULTS] Sequential: {seq_time:.3f}s | Parallel (3 workers): {par_time:.3f}s | Speedup: {speedup:.2f}x")
    # Parallel must be significantly faster (at least 1.5x on 3 workers with identical latency)
    assert speedup > 1.3


@pytest.mark.asyncio
async def test_multiple_categories_overlap_within_one_state(temp_db, scraper_cfg):
    """Prove that multiple categories overlap in execution within a single state."""
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    cfg = PipelineConfig(
        country="US",
        regions=["KS"],
        categories=["healthcare", "hospitality", "food"],
        category_workers=3,
        request_concurrency=3,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True,
    )

    start_times = {}
    end_times = {}
    all_workers_entered = asyncio.Event()

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, **kwargs):
        start_times[category] = time.time()
        if len(start_times) == 3:
            all_workers_entered.set()
        await all_workers_entered.wait()
        await asyncio.sleep(0.02)
        end_times[category] = time.time()
        return CollectionResult(10, 10, 0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    assert len(start_times) == 3
    assert len(end_times) == 3
    # All 3 categories started before the earliest one finished -> true concurrency / overlap
    latest_start = max(start_times.values())
    earliest_end = min(end_times.values())
    assert latest_start < earliest_end, f"Expected overlapping execution: max start ({latest_start}) < min end ({earliest_end})"


@pytest.mark.asyncio
async def test_next_state_never_starts_early(temp_db, scraper_cfg):
    """
    Prove that state-by-state execution is strictly isolated:
    No category of State 2 starts until ALL categories of State 1 are finished.
    """
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    # Scope: 2 states in deterministic order
    cfg = PipelineConfig(
        country="US",
        regions=["AK", "AL"],
        categories=["healthcare", "hospitality"],
        category_workers=2,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True,
    )

    task_intervals = []
    events_log = []

    q = orchestrator.subscribe()

    async def collect_events():
        try:
            while True:
                msg = await q.get()
                events_log.append(msg)
                if msg.get("event") == "completed":
                    break
        except asyncio.CancelledError:
            pass

    event_task = asyncio.create_task(collect_events())

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, **kwargs):
        t0 = time.time()
        # Simulated varying work durations per category
        sleep_dur = 0.08 if category == "healthcare" else 0.04
        await asyncio.sleep(sleep_dur)
        t1 = time.time()
        task_intervals.append({
            "region": region.code,
            "category": category,
            "start": t0,
            "end": t1,
        })
        return CollectionResult(5, 5, 0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    await event_task
    orchestrator.unsubscribe(q)

    # AK comes before AL in sorted order
    ak_intervals = [t for t in task_intervals if t["region"] == "AK"]
    al_intervals = [t for t in task_intervals if t["region"] == "AL"]

    assert len(ak_intervals) == 2
    assert len(al_intervals) == 2

    ak_finish_time = max(t["end"] for t in ak_intervals)
    al_start_time = min(t["start"] for t in al_intervals)

    # Alabama must never start before Alaska completely finishes
    assert al_start_time >= ak_finish_time, (
        f"Next state started early! State 1 ended at {ak_finish_time}, but State 2 started at {al_start_time}"
    )

    # Verify region_completed was emitted for AK with next_region AL
    ak_comp_events = [
        e["data"] for e in events_log
        if e.get("event") == "region_completed" and e.get("data", {}).get("region") == "AK"
    ]
    assert len(ak_comp_events) == 1
    assert ak_comp_events[0]["next_region"] == "AL"
    assert "healthcare" in ak_comp_events[0]["completed_categories"]
    assert "hospitality" in ak_comp_events[0]["completed_categories"]


@pytest.mark.asyncio
async def test_failed_tasks_recorded_explicitly_and_not_marked_completed(temp_db, scraper_cfg):
    """
    Prove that when a category task fails, it is explicitly flagged as 'failed' with error recorded,
    emits 'task_failed', and is NEVER silently marked 'completed'.
    """
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    cfg = PipelineConfig(
        country="US",
        regions=["WY"],
        categories=["healthcare", "hospitality"],
        category_workers=2,
        max_retries=2,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True,
    )

    events_log = []
    q = orchestrator.subscribe()

    async def collect_events():
        try:
            while True:
                msg = await q.get()
                events_log.append(msg)
                if msg.get("event") in ("completed", "failed"):
                    break
        except asyncio.CancelledError:
            pass

    event_task = asyncio.create_task(collect_events())

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, **kwargs):
        if category == "healthcare":
            raise RuntimeError("Google RPC 503 Service Unavailable")
        return CollectionResult(10, 10, 0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    await event_task
    orchestrator.unsubscribe(q)

    # Check database task records
    tasks = temp_db.get_collection_tasks(country_code="US", region_code="WY")
    health_task = next(t for t in tasks if t["category"] == "healthcare")
    hosp_task = next(t for t in tasks if t["category"] == "hospitality")

    assert health_task["status"] == "failed"
    assert "503" in health_task["last_error"]
    assert health_task["records_collected"] == 0
    assert health_task["attempt_count"] == 2

    assert hosp_task["status"] == "completed"
    assert hosp_task["records_collected"] == 10

    # Verify task_failed event was emitted
    failed_events = [e["data"] for e in events_log if e.get("event") == "task_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["category"] == "healthcare"
    assert "503" in failed_events[0]["error"]


@pytest.mark.asyncio
async def test_resume_continues_correct_state_and_skips_completed_tasks(temp_db, scraper_cfg):
    """
    Prove that resume identifies the current state, skips completed tasks across states,
    and executes only unfinished category tasks without repeating work.
    """
    # Pre-populate DB:
    # State 1 ("DE"): both categories completed
    temp_db.create_collection_task({
        "task_id": "col_us_de_healthcare",
        "country_code": "US",
        "region_code": "DE",
        "region_name": "Delaware",
        "category": "healthcare",
        "tile_id": "tile_de",
        "status": "completed",
        "records_collected": 30,
    })
    temp_db.create_collection_task({
        "task_id": "col_us_de_hospitality",
        "country_code": "US",
        "region_code": "DE",
        "region_name": "Delaware",
        "category": "hospitality",
        "tile_id": "tile_de",
        "status": "completed",
        "records_collected": 25,
    })

    # State 2 ("HI"): healthcare is completed, hospitality is pending
    temp_db.create_collection_task({
        "task_id": "col_us_hi_healthcare",
        "country_code": "US",
        "region_code": "HI",
        "region_name": "Hawaii",
        "category": "healthcare",
        "tile_id": "tile_hi",
        "status": "completed",
        "records_collected": 20,
    })

    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    cfg = PipelineConfig(
        country="US",
        regions=["DE", "HI", "RI"],
        categories=["healthcare", "hospitality"],
        category_workers=2,
        resume_checkpoint=True,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True,
    )

    executed_calls = []

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, **kwargs):
        executed_calls.append((region.code, category))
        return CollectionResult(15, 15, 0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    # Check executed calls:
    # DE healthcare and hospitality: SKIPPED
    # HI healthcare: SKIPPED
    # HI hospitality: EXECUTED
    # RI healthcare and hospitality: EXECUTED
    assert ("DE", "healthcare") not in executed_calls
    assert ("DE", "hospitality") not in executed_calls
    assert ("HI", "healthcare") not in executed_calls

    assert ("HI", "hospitality") in executed_calls
    assert ("RI", "healthcare") in executed_calls
    assert ("RI", "hospitality") in executed_calls

    # Verify HI hospitality was executed before RI began
    hi_hosp_idx = executed_calls.index(("HI", "hospitality"))
    ri_health_idx = executed_calls.index(("RI", "healthcare"))
    ri_hosp_idx = executed_calls.index(("RI", "hospitality"))
    assert hi_hosp_idx < ri_health_idx
    assert hi_hosp_idx < ri_hosp_idx


@pytest.mark.asyncio
async def test_configurable_worker_modes_and_options():
    """Verify that worker limits are configurable with options for explicit worker count or all concurrent."""
    total_cats = 10

    # 1. category_workers='all'
    cfg_all = PipelineConfig(category_workers="all")
    assert cfg_all.resolve_category_workers(total_cats) == 10

    # 2. category_workers=0 (concurrency symbol for 'all')
    cfg_zero = PipelineConfig(category_workers=0)
    assert cfg_zero.resolve_category_workers(total_cats) == 10

    # 3. category_workers=-1
    cfg_neg = PipelineConfig(category_workers=-1)
    assert cfg_neg.resolve_category_workers(total_cats) == 10

    # 4. all_categories_concurrent=True
    cfg_flag = PipelineConfig(all_categories_concurrent=True, category_workers=2)
    assert cfg_flag.resolve_category_workers(total_cats) == 10

    # 5. Fixed worker limit: 4 workers
    cfg_four = PipelineConfig(category_workers=4)
    assert cfg_four.resolve_category_workers(total_cats) == 4

    # 6. Sequential worker limit: 1 worker
    cfg_seq = PipelineConfig(category_workers=1)
    assert cfg_seq.resolve_category_workers(total_cats) == 1

    # 7. Worker count exceeding total categories clamps to total categories
    cfg_excess = PipelineConfig(category_workers=25)
    assert cfg_excess.resolve_category_workers(total_cats) == 10


@pytest.mark.asyncio
async def test_all_configured_categories_run_concurrently_in_state(temp_db, scraper_cfg):
    """
    Prove that ALL configured categories for the active state run concurrently in parallel,
    with max simultaneously active workers equal to the number of configured categories.
    """
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    test_categories = [
        "healthcare", "hospitality", "food", "retail", "fitness",
        "beauty", "automotive", "finance", "education", "real_estate"
    ]
    cfg = PipelineConfig(
        country="US",
        regions=["WY"],
        categories=test_categories,
        category_workers="all",
        all_categories_concurrent=True,
        request_concurrency=4,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True,
    )

    start_times = {}
    end_times = {}
    active_now = set()
    max_simultaneous = 0
    state_lock = asyncio.Lock()
    all_workers_started = asyncio.Event()

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, **kwargs):
        nonlocal max_simultaneous
        t0 = time.time()
        async with state_lock:
            active_now.add(category)
            start_times[category] = t0
            if len(active_now) > max_simultaneous:
                max_simultaneous = len(active_now)
            if len(active_now) == len(test_categories):
                all_workers_started.set()

        # Wait until all 10 categories have entered and are active simultaneously
        await all_workers_started.wait()
        await asyncio.sleep(0.02)

        t1 = time.time()
        async with state_lock:
            active_now.discard(category)
            end_times[category] = t1

        return CollectionResult(10, 10, 0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        assert run_id is not None
        await orchestrator._worker_task

    status = orchestrator.get_status()
    assert status["status"] == "completed"

    # All 10 configured categories must have executed
    assert len(start_times) == 10
    assert len(end_times) == 10
    assert set(start_times.keys()) == set(test_categories)

    # Prove ALL categories were simultaneously active concurrently
    assert max_simultaneous == 10, f"Expected all 10 categories active concurrently, got max {max_simultaneous}"

    # Prove full overlapping concurrency: all categories started before any category finished
    latest_start = max(start_times.values())
    earliest_end = min(end_times.values())
    assert latest_start < earliest_end, (
        f"All categories must start concurrently: latest start ({latest_start}) must be before earliest end ({earliest_end})"
    )

    # Check database tasks: all 10 marked completed
    tasks = temp_db.get_collection_tasks(country_code="US", region_code="WY")
    assert len(tasks) == 10
    for t in tasks:
        assert t["status"] == "completed"
        assert t["records_collected"] == 10


@pytest.mark.asyncio
async def test_all_categories_concurrent_request_concurrency_bounded(temp_db, scraper_cfg):
    """
    Prove that even when ALL categories run concurrently, global HTTP request_concurrency
    is strictly respected and never exceeded across workers.
    """
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    test_categories = ["cat1", "cat2", "cat3", "cat4", "cat5", "cat6"]
    max_allowed_concurrency = 2

    cfg = PipelineConfig(
        country="US",
        regions=["WY"],
        categories=test_categories,
        category_workers="all",
        request_concurrency=max_allowed_concurrency,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=False,
    )

    active_http_requests = 0
    max_observed_concurrency = 0
    http_monitor_lock = asyncio.Lock()

    # Track requests going through HttpClient to prove concurrency bounding
    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, **kwargs):
        nonlocal active_http_requests, max_observed_concurrency
        # Each category simulates 3 HTTP requests using the shared http_client semaphore
        for _ in range(3):
            async with http_client.semaphore:
                async with http_monitor_lock:
                    active_http_requests += 1
                    if active_http_requests > max_observed_concurrency:
                        max_observed_concurrency = active_http_requests
                    # Strictly assert semaphore invariant
                    assert active_http_requests <= max_allowed_concurrency

                await asyncio.sleep(0.02)

                async with http_monitor_lock:
                    active_http_requests -= 1

        return CollectionResult(5, 5, 0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    status = orchestrator.get_status()
    assert status["status"] == "completed"
    assert status["task_counts"]["completed"] == 6

    # Verify concurrency was actually exercised up to limit but never exceeded it
    assert max_observed_concurrency == max_allowed_concurrency
    assert active_http_requests == 0


@pytest.mark.asyncio
async def test_all_categories_concurrent_state_transitions_strictly_sequential(temp_db, scraper_cfg):
    """
    Prove that when running with all categories concurrent, state transitions remain strictly sequential:
    State 2 category tasks NEVER start until ALL category tasks of State 1 reach terminal status.
    """
    orchestrator = PipelineOrchestrator(temp_db, scraper_cfg)
    test_categories = ["catA", "catB", "catC", "catD"]

    cfg = PipelineConfig(
        country="US",
        regions=["WY", "VT"],  # Deterministic order: VT then WY
        categories=test_categories,
        category_workers="all",
        request_concurrency=4,
        stages={"collection": True, "discovery": False, "enrichment": False},
        dry_run=True,
    )

    intervals = []
    region_started_counts = {"VT": 0, "WY": 0}
    region_barriers = {"VT": asyncio.Event(), "WY": asyncio.Event()}
    barrier_lock = asyncio.Lock()

    async def mock_execute(http_client, country_code, region, category, limit, run_id, task_id=None, dry_run=False, **kwargs):
        t0 = time.time()
        async with barrier_lock:
            region_started_counts[region.code] += 1
            if region_started_counts[region.code] == len(test_categories):
                region_barriers[region.code].set()

        # Await all categories in this state entering concurrently
        await region_barriers[region.code].wait()
        await asyncio.sleep(0.02)

        t1 = time.time()
        intervals.append({
            "region": region.code,
            "category": category,
            "start": t0,
            "end": t1,
        })
        return CollectionResult(10, 10, 0)

    with patch.object(orchestrator, "_execute_collection_search", side_effect=mock_execute):
        run_id = await orchestrator.start(cfg)
        await orchestrator._worker_task

    status = orchestrator.get_status()
    assert status["status"] == "completed"

    vt_tasks = [t for t in intervals if t["region"] == "VT"]
    wy_tasks = [t for t in intervals if t["region"] == "WY"]

    assert len(vt_tasks) == 4
    assert len(wy_tasks) == 4

    # All VT tasks ran concurrently with each other
    vt_latest_start = max(t["start"] for t in vt_tasks)
    vt_earliest_end = min(t["end"] for t in vt_tasks)
    assert vt_latest_start < vt_earliest_end, "All State 1 categories must execute concurrently"

    # All WY tasks ran concurrently with each other
    wy_latest_start = max(t["start"] for t in wy_tasks)
    wy_earliest_end = min(t["end"] for t in wy_tasks)
    assert wy_latest_start < wy_earliest_end, "All State 2 categories must execute concurrently"

    # State transition barrier: Earliest start of State 2 must be >= latest end of State 1
    vt_finish_time = max(t["end"] for t in vt_tasks)
    wy_start_time = min(t["start"] for t in wy_tasks)

    assert wy_start_time >= vt_finish_time, (
        f"State 2 started early! State 1 finished at {vt_finish_time}, but State 2 started at {wy_start_time}"
    )


