import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.responses import StreamingResponse

from ..config import ScraperConfig, load_config
from ..db import Database
from ..export import export_stream_bytes, list_all_state_exports, resolve_safe_export_path, export_state_complete_package
from ..geo.boundaries import get_region_bounding_boxes
from ..checkpoint import CheckpointManager
from ..jobs import JobManager
from ..geo.resolve import resolve_location, estimate_scrape_complexity
from ..geo.nlparse import parse_natural_language_query
from ..scope import get_target_countries, get_us_states, get_mexico_entities, validate_target_country
from ..website import WebsiteDiscoveryService, WebsiteEnrichmentService
from ..orchestrator import PipelineOrchestrator, PipelineConfig, DEFAULT_CATEGORIES
from ..taxonomy import load_verticals

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

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        error_msgs = []
        for e in errors:
            loc = " -> ".join(str(l) for l in e.get("loc", []) if l != "body")
            msg = e.get("msg", "invalid")
            error_msgs.append(f"{loc}: {msg}" if loc else msg)
        detail_msg = "; ".join(error_msgs) or "Invalid request parameters"
        return JSONResponse(
            status_code=422,
            content={"detail": detail_msg, "errors": errors},
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
        errors = exc.errors()
        error_msgs = []
        for e in errors:
            loc = " -> ".join(str(l) for l in e.get("loc", []) if l != "body")
            msg = e.get("msg", "invalid")
            error_msgs.append(f"{loc}: {msg}" if loc else msg)
        detail_msg = "; ".join(error_msgs) or "Invalid configuration"
        return JSONResponse(
            status_code=422,
            content={"detail": detail_msg, "errors": errors},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        if isinstance(exc, HTTPException):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=getattr(exc, "headers", None),
            )
        logging.getLogger("gmaps_scraper.web").exception(
            f"Unhandled exception during {request.method} {request.url.path}: {exc}"
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal server error occurred while processing the request."},
        )

    web_dir = Path(__file__).resolve().parent
    static_dir = web_dir / "static"
    templates_dir = web_dir / "templates"

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))

    checkpoint = CheckpointManager(db)
    job_manager = JobManager.get_instance(db, cfg)
    job_manager.start_worker()
    orchestrator = PipelineOrchestrator.get_instance(db, cfg)

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
        region: Optional[List[str]] = Query(None),
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
        website_status: Optional[str] = None,
        enrichment_status: Optional[str] = None,
        missing_info: Optional[str] = None,
        job: Optional[str] = None,
        sort: str = "rating_desc",
        page: int = 1,
        page_size: int = 50
    ):
        v_list = _parse_multi(vertical)
        c_list = _parse_multi(country)
        r_list = _parse_multi(region)
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
            region=r_list,
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
            website_status=website_status,
            enrichment_status=enrichment_status,
            missing_info=missing_info,
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
        if r_list: filter_params["region"] = ",".join(r_list)
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
        if website_status: filter_params["website_status"] = website_status
        if enrichment_status: filter_params["enrichment_status"] = enrichment_status
        if missing_info: filter_params["missing_info"] = missing_info

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
                    "vertical": v_list, "country": c_list, "region": r_list, "city": ci_list,
                    "job": job,
                    "min_rating": min_rating, "min_reviews": min_reviews,
                    "has_phone": b_phone, "has_website": b_web,
                    "has_photos": b_photos, "claimed": b_claimed,
                    "has_reviews": b_reviews, "has_hours": b_hours,
                    "has_attributes": b_attrs,
                    "website_status": website_status,
                    "enrichment_status": enrichment_status,
                    "missing_info": missing_info
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

    @app.get("/pipeline", response_class=HTMLResponse)
    async def pipeline_view(request: Request):
        verticals = load_verticals()
        categories = []
        for k, v in verticals.items():
            if k == "other":
                continue
            lbl = v.get("label", k.title()) if isinstance(v, dict) else k.title()
            categories.append({"key": k, "label": lbl})

        saved_config = db.get_pipeline_config() or PipelineConfig().model_dump()
        us_states_dict = {code: s.model_dump() for code, s in get_us_states().items()}
        mexico_entities_dict = {code: e.model_dump() for code, e in get_mexico_entities().items()}

        return templates.TemplateResponse(
            request=request,
            name="pipeline.html",
            context={
                "active_page": "pipeline",
                "target_countries": get_target_countries(),
                "us_states": us_states_dict,
                "mexico_entities": mexico_entities_dict,
                "stats": db.get_stats(),
                "categories": categories,
                "saved_config": saved_config,
                "orchestrator_status": orchestrator.get_status(),
            }
        )

    @app.get("/enrichment", response_class=HTMLResponse)
    async def enrichment_view(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="enrichment.html",
            context={
                "active_page": "enrichment",
                "stats": db.get_stats(),
                "target_countries": get_target_countries(),
                "us_states": get_us_states(),
                "mexico_entities": get_mexico_entities(),
            }
        )

    @app.get("/intelligence", response_class=HTMLResponse)
    async def intelligence_view(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="intelligence.html",
            context={
                "active_page": "intelligence",
                "summary": db.get_intelligence_summary(),
                "stats": db.get_stats(),
                "target_countries": get_target_countries(),
                "us_states": get_us_states(),
                "mexico_entities": get_mexico_entities(),
            }
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
        jobs = db.list_jobs(limit=50)
        with db.get_connection() as conn:
            tasks = [dict(r) for r in conn.execute("SELECT * FROM collection_tasks ORDER BY updated_at DESC LIMIT 50").fetchall()]
        return templates.TemplateResponse(
            request=request,
            name="runs.html",
            context={"active_page": "runs", "jobs": jobs, "tasks": tasks}
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
        region: Optional[List[str]] = Query(None),
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
        website_status: Optional[str] = None,
        enrichment_status: Optional[str] = None,
        missing_info: Optional[str] = None,
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
            region=_parse_multi(region),
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
            website_status=website_status,
            enrichment_status=enrichment_status,
            missing_info=missing_info,
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

    @app.get("/api/scope/regions")
    async def api_scope_regions(country_code: Optional[str] = None):
        """Returns configured scope target countries and state/federal entity lists."""
        target_countries = get_target_countries()
        if country_code:
            cc = country_code.upper().strip()
            if cc == "US":
                states = get_us_states()
                return {"country": "US", "regions": [{"code": s.code, "name": s.name} for s in states.values()]}
            elif cc == "MX":
                entities = get_mexico_entities()
                return {"country": "MX", "regions": [{"code": e.code, "name": e.name} for e in entities.values()]}
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported country {country_code}. Permitted: US, MX")
        return {
            "countries": list(target_countries.keys()),
            "us_states": [{"code": s.code, "name": s.name} for s in get_us_states().values()],
            "mexico_entities": [{"code": e.code, "name": e.name} for e in get_mexico_entities().values()]
        }

    @app.get("/api/enrichment/stats")
    async def api_enrichment_stats(country_code: Optional[str] = None, region_code: Optional[str] = None):
        """Returns official website verification and enrichment metrics from SQLite."""
        return {
            "discovery": db.get_website_discovery_stats(country_code=country_code, region_code=region_code),
            "enrichment": db.get_enrichment_stats(country_code=country_code, region_code=region_code),
        }

    @app.get("/api/enrichment/items")
    async def api_enrichment_items(
        tab: str = "all",
        country: Optional[str] = None,
        region: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 50
    ):
        """Returns places filtered for the dedicated Website Enrichment views."""
        website_status = None
        enrichment_status = None
        missing_info = None

        if tab == "verified":
            website_status = "verified"
        elif tab == "candidates":
            website_status = "candidate"
        elif tab == "needs_review":
            website_status = "needs_review"
        elif tab == "missing":
            website_status = "website_missing"
        elif tab == "failed":
            website_status = "failed"
        elif tab == "enriched":
            enrichment_status = "completed"
        elif tab == "profiles":
            missing_info = None
        elif tab == "leaders":
            missing_info = None
        elif tab == "contacts":
            missing_info = None

        c_list = [country.strip().upper()] if country else None
        r_list = [region.strip().upper()] if region else None

        places, total = db.query_places(
            q=q,
            country=c_list,
            region=r_list,
            website_status=website_status,
            enrichment_status=enrichment_status,
            missing_info=missing_info,
            page=page,
            page_size=page_size
        )
        return {"total": total, "page": page, "page_size": page_size, "items": places}

    @app.post("/api/pipeline/run-discovery")
    async def api_run_discovery(request: Request):
        """Runs Step 2 Official Website Discovery & Verification workflow."""
        body = await request.json()
        country_code = str(body.get("country_code", "US")).strip().upper()
        region_code = body.get("region_code") or None
        if region_code:
            region_code = str(region_code).strip().upper()
        limit = int(body.get("limit", 50))
        retry_failed = bool(body.get("retry_failed", False))
        force = bool(body.get("force", False))
        dry_run = bool(body.get("dry_run", False))

        try:
            validate_target_country(country_code)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        discovery_service = WebsiteDiscoveryService(db)
        result = await discovery_service.run_discovery(
            country_code=country_code,
            region_code=region_code,
            limit=limit,
            retry_failed=retry_failed,
            force=force,
            dry_run=dry_run
        )
        return result

    @app.post("/api/pipeline/run-enrichment")
    async def api_run_enrichment(request: Request):
        """Runs Step 3 Company Profile, Leadership, and Contact Extraction workflow."""
        body = await request.json()
        country_code = str(body.get("country_code", "US")).strip().upper()
        region_code = body.get("region_code") or None
        if region_code:
            region_code = str(region_code).strip().upper()
        limit = int(body.get("limit", 50))
        max_pages = int(body.get("max_pages", 5))
        retry_failed = bool(body.get("retry_failed", False))
        force = bool(body.get("force", False))
        dry_run = bool(body.get("dry_run", False))

        try:
            validate_target_country(country_code)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        enrichment_service = WebsiteEnrichmentService(db, max_pages=max_pages)
        result = await enrichment_service.run_enrichment(
            country_code=country_code,
            region_code=region_code,
            limit=limit,
            max_pages=max_pages,
            retry_failed=retry_failed,
            force=force,
            dry_run=dry_run
        )
        return result

    @app.get("/api/intelligence")
    async def api_get_intelligence(
        cid: Optional[str] = None,
        country: Optional[str] = None,
        region: Optional[str] = None,
        match_status: Optional[str] = None,
        entity_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 50
    ):
        offset = (max(1, page) - 1) * page_size
        findings, total = db.get_additional_intelligence(
            cid=cid,
            country_code=country,
            region_code=region,
            match_status=match_status,
            entity_type=entity_type,
            limit=page_size,
            offset=offset
        )
        return {
            "findings": findings,
            "total_count": total,
            "page": page,
            "page_size": page_size
        }

    @app.get("/api/intelligence/summary")
    async def api_get_intelligence_summary(
        country: Optional[str] = None,
        region: Optional[str] = None
    ):
        return db.get_intelligence_summary(country_code=country, region_code=region)

    @app.get("/api/intelligence/sources")
    async def api_get_intelligence_sources(
        cid: Optional[str] = None,
        limit: int = 50
    ):
        return {"sources": db.get_intelligence_sources(cid=cid, limit=limit)}

    @app.get("/api/storage/status")
    async def api_storage_status():
        """Returns storage status including central DB, regional DBs, and exports."""
        from ..storage import RegionalStorageManager
        mgr = RegionalStorageManager(db, cfg)
        return mgr.get_storage_status()

    @app.get("/api/storage/databases")
    async def api_storage_databases(country: Optional[str] = None):
        """Returns the list of state-wise SQLite databases."""
        from ..storage import RegionalStorageManager
        mgr = RegionalStorageManager(db, cfg)
        status = mgr.get_storage_status()
        dbs = status.get("regional_databases", [])
        if country:
            dbs = [d for d in dbs if d.get("country_code", "").upper() == country.upper()]
        return {"databases": dbs, "count": len(dbs)}

    @app.post("/api/pipeline/run-incremental-enrichment")
    async def api_run_incremental_enrichment(request: Request):
        """Runs Incremental Business Intelligence Enrichment workflow."""
        from ..incremental.service import IncrementalEnrichmentService
        body = await request.json()
        country_code = str(body.get("country_code", "US")).strip().upper()
        region_code = body.get("region_code") or None
        if region_code:
            region_code = str(region_code).strip().upper()
        limit = int(body.get("limit", 20))
        max_pages = int(body.get("max_pages", 5))
        source_types = body.get("source_types") or ["all"]
        retry_failed = bool(body.get("retry_failed", False))
        force = bool(body.get("force", False))
        dry_run = bool(body.get("dry_run", False))

        try:
            validate_target_country(country_code)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        inc_service = IncrementalEnrichmentService(
            db=db,
            max_pages=max_pages,
            source_types=source_types
        )
        result = await inc_service.run_incremental_enrichment(
            country_code=country_code,
            region_code=region_code,
            limit=limit,
            max_pages=max_pages,
            retry_failed=retry_failed,
            force=force,
            dry_run=dry_run
        )
        return result

    @app.get("/api/pipeline/config")
    async def api_pipeline_get_config():
        """Returns persisted user pipeline configuration or default config."""
        cfg_data = db.get_pipeline_config()
        if not cfg_data:
            cfg_data = PipelineConfig().model_dump()
        return cfg_data

    @app.post("/api/pipeline/config")
    async def api_pipeline_save_config(config: PipelineConfig):
        """Saves user pipeline configuration so it persists across restarts."""
        try:
            db.save_pipeline_config(config.model_dump())
            return {"status": "saved", "config": config.model_dump()}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/pipeline/plan")
    async def api_pipeline_plan(config: PipelineConfig):
        """Generates deterministic execution order and plan before starting."""
        try:
            plan = orchestrator.preview_plan(config)
            return plan
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/pipeline/start")
    async def api_pipeline_start(config: Optional[PipelineConfig] = None):
        """Starts automated state-by-state pipeline run."""
        if config is None:
            saved = db.get_pipeline_config()
            try:
                config = PipelineConfig(**saved) if saved else PipelineConfig()
            except Exception:
                config = PipelineConfig()

        try:
            run_id = await orchestrator.start(config)
            return {"status": "started", "run_id": run_id}
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/pipeline/pause")
    async def api_pipeline_pause():
        """Pauses the active pipeline run."""
        success = orchestrator.pause()
        if not success:
            raise HTTPException(status_code=400, detail="No active pipeline run to pause.")
        return {"status": "paused"}

    @app.post("/api/pipeline/resume")
    async def api_pipeline_resume():
        """Resumes a paused pipeline run."""
        success = orchestrator.resume()
        if not success:
            raise HTTPException(status_code=400, detail="No paused pipeline run to resume.")
        return {"status": "resumed"}

    @app.post("/api/pipeline/cancel")
    async def api_pipeline_cancel():
        """Cancels the active pipeline run."""
        success = orchestrator.cancel()
        if not success:
            raise HTTPException(status_code=400, detail="No active pipeline run to cancel.")
        return {"status": "cancelled"}

    @app.post("/api/pipeline/retry-failed")
    async def api_pipeline_retry_failed():
        """Resets failed collection tasks for retry."""
        count = orchestrator.retry_failed()
        return {"status": "retried", "reset_count": count}

    @app.get("/api/pipeline/status")
    async def api_pipeline_status():
        """Returns live orchestrator status, stage, current state, counts, and metrics."""
        return orchestrator.get_status()

    @app.get("/api/pipeline/events")
    async def api_pipeline_events():
        """SSE stream for real-time progress updates on the pipeline."""
        q = orchestrator.subscribe()

        async def event_generator():
            try:
                initial = {"event": "init", "data": orchestrator.get_status()}
                yield f"data: {json.dumps(initial)}\n\n"
                while True:
                    msg = await q.get()
                    yield f"data: {json.dumps(msg)}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                orchestrator.unsubscribe(q)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.get("/api/pipeline/tiles")
    async def api_pipeline_tiles(
        country: Optional[str] = None,
        region: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 2000
    ):
        """Returns geographic tiles with metrics and counts for canvas map rendering."""
        tiles = db.get_geographic_tiles(
            country_code=country,
            region_code=region,
            category=category,
            status=status,
            run_id=run_id,
            limit=limit
        )
        tile_counts = db.get_pipeline_tile_counts(
            run_id=run_id,
            country_code=country,
            region_code=region
        )
        return {"tiles": tiles, "counts": tile_counts}

    @app.get("/api/pipeline/boundaries")
    async def api_pipeline_boundaries(
        country: str = "US",
        region: str = "AK"
    ):
        """Returns verified boundary bounding boxes for territory canvas framing."""
        try:
            boxes = get_region_bounding_boxes(country, region)
            return [b.to_dict() for b in boxes]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/pipeline/exports")
    async def api_pipeline_exports():
        """Returns list of organized state exports and their run summaries."""
        exports = list_all_state_exports(cfg.absolute_exports_dir)
        return {"exports": exports}

    @app.get("/api/pipeline/exports/download")
    async def api_pipeline_export_download(path: str = Query(..., description="Relative export file path")):
        """Safe export file download guarded against directory traversal."""
        try:
            file_path = resolve_safe_export_path(cfg.absolute_exports_dir, path)
        except PermissionError as pe:
            raise HTTPException(status_code=403, detail=str(pe))

        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Requested export file does not exist.")

        media_type = "text/csv" if file_path.suffix == ".csv" else "application/json" if file_path.suffix == ".json" else "text/plain"
        return FileResponse(
            str(file_path),
            media_type=media_type,
            filename=file_path.name
        )

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
