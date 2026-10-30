import asyncio
import json
import logging
import re
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field

from .config import ScraperConfig, load_config
from .db import Database
from .http import HttpClient
from .scope import (
    ALLOWED_COUNTRIES,
    get_target_countries,
    get_us_states,
    get_mexico_entities,
    get_regions_for_country,
    get_region,
    validate_target_country,
    RegionInfo,
)
from .models import PlaceStub
from .geo.resolve import resolve_location, get_country_bias
from .geo.boundaries import get_region_bounding_boxes
from .search import build_search_pb, parse_search_rpc_response
from .tiling import recursive_tile_search, generate_region_grid_tiles, GeoTile, sanitize_slug
from .export import export_state_category_data, export_state_complete_package, list_all_state_exports
from .taxonomy import enrich_place_dimensions, load_verticals
from .website.service import WebsiteDiscoveryService
from .website.enrichment_service import WebsiteEnrichmentService
from .storage import RegionalStorageManager

logger = logging.getLogger("gmaps_scraper.orchestrator")

DEFAULT_CATEGORIES = [
    "healthcare",
    "hospitality",
    "food",
    "retail",
    "fitness",
    "beauty",
    "automotive",
    "finance",
    "education",
    "real_estate",
]


class CollectionResult(int):
    total_unique: int
    new_records: int
    duplicates_skipped: int

    def __new__(cls, total_unique: int, new_records: int = 0, duplicates_skipped: int = 0):
        obj = super().__new__(cls, total_unique)
        obj.total_unique = total_unique
        obj.new_records = new_records
        obj.duplicates_skipped = duplicates_skipped
        return obj


class PipelineConfig(BaseModel):
    country: str = "US"  # "US", "MX", or "both"
    regions: Optional[List[str]] = None  # None means all configured regions in selected countries
    categories: List[str] = Field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    category_workers: Union[int, str] = 3  # Parallel workers processing categories concurrently per region ('all' or int)
    all_categories_concurrent: bool = False  # If True, schedule and run ALL configured categories concurrently within active state
    request_concurrency: int = 3  # Maximum shared concurrent HTTP requests across category workers
    collection_limit: Optional[int] = None  # Max places per region/category task (None or 0 = unlimited)
    batch_size: int = 50  # Safe batch limit for website discovery and enrichment
    max_crawl_pages: int = 5  # Max pages crawled per website during enrichment
    stages: Dict[str, bool] = Field(
        default_factory=lambda: {
            "collection": True,
            "discovery": True,
            "enrichment": True,
        }
    )
    collection_only: bool = False  # Dedicated collection-only mode: persists raw records to raw_places, skips deduplication and downstream jobs
    resume_checkpoint: bool = True
    retry_failed: bool = False
    dry_run: bool = False
    max_retries: int = 2
    tile_max_depth: int = 2
    tile_step_deg: float = 0.5
    tile_overlap_pct: float = 10.0
    export_every_n_records: int = 500
    export_interval_seconds: float = 30.0
    live_raw_export_dir: Optional[str] = None

    def resolve_category_workers(self, total_categories: int) -> int:
        """
        Resolves the effective category worker pool size:
        - If all_categories_concurrent is True, or category_workers is 'all', 0, or negative:
          returns total_categories (all configured categories run concurrently).
        - Otherwise returns min(configured_workers, total_categories).
        """
        if self.all_categories_concurrent:
            return max(1, total_categories)
        if isinstance(self.category_workers, str):
            val_str = self.category_workers.strip().lower()
            if val_str in ("all", "0", "max", "-1", "unlimited"):
                return max(1, total_categories)
            try:
                w = int(val_str)
                return max(1, total_categories) if w <= 0 else max(1, min(w, total_categories))
            except ValueError:
                return max(1, total_categories)
        elif isinstance(self.category_workers, (int, float)):
            w = int(self.category_workers)
            return max(1, total_categories) if w <= 0 else max(1, min(w, total_categories))
        return max(1, total_categories)



def sanitize_slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    return s.strip("_") or "all"


class PipelineOrchestrator:
    """
    Dedicated Orchestration Service for State-by-State Business Scraping,
    Website Discovery, and Company Profile Enrichment.
    """

    _instance: Optional["PipelineOrchestrator"] = None

    def __init__(self, db: Database, config: Optional[ScraperConfig] = None):
        self.db = db
        self.config = config or load_config()
        self._lock = asyncio.Lock()
        self._db_lock = asyncio.Lock()
        self._cids_lock = asyncio.Lock()
        self._global_seen_cids: Set[str] = set()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused by default
        self._cancel_requested = False
        self._is_running = False
        self._active_run_id: Optional[str] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._subscribers: List[asyncio.Queue] = []
        self._current_run_data: Dict[str, Any] = {}
        self._recent_errors: List[Dict[str, Any]] = []
        self._category_states: Dict[str, Dict[str, Any]] = {}
        self._category_recent_names: Dict[str, List[str]] = {}
        self._last_raw_export_count: int = 0
        self._last_raw_export_time: float = 0.0
        self._last_raw_export_info: Dict[str, Any] = {}

    @classmethod
    def get_instance(cls, db: Database, config: Optional[ScraperConfig] = None) -> "PipelineOrchestrator":
        if cls._instance is None or cls._instance.db.db_path != db.db_path:
            cls._instance = cls(db, config)
        return cls._instance

    # -------------------------------------------------------------------------
    # Subscription & Event Broadcasting
    # -------------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def emit_event(self, event_type: str, data: Dict[str, Any]):
        message = {"event": event_type, "data": data, "timestamp": datetime.now().isoformat()}
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    # -------------------------------------------------------------------------
    # Plan Generation & Scope Resolution
    # -------------------------------------------------------------------------

    def resolve_ordered_scope(
        self,
        country: str,
        regions: Optional[List[str]] = None
    ) -> List[Tuple[str, RegionInfo]]:
        """
        Returns a deterministic, ordered list of (country_code, RegionInfo).
        - USA: 50 states strictly sorted by 2-letter state code.
        - Mexico: 32 federal entities strictly sorted by entity code.
        - If 'both': USA followed deterministically by Mexico.
        - If a region subset is given, respects that exact deterministic ordering.
        """
        c_upper = country.strip().upper()
        countries_to_process: List[str] = []
        if c_upper in ("BOTH", "ALL"):
            countries_to_process = ["US", "MX"]
        else:
            countries_to_process = [validate_target_country(c_upper)]

        selected_regions_filter = {r.strip().upper() for r in (regions or []) if r.strip()}

        ordered_list: List[Tuple[str, RegionInfo]] = []
        for c in countries_to_process:
            c_regions = get_regions_for_country(c)
            # Deterministic sorting by region code
            for code in sorted(c_regions.keys()):
                if not selected_regions_filter or code in selected_regions_filter:
                    ordered_list.append((c, c_regions[code]))

        return ordered_list

    def preview_plan(self, config: PipelineConfig) -> Dict[str, Any]:
        """
        Generates the complete deterministic execution plan without running it.
        Includes ordered tasks for Stage 1, regions for Stage 2 & 3,
        and current task statuses from SQLite.
        """
        scope_pairs = self.resolve_ordered_scope(config.country, config.regions)
        clean_categories = [c.strip() for c in config.categories if c.strip()]
        if not clean_categories:
            clean_categories = list(DEFAULT_CATEGORIES)

        stage1_tasks: List[Dict[str, Any]] = []
        existing_tasks_map: Dict[str, Dict[str, Any]] = {}

        # Fetch existing tasks from DB to show current status in plan preview
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM collection_tasks").fetchall()
            for r in rows:
                existing_tasks_map[r["task_id"]] = dict(r)

        for country_code, reg in scope_pairs:
            for cat in clean_categories:
                slug = sanitize_slug(cat)
                task_id = f"col_{country_code.lower()}_{reg.code.lower()}_{slug}"
                existing = existing_tasks_map.get(task_id)
                status = existing["status"] if existing else "pending"
                attempts = existing["attempt_count"] if existing else 0
                last_error = existing.get("last_error") if existing else None
                rec_collected = existing.get("records_collected", 0) if existing else 0

                stage1_tasks.append({
                    "task_id": task_id,
                    "country_code": country_code,
                    "region_code": reg.code,
                    "region_name": reg.name,
                    "category": cat,
                    "status": status,
                    "attempt_count": attempts,
                    "last_error": last_error,
                    "records_collected": rec_collected,
                })

        stage2_regions: List[Dict[str, Any]] = []
        stage3_regions: List[Dict[str, Any]] = []

        for country_code, reg in scope_pairs:
            item = {
                "country_code": country_code,
                "region_code": reg.code,
                "region_name": reg.name,
            }
            if config.stages.get("discovery", True):
                stage2_regions.append(item)
            if config.stages.get("enrichment", True):
                stage3_regions.append(item)

        total_stage1 = len(stage1_tasks) if config.stages.get("collection", True) else 0
        completed_stage1 = sum(1 for t in stage1_tasks if t["status"] == "completed") if config.stages.get("collection", True) else 0
        failed_stage1 = sum(1 for t in stage1_tasks if t["status"] == "failed") if config.stages.get("collection", True) else 0

        return {
            "config": config.model_dump(),
            "scope_summary": {
                "countries": sorted(list({p[0] for p in scope_pairs})),
                "regions_count": len(scope_pairs),
                "categories_count": len(clean_categories),
            },
            "enabled_stages": {
                "collection": config.stages.get("collection", True),
                "discovery": config.stages.get("discovery", True),
                "enrichment": config.stages.get("enrichment", True),
            },
            "counts": {
                "total_stage1_tasks": total_stage1,
                "completed_stage1_tasks": completed_stage1,
                "failed_stage1_tasks": failed_stage1,
                "stage2_regions_count": len(stage2_regions),
                "stage3_regions_count": len(stage3_regions),
            },
            "stage_1_tasks": stage1_tasks,
            "stage_2_regions": stage2_regions,
            "stage_3_regions": stage3_regions,
        }

    # -------------------------------------------------------------------------
    # Execution Lifecycle (Start, Pause, Resume, Cancel, Retry)
    # -------------------------------------------------------------------------

    async def start(self, config: PipelineConfig) -> str:
        """
        Starts automated state-by-state pipeline run.
        Enforces singleton concurrency locking to prevent multiple runs.
        """
        async with self._lock:
            if self._is_running and self._worker_task and self._worker_task.done():
                logger.warning("Resetting stale worker state where task finished but running flag remained set.")
                self._is_running = False
                self._active_run_id = None
                self._cancel_requested = False

            if self._is_running:
                if self._cancel_requested:
                    raise RuntimeError(
                        f"Pipeline run {self._active_run_id} is currently cancelling. "
                        f"Please wait a moment for current tasks to finish stopping before starting a new run."
                    )
                raise RuntimeError(
                    f"A pipeline run is already active (run_id: {self._active_run_id}). "
                    f"Please pause or cancel it before starting a new run."
                )

            if getattr(config, "collection_only", False):
                config.stages["discovery"] = False
                config.stages["enrichment"] = False

            self._current_config = config

            # Persist configuration to SQLite
            self.db.save_pipeline_config(config.model_dump())

            run_id = f"pipe_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            self._active_run_id = run_id
            self._is_running = True
            self._cancel_requested = False
            self._pause_event.set()
            self._recent_errors = []

            plan = self.preview_plan(config)
            total_tasks = plan["counts"]["total_stage1_tasks"]

            # Restore accumulated businesses_collected count from the DB so the counter
            # resumes correctly rather than resetting to 0 on every new run.
            existing_businesses_count = self.db.get_stats().get("total_places", 0)

            run_date = datetime.now().strftime("%Y-%m-%d")
            run_data = {
                "run_id": run_id,
                "run_date": run_date,
                "status": "running",
                "config": config.model_dump(),
                "current_stage": "stage_1_collection" if config.stages.get("collection", True) else (
                    "stage_2_discovery" if config.stages.get("discovery", True) else "stage_3_enrichment"
                ),
                "current_country": plan["scope_summary"]["countries"][0] if plan["scope_summary"]["countries"] else None,
                "current_region": None,
                "current_category": None,
                "total_tasks": total_tasks,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "businesses_collected": existing_businesses_count,
                "websites_verified": 0,
                "companies_enriched": 0,
                "dry_run": config.dry_run,
                "error_message": None,
            }
            self._current_run_data = run_data
            self.db.create_pipeline_run(run_data)

            # Reset stale tiles/tasks left in 'running'/'in_progress' from a prior crashed session
            stale_tiles = self.db.reset_stale_running_tiles()
            stale_tasks = self.db.reset_stale_inprogress_tasks()
            if stale_tiles or stale_tasks:
                logger.info(
                    f"Cleaned up stale state from prior session: "
                    f"{stale_tiles} running tiles → pending, {stale_tasks} in_progress tasks → pending"
                )

            # If retry_failed is specified, reset failed tasks upfront
            if config.retry_failed:
                self.retry_failed()

            loop = asyncio.get_running_loop()
            self._worker_task = loop.create_task(self._orchestrator_loop(run_id, config, plan))
            self.emit_event("started", {"run_id": run_id, "status": "running", "config": config.model_dump()})
            return run_id

    def pause(self) -> bool:
        """Pauses active execution after the current unit completes."""
        if not self._is_running or not self._active_run_id:
            return False
        self._pause_event.clear()
        self._current_run_data["status"] = "paused"
        self.db.update_pipeline_run(self._active_run_id, {"status": "paused"})
        self.emit_event("paused", {"run_id": self._active_run_id, "status": "paused"})
        return True

    def resume(self) -> bool:
        """Resumes a paused pipeline run."""
        if not self._is_running or not self._active_run_id:
            # Check if there is a paused run in database to resume
            latest = self.db.get_latest_pipeline_run()
            if latest and latest["status"] == "paused":
                cfg = PipelineConfig(**latest["config"])
                # Launch new run continuing from paused checkpoint
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.start(cfg))
                    return True
                except RuntimeError:
                    pass
            return False

        self._pause_event.set()
        self._current_run_data["status"] = "running"
        self.db.update_pipeline_run(self._active_run_id, {"status": "running"})
        self.emit_event("resumed", {"run_id": self._active_run_id, "status": "running"})
        return True

    def cancel(self) -> bool:
        """Cooperatively cancels the active pipeline run."""
        if not self._is_running or not self._active_run_id:
            return False
        self._cancel_requested = True
        self._pause_event.set()  # Unpause to let worker exit cleanly
        self._current_run_data["status"] = "cancelled"
        self.db.update_pipeline_run(self._active_run_id, {"status": "cancelled"})
        self.emit_event("cancelled", {"run_id": self._active_run_id, "status": "cancelled"})
        return True

    def retry_failed(self) -> int:
        """Resets failed tasks in collection_tasks and geographic_tiles to 'pending'."""
        count = self.db.reset_failed_collection_tasks()
        tile_count = self.db.reset_failed_tiles()
        self.emit_event("retry_failed", {"reset_count": count, "reset_tiles_count": tile_count})
        return count

    def get_status(self) -> Dict[str, Any]:
        """Returns live orchestrator status and metrics."""
        task_counts = self.db.get_pipeline_task_counts()
        regions_progress = self.db.get_all_regions_progress()
        latest_run = self.db.get_latest_pipeline_run()
        tile_counts = self.db.get_pipeline_tile_counts(run_id=self._active_run_id)
        available_exports = list_all_state_exports(self.config.absolute_exports_dir)

        enrich_stats = self.db.get_enrichment_stats()
        disc_stats = self.db.get_website_discovery_stats()

        active_run = self._current_run_data if self._is_running else latest_run

        status_str = "idle"
        if self._is_running:
            if self._cancel_requested:
                status_str = "cancelling"
            elif not self._pause_event.is_set():
                status_str = "paused"
            else:
                status_str = "running"
        elif active_run:
            status_str = active_run.get("status", "idle")

        db_stats = self.db.get_stats()
        total_p = db_stats.get("total_places", 0)
        raw_p = db_stats.get("raw_places_count", 0)
        active_collected = active_run.get("businesses_collected", 0) if active_run else 0
        effective_collected = max(total_p, raw_p, active_collected)

        return {
            "is_running": self._is_running,
            "status": status_str,
            "active_run_id": self._active_run_id,
            "current_stage": active_run.get("current_stage") if active_run else "idle",
            "current_country": active_run.get("current_country") if active_run else None,
            "current_region": active_run.get("current_region") if active_run else None,
            "current_category": active_run.get("current_category") if active_run else None,
            "current_tile": active_run.get("current_tile") if active_run else None,
            "dry_run": bool(active_run.get("dry_run", False)) if active_run else False,
            "task_counts": task_counts,
            "tile_counts": tile_counts,
            "total_businesses_collected": effective_collected,
            "total_places": total_p,
            "raw_places_count": raw_p,
            "session_collected": active_collected,
            "websites_verified": disc_stats.get("verified_count", 0),
            "companies_enriched": enrich_stats.get("completed", 0),
            "company_profiles_count": enrich_stats.get("company_profiles_count", 0),
            "company_leaders_count": enrich_stats.get("company_leaders_count", 0),
            "company_contacts_count": enrich_stats.get("company_contacts_count", 0),
            "recent_errors": self._recent_errors[-10:],
            "regions_progress": regions_progress,
            "latest_run": latest_run,
            "available_exports": available_exports[:30],
        }

    def get_live_collection_stats(self) -> Dict[str, Any]:
        """Consolidates real database counts with active in-memory run tracking across all categories."""
        active_run = self._current_run_data if self._is_running else (self.db.get_latest_pipeline_run() or {})
        cc = active_run.get("current_country")
        reg = active_run.get("current_region")
        if not reg:
            try:
                with self.db.get_connection() as conn:
                    p_row = conn.execute(
                        "SELECT region_code FROM collection_tasks WHERE status IN ('in_progress', 'running', 'pending') LIMIT 1"
                    ).fetchone()
                    if p_row:
                        reg = p_row["region_code"]
            except Exception:
                pass
        cats = active_run.get("categories") or getattr(self.config, "categories", None) or list(DEFAULT_CATEGORIES)

        stats = self.db.get_collection_progress_stats(country_code=cc, region_code=reg, configured_categories=cats)
        stats["country"] = cc or "US"
        stats["region"] = reg or "NV"
        stats["category"] = self._current_run_data.get("current_category", "all")
        stats["active_tile_id"] = self._current_run_data.get("current_tile", "")
        stats["run_new_records"] = self._current_run_data.get("run_new_records", 0)
        stats["run_duplicate_records"] = self._current_run_data.get("run_duplicate_records", 0)
        stats["run_start_time"] = self._current_run_data.get("start_time")

        # Overlay live in-memory category updates
        cat_map = stats.get("categories", {})
        recent_discoveries: Dict[str, List[str]] = {}
        for cat_name, c_info in cat_map.items():
            in_mem = self._category_states.get(cat_name, {})
            if in_mem.get("active_tile_id"):
                c_info["active_tile_id"] = in_mem["active_tile_id"]
            if in_mem.get("status"):
                c_info["status"] = in_mem["status"]
            if in_mem.get("run_new"):
                c_info["run_new"] = in_mem["run_new"]
                base_raw = c_info.get("raw_saved", 0) or 0
                c_info["raw_saved"] = base_raw + in_mem["run_new"]
                c_info["records_collected"] = c_info["raw_saved"]
            if in_mem.get("run_dups"):
                c_info["run_dups"] = in_mem["run_dups"]
            if in_mem.get("results_returned"):
                c_info["results_returned"] = in_mem["results_returned"]

            # Real business names parsed from recent search results
            recent_names = self._category_recent_names.get(cat_name, [])
            c_info["recent_names"] = recent_names
            if recent_names:
                recent_discoveries[cat_name] = recent_names

        stats["recent_discoveries"] = recent_discoveries

        # Live raw export info
        reg_name = active_run.get("current_region_name") or stats["region"]
        base_dir = Path(getattr(self.config, "absolute_exports_dir", "data/exports"))
        today_date = stats.get("today_date", datetime.now().strftime("%Y-%m-%d"))
        from .export import format_region_folder_name
        reg_folder = format_region_folder_name(stats["country"], stats["region"], reg_name)
        def_export_dir = base_dir / "live_raw" / today_date / stats["country"] / reg_folder

        stats["live_raw_export"] = self._last_raw_export_info or {
            "export_path": str(def_export_dir),
            "export_dir": str(def_export_dir),
            "date": today_date,
            "total_exported_rows": stats.get("raw_saved_today", stats.get("total_raw", 0)),
            "last_export_time": self.db.get_last_commit_timestamp() or "N/A"
        }
        return stats

    def record_category_recent_names(self, category: str, names: List[str]):
        """Records up to 10 unique recent real business names discovered for a category."""
        existing = list(self._category_recent_names.get(category, []))
        for name in names:
            n_clean = name.strip() if isinstance(name, str) else ""
            if n_clean and n_clean not in existing:
                existing.append(n_clean)
        self._category_recent_names[category] = existing[-10:]

    def trigger_live_raw_export(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """
        Periodically exports newly committed raw records to dedicated raw collection export directory:
        data/exports/live_raw/{country_code}/{region_name}/
        """
        is_col_only = getattr(self.config, "collection_only", False) or getattr(getattr(self, "_current_config", None), "collection_only", False)
        if not is_col_only:
            return None

        run_new = self._current_run_data.get("run_new_records", 0)
        now = time.time()
        n_threshold = getattr(self.config, "export_every_n_records", 500)
        time_threshold = getattr(self.config, "export_interval_seconds", 30.0)

        should_export = force or (
            (run_new - self._last_raw_export_count >= n_threshold) or
            (now - self._last_raw_export_time >= time_threshold and run_new > self._last_raw_export_count)
        )

        if not should_export:
            return None

        cc = self._current_run_data.get("current_country", "US")
        rc = self._current_run_data.get("current_region", "NV")
        reg_name = self._current_run_data.get("current_region_name", rc)
        cats = self._current_run_data.get("categories", list(DEFAULT_CATEGORIES))
        base_dir = Path(self.config.absolute_exports_dir)

        try:
            from .export import export_live_raw_places
            res = export_live_raw_places(
                db=self.db,
                base_exports_dir=base_dir,
                country_code=cc,
                region_code=rc,
                region_name=reg_name,
                categories=cats
            )
            self._last_raw_export_count = run_new
            self._last_raw_export_time = now
            self._last_raw_export_info = res
            return res
        except Exception as e:
            logger.warning(f"Error during live raw export: {e}")
            return None

    # -------------------------------------------------------------------------
    # Internal Execution Engine
    # -------------------------------------------------------------------------

    async def _check_pause_and_cancel(self):
        """Awaits if paused; raises asyncio.CancelledError if cancelled."""
        if self._cancel_requested:
            raise asyncio.CancelledError("Pipeline run cancelled by user request.")
        await self._pause_event.wait()
        if self._cancel_requested:
            raise asyncio.CancelledError("Pipeline run cancelled by user request.")

    async def _orchestrator_loop(self, run_id: str, config: PipelineConfig, plan: Dict[str, Any]):
        """
        Master sequential execution loop:
        1. Stage 1: Business Collection (State by state, category by category).
        2. Stage 2: Official Website Discovery & Verification.
        3. Stage 3: Company Crawling & Information Enrichment.
        """
        total_collected = self._current_run_data.get("businesses_collected", 0)
        total_verified = 0
        total_enriched = 0

        http_client: Optional[HttpClient] = None

        try:
            req_concurrency = getattr(config, "request_concurrency", getattr(self.config, "request_concurrency", 3))
            http_client = HttpClient(
                user_agent=getattr(self.config, "user_agent", "Mozilla/5.0"),
                accept_language=getattr(self.config, "accept_language", "en-US,en;q=0.9"),
                hl="en",
                gl="us",
                concurrency=req_concurrency,
                per_host_rate=getattr(self.config, "per_host_rate", 2.0),
                max_retries=getattr(self.config, "max_retries", 3),
                backoff_base=getattr(self.config, "backoff_base", 1.5),
                proxies=getattr(self.config, "proxies", None),
            )

            # Pre-seed global seen CIDs for deduplication (only when not in collection_only mode)
            if not getattr(config, "collection_only", False):
                async with self._cids_lock:
                    with self.db.get_connection() as conn:
                        c_rows = conn.execute("SELECT cid FROM places WHERE cid IS NOT NULL").fetchall()
                        self._global_seen_cids = {r["cid"] for r in c_rows if r["cid"]}

            scope_pairs = self.resolve_ordered_scope(config.country, config.regions)
            categories = [c.strip() for c in config.categories if c.strip()] or list(DEFAULT_CATEGORIES)

            # =================================================================
            # STAGE 1: BUSINESS COLLECTION (STATE BY STATE, CONCURRENT CATEGORIES)
            # =================================================================
            if config.stages.get("collection", True):
                self._current_run_data["current_stage"] = "stage_1_collection"
                async with self._db_lock:
                    self.db.update_pipeline_run(run_id, {"current_stage": "stage_1_collection"})
                self.emit_event("stage_started", {"stage": "stage_1_collection", "run_id": run_id})

                total_stage1_tasks = len(scope_pairs) * len(categories)
                tasks_processed = 0
                run_start_time = time.time()
                self._current_run_data["start_time"] = run_start_time
                self._current_run_data["run_new_records"] = 0
                self._current_run_data["run_duplicate_records"] = 0
                total_duplicates = 0
                total_failures = 0
                active_workers_count = 0
                active_categories: Set[str] = set()
                workers_lock = asyncio.Lock()

                for idx, (country_code, reg) in enumerate(scope_pairs):
                    await self._check_pause_and_cancel()
                    self._current_run_data["current_country"] = country_code
                    self._current_run_data["current_region"] = reg.code
                    self._current_run_data["current_region_name"] = reg.name
                    self._current_run_data["categories"] = list(categories)

                    for c in categories:
                        if c not in self._category_states:
                            self._category_states[c] = {
                                "category": c,
                                "status": "PENDING",
                                "active_tile_id": "",
                                "run_new": 0,
                                "run_dups": 0,
                                "results_returned": 0,
                            }

                    async with self._db_lock:
                        self.db.update_pipeline_run(run_id, {
                            "current_country": country_code,
                            "current_region": reg.code
                        })

                    completed_categories_in_region: List[str] = []
                    failed_categories_in_region: List[str] = []
                    skipped_categories_in_region: List[str] = []

                    cat_queue: asyncio.Queue[str] = asyncio.Queue()
                    for cat in categories:
                        cat_queue.put_nowait(cat)

                    configured_workers = getattr(config, "category_workers", getattr(self.config, "category_workers", 3))
                    all_concurrent_flag = getattr(config, "all_categories_concurrent", False)

                    if hasattr(config, "resolve_category_workers"):
                        pool_size = config.resolve_category_workers(len(categories))
                    else:
                        if all_concurrent_flag or str(configured_workers).strip().lower() in ("all", "0", "max", "-1", "unlimited"):
                            pool_size = max(1, len(categories))
                        else:
                            try:
                                w_int = int(configured_workers)
                                pool_size = max(1, len(categories)) if w_int <= 0 else max(1, min(w_int, len(categories)))
                            except (ValueError, TypeError):
                                pool_size = max(1, len(categories))

                    is_all_concurrent = (pool_size >= len(categories))

                    self.emit_event("region_started", {
                        "country": country_code,
                        "region": reg.code,
                        "region_name": reg.name,
                        "region_index": idx + 1,
                        "total_regions": len(scope_pairs),
                        "categories": list(categories),
                        "category_workers": pool_size,
                        "all_categories_concurrent": is_all_concurrent,
                        "request_concurrency": getattr(config, "request_concurrency", 3),
                        "remaining_tasks": max(0, total_stage1_tasks - tasks_processed),
                    })

                    async def category_worker(worker_idx: int):
                        nonlocal tasks_processed, total_collected, total_duplicates, total_failures, active_workers_count
                        while not self._cancel_requested:
                            try:
                                cat = cat_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break

                            async with workers_lock:
                                active_workers_count += 1
                                active_categories.add(cat)
                                self._current_run_data["current_category"] = cat
                                self._category_states.setdefault(cat, {})["status"] = "RUNNING"

                            slug = sanitize_slug(cat)
                            task_id = f"col_{country_code.lower()}_{reg.code.lower()}_{slug}"

                            try:
                                await self._check_pause_and_cancel()

                                # 1. Idempotently create/fetch task
                                async with self._db_lock:
                                    existing_tasks = self.db.get_collection_tasks(
                                        country_code=country_code,
                                        region_code=reg.code
                                    )
                                    task_row = next((t for t in existing_tasks if t["task_id"] == task_id), None)

                                    if not task_row:
                                        self.db.create_collection_task({
                                            "task_id": task_id,
                                            "country_code": country_code,
                                            "region_code": reg.code,
                                            "region_name": reg.name,
                                            "category": cat,
                                            "tile_id": f"tile_{reg.code.lower()}",
                                            "status": "pending",
                                            "run_id": run_id,
                                        })
                                        task_row = {"status": "pending", "attempt_count": 0}

                                # 2. Check if already completed and resume is enabled
                                if config.resume_checkpoint and task_row["status"] == "completed":
                                    logger.info(f"Task {task_id} already completed. Skipping (resume).")
                                    self._category_states.setdefault(cat, {})["status"] = "COMPLETED"
                                    async with workers_lock:
                                        tasks_processed += 1
                                        skipped_categories_in_region.append(cat)
                                    self.emit_event("task_skipped", {
                                        "task_id": task_id,
                                        "country": country_code,
                                        "region": reg.code,
                                        "region_name": reg.name,
                                        "category": cat,
                                        "status": "completed",
                                        "reason": "already_completed",
                                        "remaining_tasks": max(0, total_stage1_tasks - tasks_processed),
                                        "completed_categories": list(completed_categories_in_region),
                                        "active_categories": list(active_categories),
                                    })
                                    continue

                                # 3. Check if failed and attempts exceeded without retry requested
                                if task_row["status"] == "failed" and not config.retry_failed and task_row.get("attempt_count", 0) >= config.max_retries:
                                    logger.info(f"Task {task_id} permanently failed ({task_row.get('attempt_count')} attempts). Skipping.")
                                    async with workers_lock:
                                        tasks_processed += 1
                                        total_failures += 1
                                        failed_categories_in_region.append(cat)
                                    self.emit_event("task_skipped", {
                                        "task_id": task_id,
                                        "country": country_code,
                                        "region": reg.code,
                                        "region_name": reg.name,
                                        "category": cat,
                                        "status": "failed",
                                        "reason": "permanently_failed",
                                        "remaining_tasks": max(0, total_stage1_tasks - tasks_processed),
                                        "completed_categories": list(completed_categories_in_region),
                                        "active_categories": list(active_categories),
                                    })
                                    continue

                                # 4. Execute the collection task with retries
                                success = False
                                collection_res = None
                                last_err_msg = None

                                for attempt in range(1, config.max_retries + 1):
                                    await self._check_pause_and_cancel()
                                    async with self._db_lock:
                                        self.db.update_collection_task_status(task_id, "in_progress")

                                    remaining_tasks = max(0, total_stage1_tasks - tasks_processed - 1)
                                    elapsed_seconds = int(time.time() - run_start_time)

                                    self.emit_event("task_progress", {
                                        "task_id": task_id,
                                        "status": "in_progress",
                                        "country": country_code,
                                        "region": reg.code,
                                        "region_name": reg.name,
                                        "category": cat,
                                        "attempt": attempt,
                                        "active_workers": active_workers_count,
                                        "active_categories": list(active_categories),
                                        "completed_categories": list(completed_categories_in_region),
                                        "failed_categories": list(failed_categories_in_region),
                                        "queued_tasks": cat_queue.qsize(),
                                        "remaining_tasks": remaining_tasks,
                                        "new_unique_records": total_collected,
                                        "duplicates": total_duplicates,
                                        "failures": total_failures,
                                        "elapsed_time": elapsed_seconds,
                                        "elapsed_seconds": elapsed_seconds,
                                        "total_collected": total_collected,
                                    })

                                    try:
                                        collection_res = await self._execute_collection_search(
                                            http_client=http_client,
                                            country_code=country_code,
                                            region=reg,
                                            category=cat,
                                            limit=config.collection_limit,
                                            run_id=run_id,
                                            task_id=task_id,
                                            dry_run=config.dry_run,
                                        )
                                        success = True
                                        break
                                    except asyncio.CancelledError:
                                        raise
                                    except Exception as e:
                                        last_err_msg = str(e)
                                        logger.warning(f"Error on task {task_id} (attempt {attempt}/{config.max_retries}): {last_err_msg}")
                                        self._recent_errors.append({
                                            "task_id": task_id,
                                            "country": country_code,
                                            "region": reg.code,
                                            "category": cat,
                                            "error": last_err_msg,
                                            "timestamp": datetime.now().isoformat()
                                        })
                                        async with self._db_lock:
                                            self.db.update_collection_task_status(task_id, "failed", error=last_err_msg)
                                        if attempt < config.max_retries:
                                            await asyncio.sleep(1.0 * attempt)

                                async with workers_lock:
                                    tasks_processed += 1
                                    remaining_tasks = max(0, total_stage1_tasks - tasks_processed)
                                    elapsed_seconds = int(time.time() - run_start_time)

                                if success and collection_res is not None:
                                    collected_for_task = int(collection_res)
                                    new_records_task = getattr(collection_res, "new_records", collected_for_task)
                                    duplicates_task = getattr(collection_res, "duplicates_skipped", 0)

                                    async with workers_lock:
                                        completed_categories_in_region.append(cat)
                                        total_collected += new_records_task
                                        total_duplicates += duplicates_task
                                        self._current_run_data["businesses_collected"] = total_collected
                                        self._current_run_data["duplicates_skipped"] = total_duplicates
                                        completed_count = self._current_run_data.get("completed_tasks", 0) + 1
                                        self._current_run_data["completed_tasks"] = completed_count

                                    async with self._db_lock:
                                        self.db.update_collection_task_status(
                                            task_id, "completed", records_collected=collected_for_task
                                        )
                                        self.db.update_pipeline_run(run_id, {
                                            "businesses_collected": total_collected,
                                            "completed_tasks": completed_count
                                        })

                                    # Immediate Category-wise export (skip in collection-only mode)
                                    if not getattr(config, "collection_only", False):
                                        run_date = self._current_run_data.get("run_date") or datetime.now().strftime("%Y-%m-%d")
                                        try:
                                            export_state_category_data(
                                                db=self.db,
                                                base_exports_dir=self.config.absolute_exports_dir,
                                                run_id=run_id,
                                                run_date=run_date,
                                                country_code=country_code,
                                                region_code=reg.code,
                                                region_name=reg.name,
                                                category=cat
                                            )
                                        except Exception as ex_err:
                                            logger.warning(f"Error exporting category {cat} data: {ex_err}")

                                    self._category_states.setdefault(cat, {})["status"] = "COMPLETED"
                                    self.trigger_live_raw_export(force=True)

                                    self.emit_event("task_completed", {
                                        "task_id": task_id,
                                        "country": country_code,
                                        "region": reg.code,
                                        "region_name": reg.name,
                                        "category": cat,
                                        "records_collected": collected_for_task,
                                        "new_records": new_records_task,
                                        "duplicates_skipped": duplicates_task,
                                        "active_workers": active_workers_count,
                                        "active_categories": list(active_categories),
                                        "completed_categories": list(completed_categories_in_region),
                                        "queued_tasks": cat_queue.qsize(),
                                        "new_unique_records": total_collected,
                                        "duplicates": total_duplicates,
                                        "failures": total_failures,
                                        "total_unique": total_collected,
                                        "total_collected": total_collected,
                                        "remaining_tasks": remaining_tasks,
                                        "elapsed_time": elapsed_seconds,
                                        "elapsed_seconds": elapsed_seconds,
                                    })
                                else:
                                    async with workers_lock:
                                        total_failures += 1
                                        failed_categories_in_region.append(cat)
                                        self._category_states.setdefault(cat, {})["status"] = "FAILED"
                                        failed_count = self._current_run_data.get("failed_tasks", 0) + 1
                                        self._current_run_data["failed_tasks"] = failed_count

                                    async with self._db_lock:
                                        self.db.update_pipeline_run(run_id, {
                                            "failed_tasks": failed_count
                                        })

                                    self.emit_event("task_failed", {
                                        "task_id": task_id,
                                        "country": country_code,
                                        "region": reg.code,
                                        "region_name": reg.name,
                                        "category": cat,
                                        "error": last_err_msg or "Failed after retries",
                                        "remaining_tasks": remaining_tasks,
                                        "elapsed_seconds": elapsed_seconds,
                                        "completed_categories": list(completed_categories_in_region),
                                        "failed_categories": list(failed_categories_in_region),
                                        "active_categories": list(active_categories),
                                    })
                            finally:
                                async with workers_lock:
                                    active_workers_count -= 1
                                    active_categories.discard(cat)
                                cat_queue.task_done()

                    worker_coros = [category_worker(i) for i in range(pool_size)]
                    await asyncio.gather(*worker_coros)

                    # Region transition rule:
                    # Move to next region only when this region's tasks are complete or explicitly terminal
                    reg_prog = self.db.get_region_progress(country_code, reg.code)
                    logger.info(f"Region {country_code}-{reg.code} batch complete: {reg_prog}")

                    if reg_prog.get("failed_tasks", 0) > 0 and not config.dry_run and not config.retry_failed:
                        err_msg = (
                            f"Region {country_code}-{reg.code} cannot advance to next state: "
                            f"{reg_prog['failed_tasks']} task(s) failed and remain unresolved. "
                            f"Resolve failures or run with --retry-failed."
                        )
                        logger.error(err_msg)
                        self.emit_event("failed", {"run_id": run_id, "error": err_msg, "region": reg.code})
                        raise RuntimeError(err_msg)

                    next_pair = scope_pairs[idx + 1] if idx + 1 < len(scope_pairs) else None
                    next_country_code, next_reg = next_pair if next_pair else (None, None)
                    next_region_name = next_reg.name if next_reg else None

                    self.emit_event("region_completed", {
                        "country": country_code,
                        "region": reg.code,
                        "region_name": reg.name,
                        "region_index": idx + 1,
                        "total_regions": len(scope_pairs),
                        "next_country": next_country_code,
                        "next_region": next_reg.code if next_reg else None,
                        "next_region_name": next_region_name,
                        "completed_categories": list(completed_categories_in_region),
                        "failed_categories": list(failed_categories_in_region),
                        "skipped_categories": list(skipped_categories_in_region),
                        "progress": reg_prog,
                        "remaining_tasks": max(0, total_stage1_tasks - tasks_processed),
                    })

                    # Generate or refresh state summary and complete package (skip in collection-only mode)
                    if not getattr(config, "collection_only", False):
                        run_date = self._current_run_data.get("run_date") or datetime.now().strftime("%Y-%m-%d")
                        try:
                            pkg_res = export_state_complete_package(
                                db=self.db,
                                base_exports_dir=self.config.absolute_exports_dir,
                                run_id=run_id,
                                run_date=run_date,
                                country_code=country_code,
                                region_code=reg.code,
                                region_name=reg.name,
                                categories=categories
                            )
                            self.emit_event("state_exported", {
                                "country": country_code,
                                "region": reg.code,
                                "relative_dir": pkg_res["relative_dir"],
                                "all_businesses_count": pkg_res["all_businesses_count"],
                                "summary": pkg_res["summary"]
                            })
                        except Exception as ex_err:
                            logger.warning(f"Error exporting state package for {country_code}-{reg.code}: {ex_err}")

            # =================================================================
            # STAGE 2: WEBSITE DISCOVERY & VERIFICATION
            # =================================================================
            if not getattr(config, "collection_only", False) and config.stages.get("discovery", True):
                await self._check_pause_and_cancel()
                self._current_run_data["current_stage"] = "stage_2_discovery"
                self._current_run_data["current_category"] = None
                self.db.update_pipeline_run(run_id, {
                    "current_stage": "stage_2_discovery",
                    "current_category": None
                })
                self.emit_event("stage_started", {"stage": "stage_2_discovery", "run_id": run_id})

                discovery_service = WebsiteDiscoveryService(self.db, user_agent=self.config.user_agent)

                for country_code, reg in scope_pairs:
                    await self._check_pause_and_cancel()
                    self._current_run_data["current_country"] = country_code
                    self._current_run_data["current_region"] = reg.code
                    self.db.update_pipeline_run(run_id, {
                        "current_country": country_code,
                        "current_region": reg.code
                    })

                    disc_res = await discovery_service.run_discovery(
                        country_code=country_code,
                        region_code=reg.code,
                        limit=config.batch_size,
                        retry_failed=config.retry_failed,
                        force=False,
                        dry_run=config.dry_run,
                    )
                    v_count = disc_res.get("counts", {}).get("verified", 0)
                    total_verified += v_count
                    self._current_run_data["websites_verified"] = total_verified
                    self.db.update_pipeline_run(run_id, {"websites_verified": total_verified})
                    self.emit_event("stage2_progress", {
                        "country": country_code,
                        "region": reg.code,
                        "verified_in_region": v_count,
                        "total_verified": total_verified,
                    })

            # =================================================================
            # STAGE 3: WEBSITE CRAWLING & COMPANY ENRICHMENT
            # =================================================================
            if not getattr(config, "collection_only", False) and config.stages.get("enrichment", True):
                await self._check_pause_and_cancel()
                self._current_run_data["current_stage"] = "stage_3_enrichment"
                self._current_run_data["current_category"] = None
                self.db.update_pipeline_run(run_id, {
                    "current_stage": "stage_3_enrichment",
                    "current_category": None
                })
                self.emit_event("stage_started", {"stage": "stage_3_enrichment", "run_id": run_id})

                enrichment_service = WebsiteEnrichmentService(
                    self.db,
                    max_pages=config.max_crawl_pages,
                    user_agent=self.config.user_agent
                )

                for country_code, reg in scope_pairs:
                    await self._check_pause_and_cancel()
                    self._current_run_data["current_country"] = country_code
                    self._current_run_data["current_region"] = reg.code
                    self.db.update_pipeline_run(run_id, {
                        "current_country": country_code,
                        "current_region": reg.code
                    })

                    enrich_res = await enrichment_service.run_enrichment(
                        country_code=country_code,
                        region_code=reg.code,
                        limit=config.batch_size,
                        max_pages=config.max_crawl_pages,
                        retry_failed=config.retry_failed,
                        force=False,
                        dry_run=config.dry_run,
                    )
                    c_count = enrich_res.get("counts", {}).get("completed", 0)
                    total_enriched += c_count
                    self._current_run_data["companies_enriched"] = total_enriched
                    self.db.update_pipeline_run(run_id, {"companies_enriched": total_enriched})
                    self.emit_event("stage3_progress", {
                        "country": country_code,
                        "region": reg.code,
                        "completed_in_region": c_count,
                        "total_enriched": total_enriched,
                    })

            # Synchronize affected regional SQLite databases and export packages
            try:
                self._sync_and_export_affected_regions(scope_pairs, run_id, is_failed=False)
            except Exception as se_err:
                logger.error(f"Error during regional sync and exports: {se_err}")

            # Mark completed
            self._current_run_data["status"] = "completed"
            self._current_run_data["current_stage"] = "completed"
            self.db.update_pipeline_run(run_id, {
                "status": "completed",
                "current_stage": "completed"
            })
            self.emit_event("completed", {
                "run_id": run_id,
                "status": "completed",
                "businesses_collected": total_collected,
                "websites_verified": total_verified,
                "companies_enriched": total_enriched,
            })

        except asyncio.CancelledError:
            logger.info(f"Pipeline run {run_id} cancelled.")
            try:
                if 'scope_pairs' in locals() and scope_pairs:
                    self._sync_and_export_affected_regions(scope_pairs, run_id, is_failed=True)
            except Exception as se_err:
                logger.error(f"Error syncing affected regions on cancellation: {se_err}")
            self._current_run_data["status"] = "cancelled"
            self.db.update_pipeline_run(run_id, {"status": "cancelled"})
            self.emit_event("cancelled", {"run_id": run_id, "status": "cancelled"})
        except Exception as e:
            logger.exception(f"Pipeline run {run_id} encountered fatal error: {e}")
            try:
                if 'scope_pairs' in locals() and scope_pairs:
                    self._sync_and_export_affected_regions(scope_pairs, run_id, is_failed=True)
            except Exception as se_err:
                logger.error(f"Error syncing affected regions on failure: {se_err}")
            err_str = str(e)
            self._current_run_data["status"] = "failed"
            self._current_run_data["error_message"] = err_str
            self.db.update_pipeline_run(run_id, {"status": "failed", "error_message": err_str})
            self.emit_event("failed", {"run_id": run_id, "status": "failed", "error": err_str})
        finally:
            if http_client:
                try:
                    await http_client.close()
                except Exception:
                    pass
            self._is_running = False
            self._active_run_id = None
            self._cancel_requested = False

    def _sync_and_export_affected_regions(
        self,
        scope_pairs: List[Tuple[str, RegionInfo]],
        run_id: str,
        is_failed: bool = False
    ) -> Dict[str, Any]:
        """
        Synchronizes affected regional SQLite databases and exports CSV/JSON packages
        from the central SQLite database.
        """
        storage_mgr = RegionalStorageManager(self.db, self.config)
        results: Dict[str, List[Any]] = {"synced": [], "failed": []}
        run_date = self._current_run_data.get("run_date") or datetime.now().strftime("%Y-%m-%d")

        for country_code, reg in scope_pairs:
            try:
                res = storage_mgr.sync_and_export_region(
                    country_code=country_code,
                    region_code=reg.code,
                    region_name=reg.name,
                    run_id=run_id,
                    run_date=run_date
                )
                results["synced"].append(res)
                self.emit_event("region_synced", {
                    "country": country_code,
                    "region": reg.code,
                    "places_synced": res.get("sync", {}).get("places_synced", 0),
                    "db_path": res.get("regional_db_path"),
                    "status": "success",
                    "pipeline_failed": is_failed
                })
            except Exception as e:
                logger.error(f"Failed to sync/export regional database for {country_code}-{reg.code}: {e}")
                results["failed"].append({
                    "country": country_code,
                    "region": reg.code,
                    "error": str(e)
                })
                self.emit_event("region_sync_failed", {
                    "country": country_code,
                    "region": reg.code,
                    "error": str(e),
                    "pipeline_failed": is_failed
                })
        return results

    async def _execute_collection_search(
        self,
        http_client: HttpClient,
        country_code: str,
        region: RegionInfo,
        category: str,
        limit: Optional[int],
        run_id: str,
        task_id: Optional[str] = None,
        dry_run: bool = False,
        collection_only: bool = False
    ) -> CollectionResult:
        """
        Executes Geographic Tile-Based Scraping for a region and category (Section 3).
        Generates/retrieves deterministic tiles, searches Google Maps, deduplicates by CID,
        persists tile metrics, detects saturation, subdivides into quadrants, and handles retries.
        """
        gl, hl = get_country_bias(country_code)
        eff_task_id = task_id or f"col_{country_code.lower()}_{region.code.lower()}_{sanitize_slug(category)}"

        # 1. Retrieve or generate geographic tiles for this task
        existing_tiles = self.db.get_geographic_tiles(
            country_code=country_code,
            region_code=region.code,
            category=category,
            task_id=eff_task_id
        )

        if not existing_tiles:
            step_deg = getattr(self.config, "tile_step_deg", 0.5)
            # Adaptive refinement for small regions
            if region.code in ("CMX", "TLA", "RI", "DE", "DC", "MOR"):
                step_deg = 0.1
            tiles = generate_region_grid_tiles(
                country_code=country_code,
                region_code=region.code,
                category=category,
                task_id=eff_task_id,
                run_id=run_id,
                step_deg=step_deg
            )
            for t in tiles:
                async with self._db_lock:
                    self.db.create_geographic_tile(t.model_dump())
            self.emit_event("tiles_generated", {
                "task_id": eff_task_id,
                "country": country_code,
                "region": region.code,
                "category": category,
                "count": len(tiles)
            })
            existing_tiles = self.db.get_geographic_tiles(
                country_code=country_code,
                region_code=region.code,
                category=category,
                task_id=eff_task_id
            )

        # 2. Build work queue of unfinished tiles
        work_queue: List[GeoTile] = []
        for t_dict in existing_tiles:
            tile_obj = GeoTile(**t_dict)
            if tile_obj.status in ("pending", "running", "retry_pending"):
                work_queue.append(tile_obj)
            elif tile_obj.status == "failed" and self.config.retry_failed:
                tile_obj.status = "pending"
                work_queue.append(tile_obj)

        # 3. Track unique businesses
        seen_cids: Set[str] = set()
        is_col_only = collection_only or getattr(getattr(self, "_current_config", None), "collection_only", False)
        if not is_col_only:
            with self.db.get_connection() as conn:
                rows = conn.execute("""
                    SELECT cid FROM places
                    WHERE country_code = ? AND region = ? AND lower(category) = lower(?)
                """, (country_code.upper(), region.code.upper(), category)).fetchall()
                seen_cids.update(r["cid"] for r in rows if r["cid"])

        max_depth = getattr(self.config, "tile_max_depth", 2)
        overlap_pct = getattr(self.config, "tile_overlap_pct", 10.0)

        task_new_records = 0
        task_duplicates = 0

        # 4. Process tiles — parallel tile worker pool for 3-5x throughput
        #    Number of concurrent tile workers = min(request_concurrency, queue size)
        tile_concurrency = max(1, min(
            getattr(self.config, "request_concurrency", 5),
            len(work_queue) if work_queue else 1
        ))

        # Shared mutable state protected by a local asyncio lock
        seen_cids_lock = asyncio.Lock()
        tile_queue: asyncio.Queue = asyncio.Queue()
        for t in work_queue:
            tile_queue.put_nowait(t)

        # child tiles discovered during processing are pushed here and drained each batch
        child_tile_queue: asyncio.Queue = asyncio.Queue()

        tile_new_total = 0
        tile_dup_total = 0
        totals_lock = asyncio.Lock()

        async def _process_one_tile(tile: "GeoTile"):
            nonlocal tile_new_total, tile_dup_total

            await self._check_pause_and_cancel()
            if limit and limit > 0:
                async with seen_cids_lock:
                    if len(seen_cids) >= limit:
                        return

            self._current_run_data["current_tile"] = tile.tile_id
            self._category_states.setdefault(category, {})["active_tile_id"] = tile.tile_id
            self._category_states[category]["status"] = "RUNNING"
            tile.status = "running"
            tile.started_at = datetime.now().isoformat()
            async with self._db_lock:
                self.db.update_geographic_tile(tile.tile_id, {
                    "status": "running",
                    "started_at": tile.started_at
                })
            self.emit_event("tile_progress", {
                "tile_id": tile.tile_id,
                "task_id": eff_task_id,
                "status": "running",
                "country": country_code,
                "region": region.code,
                "category": category,
                "depth": tile.depth,
                "center_lat": tile.center_lat,
                "center_lng": tile.center_lng,
                "min_lat": tile.min_lat,
                "max_lat": tile.max_lat,
                "min_lng": tile.min_lng,
                "max_lng": tile.max_lng,
                "span_deg": tile.span_deg,
            })

            stubs = []
            tile_success = False
            last_err = None
            http_status = None

            if dry_run:
                await asyncio.sleep(0.01)
                tile_success = True
                base_idx = abs(hash(tile.tile_id)) % 100000
                stubs_count = 20 if tile.depth == 0 and tile_queue.empty() else 5
                for i in range(stubs_count):
                    cid_val = f"dry_{country_code.lower()}_{region.code.lower()}_{base_idx + i}"
                    stubs.append(PlaceStub(
                        cid=cid_val,
                        ftid=f"0x0:0x{base_idx + i:x}",
                        place_id=f"PID_{cid_val}",
                        name=f"{category.title()} Business {base_idx + i}",
                        address=f"{region.name}, {country_code}",
                        latitude=tile.center_lat,
                        longitude=tile.center_lng,
                        category=category,
                        status="pending"
                    ))
            else:
                # HttpClient already handles retries + block rotation internally;
                # we only retry here if the response was successful but empty (could be a soft block)
                for attempt in range(1, self.config.max_retries + 1):
                    await self._check_pause_and_cancel()
                    tile.attempts += 1
                    pb = build_search_pb(tile.center_lat, tile.center_lng, span_meters=tile.span_meters, num_results=20)
                    url = f"https://www.google.com/search?tbm=map&authuser=0&hl={hl}&gl={gl}&pb={pb}&q={urllib.parse.quote(category)}"
                    try:
                        resp = await http_client.get(url)
                        http_status = resp.status_code
                        stubs = parse_search_rpc_response(resp.text)
                        tile_success = True
                        break
                    except Exception as e:
                        last_err = str(e)
                        tile.retry_count += 1
                        if attempt < self.config.max_retries:
                            async with self._db_lock:
                                self.db.update_geographic_tile(tile.tile_id, {
                                    "status": "retry_pending",
                                    "attempts": tile.attempts,
                                    "retry_count": tile.retry_count,
                                    "last_error": last_err
                                })
                            await asyncio.sleep(1.0 * attempt)

            if not tile_success:
                tile.status = "failed"
                tile.last_error = last_err
                async with self._db_lock:
                    self.db.update_geographic_tile(tile.tile_id, {
                        "status": "failed",
                        "attempts": tile.attempts,
                        "retry_count": tile.retry_count,
                        "last_error": last_err,
                        "http_status": http_status
                    })
                self.emit_event("tile_failed", {
                    "tile_id": tile.tile_id,
                    "task_id": eff_task_id,
                    "error": last_err
                })
                return

            # Process places
            new_in_tile = 0
            dup_in_tile = 0
            for stub in stubs:
                if not stub.cid:
                    continue

                raw_id = f"{eff_task_id}:{tile.tile_id}:{stub.cid}"
                raw_dict = stub.model_dump()
                raw_dict["raw_id"] = raw_id
                raw_dict["country_code"] = country_code.upper()
                raw_dict["region"] = region.code.upper()
                raw_dict["search_task_id"] = eff_task_id
                raw_dict["tile_id"] = tile.tile_id
                raw_dict["run_id"] = run_id
                raw_dict["source_data"] = json.dumps(stub.model_dump())

                if is_col_only:
                    async with totals_lock:
                        if limit and limit > 0 and (tile_new_total + new_in_tile) >= limit:
                            break
                    if not dry_run:
                        async with self._db_lock:
                            was_new = self.db.insert_raw_place(raw_dict)
                            if was_new:
                                new_in_tile += 1
                            else:
                                dup_in_tile += 1
                    else:
                        new_in_tile += 1
                else:
                    async with self._cids_lock:
                        async with seen_cids_lock:
                            if (stub.cid in self._global_seen_cids) or (stub.cid in seen_cids):
                                dup_in_tile += 1
                                if not dry_run:
                                    async with self._db_lock:
                                        self.db.insert_raw_place(raw_dict)
                                continue
                            if limit and limit > 0 and len(seen_cids) >= limit:
                                break
                            seen_cids.add(stub.cid)
                            self._global_seen_cids.add(stub.cid)

                    stub_dict = stub.model_dump()
                    stub_dict["country_code"] = country_code.upper()
                    stub_dict["region"] = region.code.upper()
                    stub_dict["first_seen_job_id"] = run_id
                    stub_dict["tile_id"] = tile.tile_id
                    if not dry_run:
                        async with self._db_lock:
                            if self.db.upsert_place_stub(stub_dict):
                                new_in_tile += 1
                            self.db.insert_raw_place(raw_dict)
                        enrich_place_dimensions({"category": stub.category, "address": stub.address})
                    else:
                        new_in_tile += 1

            async with totals_lock:
                tile_new_total += new_in_tile
                tile_dup_total += dup_in_tile

            # Live global count update across run
            async with self._cids_lock:
                if new_in_tile > 0:
                    self._current_run_data["businesses_collected"] = self._current_run_data.get("businesses_collected", 0) + new_in_tile
                    self._current_run_data["run_new_records"] = self._current_run_data.get("run_new_records", 0) + new_in_tile
                if dup_in_tile > 0:
                    self._current_run_data["run_duplicate_records"] = self._current_run_data.get("run_duplicate_records", 0) + dup_in_tile
                self._current_run_data["current_category"] = category
                self._current_run_data["current_tile"] = tile.tile_id

                # Update per-category tracking
                c_st = self._category_states.setdefault(category, {})
                c_st["active_tile_id"] = tile.tile_id
                c_st["status"] = "RUNNING"
                c_st["run_new"] = c_st.get("run_new", 0) + new_in_tile
                c_st["run_dups"] = c_st.get("run_dups", 0) + dup_in_tile
                c_st["results_returned"] = c_st.get("results_returned", 0) + len(stubs)

                # Store live business names from newly processed search results
                parsed_names = [s.name.strip() for s in stubs if s.name and s.name.strip()]
                if parsed_names:
                    rec_list = self._category_recent_names.setdefault(category, [])
                    for nm in parsed_names:
                        if nm not in rec_list:
                            rec_list.append(nm)
                    if len(rec_list) > 10:
                        self._category_recent_names[category] = rec_list[-10:]

            # Trigger live raw export check
            self.trigger_live_raw_export()

            cur_total_db = self._current_run_data.get("businesses_collected", 0)

            # Check saturation (20 results hard cap) → subdivide
            if len(stubs) >= 20 and tile.depth < max_depth and tile.span_deg > 0.02:
                tile.status = "subdivided"
                tile.subdivision_status = "subdivided_into_4"
                children = tile.subdivide(overlap_pct=overlap_pct, max_depth=max_depth)
                async with self._db_lock:
                    for child in children:
                        self.db.create_geographic_tile(child.model_dump())
                    self.db.update_geographic_tile(tile.tile_id, {
                        "status": "subdivided",
                        "results_count": len(stubs),
                        "new_records": new_in_tile,
                        "duplicate_cids": dup_in_tile,
                        "subdivision_status": "subdivided_into_4",
                        "completed_at": datetime.now().isoformat(),
                        "last_checkpoint": datetime.now().isoformat()
                    })
                for child in children:
                    child_tile_queue.put_nowait(child)
                self.emit_event("tile_subdivided", {
                    "tile_id": tile.tile_id,
                    "task_id": eff_task_id,
                    "country": country_code,
                    "region": region.code,
                    "region_name": region.name,
                    "category": category,
                    "children_count": len(children),
                    "depth": tile.depth,
                    "status": "subdivided",
                    "results_count": len(stubs),
                    "new_in_tile": new_in_tile,
                    "new_records": new_in_tile,
                    "duplicate_cids": dup_in_tile,
                    "category_new": tile_new_total,
                    "category_dups": tile_dup_total,
                    "businesses_collected": cur_total_db,
                    "total_unique": cur_total_db,
                })
            else:
                if len(stubs) >= 20:
                    tile.status = "limit_reached"
                else:
                    tile.status = "completed"
                async with self._db_lock:
                    self.db.update_geographic_tile(tile.tile_id, {
                        "status": tile.status,
                        "results_count": len(stubs),
                        "new_records": new_in_tile,
                        "duplicate_cids": dup_in_tile,
                        "completed_at": datetime.now().isoformat(),
                        "last_checkpoint": datetime.now().isoformat()
                    })
                self.emit_event("tile_completed", {
                    "tile_id": tile.tile_id,
                    "task_id": eff_task_id,
                    "country": country_code,
                    "region": region.code,
                    "region_name": region.name,
                    "category": category,
                    "status": tile.status,
                    "results_count": len(stubs),
                    "new_in_tile": new_in_tile,
                    "new_records": new_in_tile,
                    "duplicate_cids": dup_in_tile,
                    "category_new": tile_new_total,
                    "category_dups": tile_dup_total,
                    "businesses_collected": cur_total_db,
                    "total_unique": cur_total_db,
                })

        async def _tile_worker():
            """Pulls tiles from the shared queue and processes them until empty."""
            while True:
                try:
                    tile = tile_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    await _process_one_tile(tile)
                except asyncio.CancelledError:
                    raise
                except Exception as tile_err:
                    logger.warning(f"Tile worker error on {tile.tile_id}: {tile_err}")
                finally:
                    tile_queue.task_done()

        # Run the initial batch in parallel, then drain child subdivided tiles in batches
        while not tile_queue.empty():
            batch_workers = min(tile_concurrency, tile_queue.qsize())
            await asyncio.gather(*[_tile_worker() for _ in range(batch_workers)])

            # Move any child tiles produced during this batch into the main queue
            while not child_tile_queue.empty():
                tile_queue.put_nowait(child_tile_queue.get_nowait())

            # Re-check limit
            if limit and limit > 0 and len(seen_cids) >= limit:
                break

        task_new_records = tile_new_total
        task_duplicates = tile_dup_total

        total_unique_records = task_new_records if is_col_only else len(seen_cids)
        return CollectionResult(total_unique_records, task_new_records, task_duplicates)
