import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

from ..config import ScraperConfig, load_config
from ..db import Database
from ..export import export_stream_bytes
from ..checkpoint import CheckpointManager
from ..jobs import JobManager
from ..geo.resolve import resolve_location, estimate_scrape_complexity
from ..geo.nlparse import parse_natural_language_query

def _parse_bool(val: Optional[Any]) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    s = str(val).lower().strip()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None

def _parse_multi(val: Optional[Any]) -> Optional[List[str]]:
    if val is None:
        return None
    if isinstance(val, (list, tuple, set)):
        items = []
        for v in val:
            for sub in str(v).split(","):
                s = sub.strip()
                if s:
                    items.append(s)
        return items if items else None
    if isinstance(val, str):
        items = [s.strip() for s in val.split(",") if s.strip()]
        return items if items else None
    return [str(val)]

def create_app(config: Optional[ScraperConfig] = None) -> FastAPI:
    cfg = config or load_config()
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    app = FastAPI(title="Google Maps Scraper Local Dashboard", docs_url=None, redoc_url=None)

    web_dir = Path(__file__).resolve().parent
    static_dir = web_dir / "static"
    templates_dir = web_dir / "templates"

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))

    checkpoint = CheckpointManager(db)
    job_manager = JobManager.get_instance(db, cfg)
    job_manager.start_worker()

    @app.on_event("startup")
    async def on_startup():
        job_manager.start_worker()

    # -------------------------------------------------------------
    # HTML View Routes
    # -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_view(request: Request):
        stats = db.get_stats()
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"active_page": "dashboard", "stats": stats}
        )

    @app.get("/live", response_class=HTMLResponse)
    async def live_view(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="live.html",
            context={"active_page": "live"}
        )

    @app.get("/places", response_class=HTMLResponse)
    async def places_view(
        request: Request,
        q: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        vertical: Optional[List[str]] = Query(None),
        country: Optional[List[str]] = Query(None),
        city: Optional[List[str]] = Query(None),
        min_rating: Optional[float] = None,
        min_reviews: Optional[int] = None,
        has_phone: Optional[str] = None,
        has_website: Optional[str] = None,
        has_photos: Optional[str] = None,
        claimed: Optional[str] = None,
        has_reviews: Optional[str] = None,
        has_hours: Optional[str] = None,
        has_attributes: Optional[str] = None,
        job: Optional[str] = None,
        sort: str = "rating_desc",
        page: int = 1,
        page_size: int = 50
    ):
        v_list = _parse_multi(vertical)
        c_list = _parse_multi(country)
        ci_list = _parse_multi(city)

        b_phone = _parse_bool(has_phone)
        b_web = _parse_bool(has_website)
        b_photos = _parse_bool(has_photos)
        b_claimed = _parse_bool(claimed)
        b_reviews = _parse_bool(has_reviews)
        b_hours = _parse_bool(has_hours)
        b_attrs = _parse_bool(has_attributes)

        places, total = db.query_places(
            q=q,
            status=status,
            category=category,
            vertical=v_list,
            country=c_list,
            city=ci_list,
            job=job,
            min_rating=min_rating,
            min_reviews=min_reviews,
            has_phone=b_phone,
            has_website=b_web,
            has_photos=b_photos,
            claimed=b_claimed,
            has_reviews=b_reviews,
            has_hours=b_hours,
            has_attributes=b_attrs,
            sort=sort,
            page=page,
            page_size=page_size
        )

        filter_params = {}
        if q: filter_params["q"] = q
        if status: filter_params["status"] = status
        if category: filter_params["category"] = category
        if v_list: filter_params["vertical"] = ",".join(v_list)
        if c_list: filter_params["country"] = ",".join(c_list)
        if ci_list: filter_params["city"] = ",".join(ci_list)
        if job: filter_params["job"] = job
        if min_rating is not None: filter_params["min_rating"] = str(min_rating)
        if min_reviews is not None: filter_params["min_reviews"] = str(min_reviews)
        if has_phone: filter_params["has_phone"] = has_phone
        if has_website: filter_params["has_website"] = has_website
        if has_photos: filter_params["has_photos"] = has_photos
        if claimed: filter_params["claimed"] = claimed
        if has_reviews: filter_params["has_reviews"] = has_reviews
        if has_hours: filter_params["has_hours"] = has_hours
        if has_attributes: filter_params["has_attributes"] = has_attributes

        return templates.TemplateResponse(
            request=request,
            name="places.html",
            context={
                "active_page": "places",
                "places": places,
                "total_count": total,
                "page": page,
                "page_size": page_size,
                "sort": sort,
                "filters": {
                    "q": q, "status": status, "category": category,
                    "vertical": v_list, "country": c_list, "city": ci_list,
                    "job": job,
                    "min_rating": min_rating, "min_reviews": min_reviews,
                    "has_phone": b_phone, "has_website": b_web,
                    "has_photos": b_photos, "claimed": b_claimed,
                    "has_reviews": b_reviews, "has_hours": b_hours,
                    "has_attributes": b_attrs,
                },
                "filter_params": filter_params
            }
        )

    @app.get("/places/{cid}", response_class=HTMLResponse)
    async def place_detail_view(request: Request, cid: str):
        place = db.get_place_details_full(cid)
        if not place:
            raise HTTPException(status_code=404, detail="Place not found")
        return templates.TemplateResponse(
            request=request,
            name="place_detail.html",
            context={"active_page": "places", "place": place}
        )

    @app.get("/map", response_class=HTMLResponse)
    async def map_view(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="map.html",
            context={"active_page": "map"}
        )

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_view(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="runs.html",
            context={"active_page": "runs"}
        )

    @app.get("/quality", response_class=HTMLResponse)
    async def quality_view(request: Request):
        quality = db.get_quality_report()
        return templates.TemplateResponse(
            request=request,
            name="quality.html",
            context={"active_page": "quality", "quality": quality}
        )

    # -------------------------------------------------------------
    # JSON API Endpoints (All under /api)
    # -------------------------------------------------------------

    @app.get("/api/stats")
    async def api_stats():
        return db.get_stats()

    @app.get("/api/verticals")
    async def api_verticals(
        q: Optional[str] = None,
        country: Optional[List[str]] = Query(None),
        city: Optional[List[str]] = Query(None),
        min_rating: Optional[float] = None,
        min_reviews: Optional[int] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        has_phone: Optional[str] = None,
        has_website: Optional[str] = None,
        has_photos: Optional[str] = None,
        claimed: Optional[str] = None,
        has_reviews: Optional[str] = None,
        has_hours: Optional[str] = None,
        has_attributes: Optional[str] = None
    ):
        filters = {
            "q": q,
            "country": _parse_multi(country),
            "city": _parse_multi(city),
            "min_rating": min_rating,
            "min_reviews": min_reviews,
            "status": status,
            "category": category,
            "has_phone": _parse_bool(has_phone),
            "has_website": _parse_bool(has_website),
            "has_photos": _parse_bool(has_photos),
            "claimed": _parse_bool(claimed),
            "has_reviews": _parse_bool(has_reviews),
            "has_hours": _parse_bool(has_hours),
            "has_attributes": _parse_bool(has_attributes)
        }
        return db.get_vertical_facets(filters)

    @app.get("/api/countries")
    async def api_countries(
        q: Optional[str] = None,
        vertical: Optional[List[str]] = Query(None),
        city: Optional[List[str]] = Query(None),
        min_rating: Optional[float] = None,
        min_reviews: Optional[int] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        has_phone: Optional[str] = None,
        has_website: Optional[str] = None,
        has_photos: Optional[str] = None,
        claimed: Optional[str] = None,
        has_reviews: Optional[str] = None,
        has_hours: Optional[str] = None,
        has_attributes: Optional[str] = None
    ):
        filters = {
            "q": q,
            "vertical": _parse_multi(vertical),
            "city": _parse_multi(city),
            "min_rating": min_rating,
            "min_reviews": min_reviews,
            "status": status,
            "category": category,
            "has_phone": _parse_bool(has_phone),
            "has_website": _parse_bool(has_website),
            "has_photos": _parse_bool(has_photos),
            "claimed": _parse_bool(claimed),
            "has_reviews": _parse_bool(has_reviews),
            "has_hours": _parse_bool(has_hours),
            "has_attributes": _parse_bool(has_attributes)
        }
        return db.get_country_facets(filters)

    @app.get("/api/cities")
    async def api_cities(
        country: Optional[str] = None,
        q: Optional[str] = None,
        vertical: Optional[List[str]] = Query(None),
        min_rating: Optional[float] = None,
        min_reviews: Optional[int] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        has_phone: Optional[str] = None,
        has_website: Optional[str] = None,
        has_photos: Optional[str] = None,
        claimed: Optional[str] = None,
        has_reviews: Optional[str] = None,
        has_hours: Optional[str] = None,
        has_attributes: Optional[str] = None
    ):
        filters = {
            "q": q,
            "vertical": _parse_multi(vertical),
            "min_rating": min_rating,
            "min_reviews": min_reviews,
            "status": status,
            "category": category,
            "has_phone": _parse_bool(has_phone),
            "has_website": _parse_bool(has_website),
            "has_photos": _parse_bool(has_photos),
            "claimed": _parse_bool(claimed),
            "has_reviews": _parse_bool(has_reviews),
            "has_hours": _parse_bool(has_hours),
            "has_attributes": _parse_bool(has_attributes)
        }
        return db.get_city_facets(country=country, filters=filters)

    @app.get("/api/places")
    async def api_places(
        q: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        vertical: Optional[List[str]] = Query(None),
        country: Optional[List[str]] = Query(None),
        city: Optional[List[str]] = Query(None),
        min_rating: Optional[float] = None,
        min_reviews: Optional[int] = None,
        has_phone: Optional[str] = None,
        has_website: Optional[str] = None,
        has_photos: Optional[str] = None,
        claimed: Optional[str] = None,
        has_reviews: Optional[str] = None,
        has_hours: Optional[str] = None,
        has_attributes: Optional[str] = None,
        job: Optional[str] = None,
        sort: str = "rating_desc",
        page: int = 1,
        page_size: int = 50
    ):
        places, total = db.query_places(
            q=q,
            status=status,
            category=category,
            vertical=_parse_multi(vertical),
            country=_parse_multi(country),
            city=_parse_multi(city),
            job=job,
            min_rating=min_rating,
            min_reviews=min_reviews,
            has_phone=_parse_bool(has_phone),
            has_website=_parse_bool(has_website),
            has_photos=_parse_bool(has_photos),
            claimed=_parse_bool(claimed),
            has_reviews=_parse_bool(has_reviews),
            has_hours=_parse_bool(has_hours),
            has_attributes=_parse_bool(has_attributes),
            sort=sort,
            page=page,
            page_size=page_size
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": places
        }

    @app.get("/api/places/{cid}")
    async def api_place_detail(cid: str):
        place = db.get_place_details_full(cid)
        if not place:
            raise HTTPException(status_code=404, detail="Place not found")
        return place

    @app.get("/api/places/{cid}/full")
    async def api_place_detail_full(cid: str):
        """Returns every column of the place + hours + attributes + reviews + photos + popular_times."""
        place = db.get_place_details_full(cid)
        if not place:
            raise HTTPException(status_code=404, detail="Place not found")
        return place

    @app.get("/api/places/{cid}/reviews")
    async def api_place_reviews(cid: str, page: int = 1, page_size: int = 50):
        offset = max(0, (page - 1) * page_size)
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE cid = ? ORDER BY timestamp_ms DESC LIMIT ? OFFSET ?",
                (cid, page_size, offset)
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM reviews WHERE cid = ?", (cid,)).fetchone()[0]
        return {
            "total": total,
            "page": page,
            "items": [dict(r) for r in rows]
        }

    @app.get("/api/places/{cid}/photos")
    async def api_place_photos(cid: str):
        with db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM photos WHERE cid = ?", (cid,)).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/map")
    async def api_map_points(
        q: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        vertical: Optional[List[str]] = Query(None),
        country: Optional[List[str]] = Query(None),
        city: Optional[List[str]] = Query(None),
        min_rating: Optional[float] = None,
        min_reviews: Optional[int] = None,
        has_phone: Optional[str] = None,
        has_website: Optional[str] = None,
        has_photos: Optional[str] = None,
        claimed: Optional[str] = None,
        has_reviews: Optional[str] = None,
        has_hours: Optional[str] = None,
        has_attributes: Optional[str] = None
    ):
        places, _ = db.query_places(
            q=q,
            status=status,
            category=category,
            vertical=_parse_multi(vertical),
            country=_parse_multi(country),
            city=_parse_multi(city),
            min_rating=min_rating,
            min_reviews=min_reviews,
            has_phone=_parse_bool(has_phone),
            has_website=_parse_bool(has_website),
            has_photos=_parse_bool(has_photos),
            claimed=_parse_bool(claimed),
            has_reviews=_parse_bool(has_reviews),
            has_hours=_parse_bool(has_hours),
            has_attributes=_parse_bool(has_attributes),
            page=1,
            page_size=50000
        )
        points = [
            {
                "cid": p["cid"],
                "lat": p["latitude"],
                "lng": p["longitude"],
                "name": p["name"],
                "rating": p["rating"],
                "category": p["category"],
                "vertical": p.get("vertical"),
                "country": p.get("country"),
                "city": p.get("city")
            }
            for p in places
            if p.get("latitude") is not None and p.get("longitude") is not None
        ]
        return points

    @app.get("/api/progress")
    async def api_progress():
        return checkpoint.get_progress()

    @app.get("/api/logs")
    async def api_logs(tail: int = 200):
        log_file = cfg.absolute_logs_dir / "gmaps.log"
        if not log_file.exists():
            return {"logs": "No log file found."}
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return {"logs": "".join(lines[-tail:])}
        except Exception as e:
            return {"logs": f"Error reading logs: {e}"}

    @app.post("/api/run/{action}")
    async def api_run_action(action: str):
        valid_actions = ["search", "enrich", "reviews", "photos", "export", "full"]
        if action not in valid_actions:
            raise HTTPException(status_code=400, detail=f"Invalid action. Expected one of {valid_actions}")

        checkpoint.start_run(query="ui_triggered", bbox="", step=action)
        log_file = cfg.absolute_logs_dir / "gmaps.log"
        cfg.absolute_logs_dir.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{checkpoint.start_time}] [INFO] [UI] Action '{action}' triggered from local dashboard.\n")

        checkpoint.update_progress(step=action, discovered=0, enriched=0)
        return {"status": "started", "action": action, "run_id": checkpoint.run_id}

    @app.post("/api/resume")
    async def api_resume():
        checkpoint.start_run(query="resume", bbox="", step="resume")
        log_file = cfg.absolute_logs_dir / "gmaps.log"
        cfg.absolute_logs_dir.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("[INFO] [UI] Pipeline run resumed from last incomplete state.\n")
        return {"status": "resumed"}

    @app.post("/api/retry-failed")
    async def api_retry_failed():
        count = db.retry_failed_places()
        log_file = cfg.absolute_logs_dir / "gmaps.log"
        cfg.absolute_logs_dir.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[INFO] [UI] Reset {count} failed places to pending status.\n")
        return {"status": "retried", "reset_count": count}

    @app.get("/api/export")
    async def api_export(
        format: str = "csv",
        full: bool = False,
        q: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        vertical: Optional[List[str]] = Query(None),
        country: Optional[List[str]] = Query(None),
        city: Optional[List[str]] = Query(None),
        min_rating: Optional[float] = None,
        min_reviews: Optional[int] = None,
        has_phone: Optional[str] = None,
        has_website: Optional[str] = None,
        has_photos: Optional[str] = None,
        claimed: Optional[str] = None,
        has_reviews: Optional[str] = None,
        has_hours: Optional[str] = None,
        has_attributes: Optional[str] = None,
        job: Optional[str] = None
    ):
        filters = {
            "q": q,
            "status": status,
            "category": category,
            "vertical": _parse_multi(vertical),
            "country": _parse_multi(country),
            "city": _parse_multi(city),
            "job": job,
            "min_rating": min_rating,
            "min_reviews": min_reviews,
            "has_phone": _parse_bool(has_phone),
            "has_website": _parse_bool(has_website),
            "has_photos": _parse_bool(has_photos),
            "claimed": _parse_bool(claimed),
            "has_reviews": _parse_bool(has_reviews),
            "has_hours": _parse_bool(has_hours),
            "has_attributes": _parse_bool(has_attributes)
        }
        content_bytes, media_type = export_stream_bytes(db, export_format=format, filters=filters, full=full)
        filename = f"places_export_{'full' if full else 'compact'}.{format}"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(content=content_bytes, media_type=media_type, headers=headers)

    # -------------------------------------------------------------
    # Live Search & Streaming API Endpoints
    # -------------------------------------------------------------
    MAX_SSE_CONNECTIONS = 10
    _active_sse_connections = 0

    @app.post("/api/live/parse")
    async def api_live_parse(request: Request):
        """Natural language parse into (query, location)."""
        body = await request.json()
        text = str(body.get("text", "")).strip()
        q, loc = parse_natural_language_query(text)
        return {"query": q, "location": loc or ""}

    @app.post("/api/live/resolve")
    async def api_live_resolve(request: Request):
        """Resolves location free text/coords/bbox via search RPC."""
        body = await request.json()
        loc = str(body.get("location", "")).strip()
        force = bool(body.get("force_refresh", False))
        if not loc:
            raise HTTPException(status_code=400, detail="Location input cannot be empty.")
        try:
            res = await resolve_location(loc, db=db, force_refresh=force)
            return res
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Location resolution failed: {e}")

    @app.post("/api/live/start")
    async def api_live_start(request: Request):
        """Starts a new live scrape job or validates pre-flight estimate."""
        body = await request.json()
        query = str(body.get("query", "")).strip()
        location = str(body.get("location", "")).strip()
        lat = body.get("lat")
        lng = body.get("lng")
        span_deg = body.get("span_deg")
        options = body.get("options", {})
        confirm_large = bool(body.get("confirmed", False))

        if not query:
            raise HTTPException(status_code=400, detail="Business query is required.")

        if not location and lat is not None and lng is not None:
            location = f"{lat},{lng}"
            if span_deg:
                options["span_override_deg"] = float(span_deg)

        if not location:
            raise HTTPException(status_code=400, detail="Location or coordinates required.")

        # Guardrail: Pre-flight check
        eff_span = float(span_deg) if span_deg is not None else 0.1
        est = estimate_scrape_complexity(eff_span, max_places=options.get("max_places", 200))
        if est["estimated_requests"] > 5000 and not confirm_large:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "large_job_confirmation_required",
                    "detail": f"Job estimated at {est['estimated_requests']} requests. Please confirm to proceed.",
                    "estimated_requests": est["estimated_requests"],
                    "estimated_tiles": est["estimated_tiles"]
                }
            )

        job_id = job_manager.create_job(query=query, location=location, options=options)
        return {"job_id": job_id}

    @app.get("/api/live/stream")
    async def api_live_stream(request: Request, job_id: str):
        """Server-Sent Events streaming endpoint for live jobs."""
        nonlocal _active_sse_connections
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if _active_sse_connections >= MAX_SSE_CONNECTIONS:
            raise HTTPException(status_code=429, detail="Too many concurrent stream connections (limit 10).")

        async def sse_generator():
            nonlocal _active_sse_connections
            _active_sse_connections += 1
            queue = job_manager.subscribe(job_id)
            try:
                # 1. Replay historical events from DB for late/reconnecting clients
                past_events = db.get_job_events(job_id, limit=300)
                for ev in past_events:
                    stage = ev.get("stage") or ""
                    level = ev.get("level") or "info"
                    payload = ev.get("payload") or {"message": ev.get("message", ""), "stage": stage}
                    
                    if "cid" in payload:
                        ev_type = "place"
                    elif stage in ("done", "cancelled"):
                        ev_type = "done"
                    elif level == "error":
                        ev_type = "error"
                    elif "done" in payload and "total" in payload:
                        ev_type = "progress"
                    else:
                        ev_type = "stage"

                    yield f"event: {ev_type}\ndata: {json.dumps(payload)}\n\n"

                # Check if job already terminated
                current_job = db.get_job(job_id)
                if current_job and current_job["status"] in ("done", "cancelled", "failed"):
                    return

                # 2. Stream live events with 15s heartbeats
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                        ev_type = msg.get("event", "progress")
                        data = msg.get("data", {})
                        yield f"event: {ev_type}\ndata: {json.dumps(data)}\n\n"

                        if ev_type in ("done", "error"):
                            break
                        if isinstance(data, dict) and data.get("status") in ("done", "cancelled", "failed"):
                            break
                    except asyncio.TimeoutError:
                        # 15s heartbeat comment
                        yield ": ping\n\n"
            finally:
                job_manager.unsubscribe(job_id, queue)
                _active_sse_connections = max(0, _active_sse_connections - 1)

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    @app.get("/api/live/job/{job_id}")
    async def api_live_get_job(job_id: str):
        """Returns job summary and full event history log."""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        events = db.get_job_events(job_id, limit=300)
        return {"job": job, "events": events}

    @app.get("/api/live/jobs")
    async def api_live_list_jobs(limit: int = 50):
        """Returns recent scraping jobs."""
        return db.list_jobs(limit=limit)

    @app.post("/api/live/cancel/{job_id}")
    async def api_live_cancel(job_id: str):
        """Cooperatively cancels a running or queued job."""
        success = job_manager.cancel_job(job_id)
        if not success:
            raise HTTPException(status_code=400, detail="Job cannot be cancelled or not found")
        return {"status": "cancelled", "job_id": job_id}

    @app.post("/api/live/retry/{job_id}")
    async def api_live_retry(job_id: str):
        """Re-runs an interrupted or failed job."""
        try:
            jid = job_manager.retry_job(job_id)
            return {"status": "requeued", "job_id": jid}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # -------------------------------------------------------------
    # Media Serving: Local photos served cleanly with 404s
    # -------------------------------------------------------------

    @app.get("/media/photos/{cid}/{filename}")
    async def serve_local_photo(cid: str, filename: str):
        photo_path = cfg.absolute_photos_dir / cid / filename
        if not photo_path.exists():
            raise HTTPException(status_code=404, detail="Photo not found on local disk")

        suffix = photo_path.suffix.lower()
        media_type = "image/jpeg" if suffix in [".jpg", ".jpeg"] else "image/png" if suffix == ".png" else "image/webp"
        return FileResponse(str(photo_path), media_type=media_type)

    return app
