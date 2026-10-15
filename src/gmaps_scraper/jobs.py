import asyncio
import json
import logging
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional, Set

from .config import ScraperConfig, load_config
from .db import Database
from .http import HttpClient
from .geo.resolve import resolve_location, get_country_bias
from .search import build_search_pb, parse_search_rpc_response
from .details import parse_place_details_response
from .reviews import build_boq_reviews_url, parse_reviews_response
from .photos import download_photo_file
from .tiling import recursive_tile_search
from .taxonomy import enrich_place_dimensions

logger = logging.getLogger("gmaps_scraper.jobs")

class JobCancelledError(Exception):
    """Raised when a job is cancelled cooperatively."""
    pass

class JobManager:
    """
    Manages persistent, sequential, cancellable scraping jobs.
    Runs a single sequential background worker loop.
    Broadcasts real-time events to active subscribers.
    """
    _instance: Optional["JobManager"] = None

    def __init__(self, db: Database, config: Optional[ScraperConfig] = None):
        self.db = db
        self.config = config or load_config()
        self._queue: Optional[asyncio.Queue[str]] = None
        self._cancel_flags: Dict[str, bool] = {}
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._active_job_id: Optional[str] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._interrupted_marked = False

    @property
    def queue(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    @classmethod
    def get_instance(cls, db: Database, config: Optional[ScraperConfig] = None) -> "JobManager":
        if cls._instance is None or cls._instance.db.db_path != db.db_path:
            cls._instance = cls(db, config)
        return cls._instance

    def start_worker(self):
        """Starts background worker if not already running."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        if self._queue is None:
            self._queue = asyncio.Queue()

        if not self._interrupted_marked:
            self._interrupted_marked = True
            try:
                self.db.mark_interrupted_jobs()
            except Exception as e:
                logger.warning(f"Failed to mark interrupted jobs on startup: {e}")

        if not self._running or self._worker_task is None or self._worker_task.done():
            self._running = True
            self._worker_task = loop.create_task(self._worker_loop())

    async def stop_worker(self):
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def _worker_loop(self):
        """Sequential single-worker queue loop with database fallback."""
        while self._running:
            try:
                job_id = None
                try:
                    job_id = await asyncio.wait_for(self.queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    with self.db.get_connection() as conn:
                        row = conn.execute("SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1").fetchone()
                        if row:
                            job_id = row[0]
                        else:
                            continue

                if not job_id:
                    continue

                self._active_job_id = job_id
                try:
                    await self._execute_job(job_id)
                except Exception as e:
                    logger.exception(f"Job {job_id} encountered unexpected failure: {e}")
                finally:
                    self._active_job_id = None
                    try:
                        self.queue.task_done()
                    except ValueError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Subscription & Event Broadcasting
    # ------------------------------------------------------------------
    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        if job_id not in self._subscribers:
            self._subscribers[job_id] = []
        self._subscribers[job_id].append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue):
        if job_id in self._subscribers:
            try:
                self._subscribers[job_id].remove(q)
            except ValueError:
                pass
            if not self._subscribers[job_id]:
                del self._subscribers[job_id]

    async def broadcast_event(self, job_id: str, event_type: str, data: Dict[str, Any]):
        subscribers = list(self._subscribers.get(job_id, []))
        message = {"event": event_type, "data": data}
        for q in subscribers:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def emit_event(
        self,
        job_id: str,
        stage: str,
        message: str,
        event_type: str = "progress",
        level: str = "info",
        payload: Optional[Dict[str, Any]] = None
    ):
        """Persists event to SQLite and broadcasts to SSE subscribers."""
        self.db.add_job_event(job_id, stage, message, level=level, payload=payload)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast_event(job_id, event_type, payload or {"message": message, "stage": stage}))
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Job CRUD & Lifecycle
    # ------------------------------------------------------------------
    def create_job(
        self,
        query: str,
        location: str,
        options: Optional[Dict[str, Any]] = None,
        job_id: Optional[str] = None
    ) -> str:
        jid = job_id or str(uuid.uuid4())[:8]
        opts = {
            "max_places": 200,
            "max_reviews_per_place": 50,
            "download_photos": False,
            "enrich_details": True,
            "fetch_reviews": True,
            "max_depth": 3,
            "span_override_deg": None,
            "force_refresh": False,
            "timeout_seconds": 1800,
            **(options or {})
        }

        job_row = {
            "id": jid,
            "status": "queued",
            "query": query.strip(),
            "location_input": location.strip(),
            "resolved_label": None,
            "lat": None,
            "lng": None,
            "span_deg": None,
            "country_code": None,
            "options": opts,
            "stage": "queued",
            "progress": {
                "stage": "queued",
                "done": 0,
                "total": 0,
                "places_found": 0,
                "reviews_found": 0,
                "photos_found": 0,
                "new_places": 0,
                "already_known_places": 0,
                "eta_s": 0
            }
        }
        self.db.create_job(job_row)
        self.emit_event(jid, "queued", f"Job {jid} queued for execution", event_type="stage")
        self._cancel_flags[jid] = False

        self.start_worker()
        self.queue.put_nowait(jid)
        return jid

    def cancel_job(self, job_id: str) -> bool:
        job = self.db.get_job(job_id)
        if not job or job["status"] in ("done", "cancelled", "failed"):
            return False

        self._cancel_flags[job_id] = True
        if job["status"] == "queued":
            self.db.update_job(job_id, {
                "status": "cancelled",
                "stage": "cancelled",
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            self.emit_event(
                job_id, "cancelled", "Job cancelled while queued",
                event_type="done",
                payload={"job_id": job_id, "status": "cancelled", "places_found": 0, "reviews_found": 0, "photos_found": 0}
            )
        return True

    def retry_job(self, job_id: str) -> str:
        job = self.db.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found.")

        # Reset status and requeue
        self.db.update_job(job_id, {
            "status": "queued",
            "stage": "queued",
            "error": None,
            "started_at": None,
            "finished_at": None
        })
        self._cancel_flags[job_id] = False
        self.emit_event(job_id, "queued", f"Job {job_id} requeued for execution", event_type="stage")
        self.start_worker()
        self.queue.put_nowait(job_id)
        return job_id

    # ------------------------------------------------------------------
    # Job Pipeline Execution
    # ------------------------------------------------------------------
    async def _execute_job(self, job_id: str):
        job = self.db.get_job(job_id)
        if not job or job["status"] == "cancelled":
            return

        start_time = time.time()
        options = job.get("options", {})
        timeout_seconds = options.get("timeout_seconds", 1800)
        max_places = options.get("max_places", 200)
        max_reviews = options.get("max_reviews_per_place", 50)
        do_photos = options.get("download_photos", False)
        do_enrich = options.get("enrich_details", True)
        do_reviews = options.get("fetch_reviews", True)
        max_depth = options.get("max_depth", 3)
        span_override = options.get("span_override_deg")
        force_refresh = options.get("force_refresh", False)

        def check_cancelled():
            if self._cancel_flags.get(job_id, False):
                raise JobCancelledError("Job was cancelled cooperatively by user request.")
            if time.time() - start_time > timeout_seconds:
                raise TimeoutError(f"Job exceeded timeout limit of {timeout_seconds}s.")

        self.db.update_job(job_id, {
            "status": "resolving",
            "stage": "resolving",
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        self.emit_event(job_id, "resolving", f"Resolving location '{job['location_input']}'...", event_type="stage")

        http_client = HttpClient(
            user_agent=self.config.user_agent,
            accept_language=self.config.accept_language,
            hl="en",
            gl="us",
            concurrency=self.config.concurrency,
            per_host_rate=self.config.per_host_rate,
            max_retries=self.config.max_retries,
            backoff_base=self.config.backoff_base,
            proxies=self.config.proxies
        )

        places_found = 0
        new_places = 0
        already_known = 0
        reviews_found = 0
        photos_found = 0

        try:
            # Stage 1: Location Resolution
            check_cancelled()
            res = await resolve_location(
                job["location_input"],
                db=self.db,
                http_client=http_client,
                force_refresh=force_refresh
            )
            lat = res["lat"]
            lng = res["lng"]
            span_deg = float(span_override) if span_override is not None else res["span_deg"]
            country_code = res.get("country_code")

            gl, hl = get_country_bias(country_code)
            http_client.gl = gl
            http_client.hl = hl

            self.db.update_job(job_id, {
                "resolved_label": res["label"],
                "lat": lat,
                "lng": lng,
                "span_deg": span_deg,
                "country_code": country_code,
            })
            self.emit_event(
                job_id, "resolving",
                f"Resolved to {res['label']} ({lat:.4f}, {lng:.4f}, span={span_deg}deg)",
                event_type="progress",
                payload={
                    "stage": "resolving", "resolved": res,
                    "places_found": 0, "reviews_found": 0, "photos_found": 0
                }
            )

            # Stage 2: Discovery via Spatial Tiling
            check_cancelled()
            self.db.update_job(job_id, {"status": "discovering", "stage": "discovering"})
            self.emit_event(job_id, "discovering", f"Starting spatial tile search for '{job['query']}'...", event_type="stage")

            discovered_stubs: List[Any] = []
            seen_cids: Set[str] = set()

            async def fetch_tile(t_lat: float, t_lng: float, span_meters: int):
                check_cancelled()
                pb = build_search_pb(t_lat, t_lng, span_meters=span_meters, num_results=20)
                url = f"https://www.google.com/search?tbm=map&authuser=0&hl={hl}&gl={gl}&pb={pb}&q={urllib.parse.quote(job['query'])}"
                resp = await http_client.get(url)
                stubs = parse_search_rpc_response(resp.text)

                nonlocal new_places, already_known, places_found
                for stub in stubs:
                    check_cancelled()
                    if stub.cid in seen_cids:
                        continue
                    if len(seen_cids) >= max_places:
                        break
                    seen_cids.add(stub.cid)
                    discovered_stubs.append(stub)

                    existing = self.db.get_place(stub.cid)
                    is_new = existing is None
                    if is_new:
                        new_places += 1
                    else:
                        already_known += 1
                    places_found += 1

                    stub_dict = stub.model_dump()
                    stub_dict["first_seen_job_id"] = job_id
                    self.db.upsert_place_stub(stub_dict)

                    enriched = enrich_place_dimensions({"category": stub.category, "address": stub.address})
                    place_event_payload = {
                        "cid": stub.cid,
                        "name": stub.name,
                        "address": stub.address,
                        "category": stub.category,
                        "vertical": enriched.get("vertical"),
                        "rating": stub.rating,
                        "review_count": stub.review_count,
                        "latitude": stub.latitude,
                        "longitude": stub.longitude,
                        "phone": stub.phone,
                        "website": stub.website,
                        "is_new": is_new
                    }
                    self.emit_event(
                        job_id, "discovering",
                        f"Found place: {stub.name}",
                        event_type="place",
                        payload=place_event_payload
                    )

                return stubs

            def on_tile_progress(tiles_done: int, total_unique: int):
                check_cancelled()
                self.emit_event(
                    job_id, "discovering",
                    f"Processed {tiles_done} tiles | {places_found} places found",
                    event_type="progress",
                    payload={
                        "stage": "discovering",
                        "done": tiles_done,
                        "total": res.get("estimated_tiles", 16),
                        "places_found": places_found,
                        "reviews_found": reviews_found,
                        "photos_found": photos_found,
                        "new_places": new_places,
                        "already_known_places": already_known,
                        "eta_s": max(0, int((res.get("estimated_tiles", 16) - tiles_done) * 0.8))
                    }
                )

            raw_stubs = await recursive_tile_search(
                fetch_tile,
                center_lat=lat,
                center_lng=lng,
                initial_span_deg=span_deg,
                max_depth=max_depth,
                overlap_pct=self.config.tile_overlap_pct,
                on_tile_complete=on_tile_progress,
                max_results=max_places
            )

            self.db.update_job(job_id, {
                "places_found": places_found,
                "progress": {
                    "stage": "discovering", "done": len(discovered_stubs), "total": len(discovered_stubs),
                    "places_found": places_found, "new_places": new_places, "already_known_places": already_known,
                    "reviews_found": reviews_found, "photos_found": photos_found
                }
            })

            # Stage 3: Details Enrichment
            if do_enrich and discovered_stubs:
                check_cancelled()
                self.db.update_job(job_id, {"status": "enriching", "stage": "enriching"})
                self.emit_event(job_id, "enriching", f"Enriching details for {len(discovered_stubs)} places...", event_type="stage")

                for idx, stub in enumerate(discovered_stubs, 1):
                    check_cancelled()
                    # Skip if already enriched unless force_refresh
                    existing = self.db.get_place(stub.cid)
                    if existing and existing["status"] in ("details_done", "reviews_done", "complete") and not force_refresh:
                        continue

                    s_lat = stub.latitude or lat
                    s_lng = stub.longitude or lng
                    pb = f"!4m8!1m3!1d500!2d{s_lng}!3d{s_lat}!3m2!1i1024!2i768!4f13.1!7i20!10b1"
                    url = f"https://www.google.com/search?tbm=map&authuser=0&hl={hl}&gl={gl}&pb={pb}&q={urllib.parse.quote(stub.name)}"

                    try:
                        resp = await http_client.get(url)
                        details = parse_place_details_response(resp.text, stub.cid)
                        if details:
                            self.db.update_place_details(stub.cid, details, status="details_done")
                    except Exception as e:
                        logger.warning(f"Failed details for {stub.name}: {e}")

                    if idx % 5 == 0 or idx == len(discovered_stubs):
                        self.emit_event(
                            job_id, "enriching",
                            f"Enriched {idx}/{len(discovered_stubs)} places",
                            event_type="progress",
                            payload={
                                "stage": "enriching", "done": idx, "total": len(discovered_stubs),
                                "places_found": places_found, "reviews_found": reviews_found, "photos_found": photos_found,
                                "eta_s": max(0, int((len(discovered_stubs) - idx) * 0.5))
                            }
                        )

            # Stage 4: Reviews Extraction
            if do_reviews and discovered_stubs:
                check_cancelled()
                self.db.update_job(job_id, {"status": "reviews", "stage": "reviews"})
                self.emit_event(job_id, "reviews", f"Fetching reviews for places...", event_type="stage")

                for idx, stub in enumerate(discovered_stubs, 1):
                    check_cancelled()
                    existing = self.db.get_place(stub.cid)
                    if existing and existing["status"] in ("reviews_done", "complete") and not force_refresh:
                        continue

                    ftid = stub.ftid or (existing["ftid"] if existing else None)
                    if not ftid:
                        continue

                    token = None
                    place_revs = 0
                    while place_revs < max_reviews:
                        check_cancelled()
                        rev_url = build_boq_reviews_url(ftid, page_size=10, sort_code=1, pagination_token=token)
                        try:
                            resp = await http_client.get(rev_url)
                            parsed_revs, next_token = parse_reviews_response(resp.text, stub.cid)
                            if not parsed_revs:
                                break
                            for r in parsed_revs:
                                self.db.insert_review(r)
                                place_revs += 1
                                reviews_found += 1
                            if not next_token or next_token == token:
                                break
                            token = next_token
                        except Exception:
                            break

                    with self.db.get_connection() as conn:
                        conn.execute("UPDATE places SET status = 'reviews_done' WHERE cid = ?", (stub.cid,))
                        conn.commit()

                    if idx % 5 == 0 or idx == len(discovered_stubs):
                        self.emit_event(
                            job_id, "reviews",
                            f"Fetched reviews for {idx}/{len(discovered_stubs)} places ({reviews_found} reviews)",
                            event_type="progress",
                            payload={
                                "stage": "reviews", "done": idx, "total": len(discovered_stubs),
                                "places_found": places_found, "reviews_found": reviews_found, "photos_found": photos_found,
                                "eta_s": max(0, int((len(discovered_stubs) - idx) * 0.6))
                            }
                        )

            # Stage 5: Photo Downloads
            if do_photos:
                check_cancelled()
                self.db.update_job(job_id, {"status": "photos", "stage": "photos"})
                self.emit_event(job_id, "photos", f"Downloading photos...", event_type="stage")

                with self.db.get_connection() as conn:
                    rows = conn.execute("""
                        SELECT photos.id, photos.cid, photos.photo_url
                        FROM photos
                        JOIN places ON photos.cid = places.cid
                        WHERE places.first_seen_job_id = ? AND photos.local_path IS NULL
                        LIMIT 100
                    """, (job_id,)).fetchall()
                    photos_to_dl = [dict(r) for r in rows]

                for p_idx, ph in enumerate(photos_to_dl, 1):
                    check_cancelled()
                    try:
                        p_path = await download_photo_file(
                            ph["photo_url"], ph["cid"], self.config.absolute_photos_dir, http_client
                        )
                        if p_path:
                            with self.db.get_connection() as conn:
                                conn.execute("UPDATE photos SET local_path = ? WHERE id = ?", (p_path, ph["id"]))
                                conn.commit()
                            photos_found += 1
                    except Exception:
                        pass

                    if p_idx % 5 == 0 or p_idx == len(photos_to_dl):
                        self.emit_event(
                            job_id, "photos",
                            f"Downloaded {p_idx}/{len(photos_to_dl)} photos",
                            event_type="progress",
                            payload={
                                "stage": "photos", "done": p_idx, "total": len(photos_to_dl),
                                "places_found": places_found, "reviews_found": reviews_found, "photos_found": photos_found,
                                "eta_s": max(0, int((len(photos_to_dl) - p_idx) * 0.4))
                            }
                        )

            # Completed cleanly
            self.db.update_job(job_id, {
                "status": "done",
                "stage": "done",
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "places_found": places_found,
                "reviews_found": reviews_found,
                "photos_found": photos_found,
                "progress": {
                    "stage": "done", "done": places_found, "total": places_found,
                    "places_found": places_found, "new_places": new_places, "already_known_places": already_known,
                    "reviews_found": reviews_found, "photos_found": photos_found, "eta_s": 0
                }
            })
            self.emit_event(
                job_id, "done",
                f"Job finished: {places_found} places ({new_places} new, {already_known} existing), {reviews_found} reviews, {photos_found} photos.",
                event_type="done",
                payload={
                    "job_id": job_id, "status": "done",
                    "places_found": places_found, "reviews_found": reviews_found, "photos_found": photos_found,
                    "new_places": new_places, "already_known_places": already_known
                }
            )

        except JobCancelledError:
            self.db.update_job(job_id, {
                "status": "cancelled",
                "stage": "cancelled",
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "places_found": places_found,
                "reviews_found": reviews_found,
                "photos_found": photos_found
            })
            self.emit_event(
                job_id, "cancelled", "Job cancelled by user. Partial places remain in database.",
                event_type="done",
                level="warning",
                payload={
                    "job_id": job_id, "status": "cancelled",
                    "places_found": places_found, "reviews_found": reviews_found, "photos_found": photos_found
                }
            )
        except Exception as e:
            err_msg = str(e)
            self.db.update_job(job_id, {
                "status": "failed",
                "stage": "failed",
                "error": err_msg,
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "places_found": places_found,
                "reviews_found": reviews_found,
                "photos_found": photos_found
            })
            self.emit_event(
                job_id, "failed", f"Job failed: {err_msg}",
                event_type="error",
                level="error",
                payload={"job_id": job_id, "status": "failed", "message": err_msg, "detail": err_msg}
            )
            self.emit_event(
                job_id, "failed", f"Job failed: {err_msg}",
                event_type="done",
                level="error",
                payload={"job_id": job_id, "status": "failed", "message": err_msg, "detail": err_msg}
            )
        finally:
            await http_client.close()
