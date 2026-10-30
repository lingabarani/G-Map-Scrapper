import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union
import typer
from rich.console import Console, Group
from rich.table import Table
from rich.live import Live
from rich.text import Text
from rich import box

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .config import load_config
from .db import Database
from .logging_setup import setup_logging
from .export import export_data
from .checkpoint import CheckpointManager
from .models import PlaceStub
from .search import build_search_pb, parse_search_rpc_response
import signal

def _sigint_handler(signum, frame):
    try:
        console.print("\n[yellow]Shutdown signal received. Database flushed. To resume, run:[/]\n[bold cyan]  python -m gmaps_scraper resume[/]")
    except Exception:
        pass
    sys.exit(0)

try:
    signal.signal(signal.SIGINT, _sigint_handler)
except Exception:
    pass
from .details import parse_place_details_response
from .reviews import build_boq_reviews_url, parse_reviews_response
from .photos import download_photo_file
from .http import HttpClient
from .tiling import recursive_tile_search

app = typer.Typer(help="Google Maps Scraper - 100% Local Scraper & Web Dashboard")
console = Console(safe_box=True)

@app.command()
def init(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to custom user.yaml")
):
    """Initialize local SQLite database schema and directories."""
    cfg = load_config(config_path)
    logger = setup_logging(cfg.absolute_logs_dir)
    cfg.absolute_raw_dir.mkdir(parents=True, exist_ok=True)
    cfg.absolute_photos_dir.mkdir(parents=True, exist_ok=True)
    cfg.absolute_exports_dir.mkdir(parents=True, exist_ok=True)
    cfg.absolute_logs_dir.mkdir(parents=True, exist_ok=True)

    db = Database(cfg.absolute_db_path)
    db.init_schema()
    console.print(f"[bold green][OK] Initialized database and schema at:[/] {cfg.absolute_db_path}")

@app.command()
def search(
    query: str = typer.Option(..., "--query", "-q", help="Search query (e.g. 'coffee')"),
    bbox: str = typer.Option(..., "--bbox", "-b", help="Bounding box format: 'lat,lng,span_deg' (e.g. '40.70,-74.02,0.02')"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Tile discovery: recursive subdivision and stub collection."""
    cfg = load_config(config_path)
    logger = setup_logging(cfg.absolute_logs_dir)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    parts = [float(p.strip()) for p in bbox.split(",")]
    lat, lng, span = parts[0], parts[1], parts[2]

    console.print(f"[bold cyan]Starting tile discovery for '{query}' at ({lat}, {lng}) span={span}...[/]")

    async def run_search():
        http_client = HttpClient(
            user_agent=cfg.user_agent,
            accept_language=cfg.accept_language,
            hl=cfg.hl,
            gl=cfg.gl,
            concurrency=cfg.concurrency,
            per_host_rate=cfg.per_host_rate,
            max_retries=cfg.max_retries,
            backoff_base=cfg.backoff_base,
            proxies=cfg.proxies
        )

        async def fetch_tile(t_lat: float, t_lng: float, span_meters: int):
            pb = build_search_pb(t_lat, t_lng, span_meters=span_meters, num_results=20)
            url = f"https://www.google.com/search?tbm=map&authuser=0&hl={cfg.hl}&gl={cfg.gl}&pb={pb}&q={query}"
            resp = await http_client.get(url)
            # Cache raw response
            cache_file = cfg.absolute_raw_dir / f"search_{int(t_lat*1000)}_{int(t_lng*1000)}.json"
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(resp.text)
            stubs = parse_search_rpc_response(resp.text)
            return stubs

        stubs = await recursive_tile_search(
            fetch_tile,
            center_lat=lat,
            center_lng=lng,
            initial_span_deg=span,
            max_depth=cfg.tile_max_depth,
            overlap_pct=cfg.tile_overlap_pct,
            on_tile_complete=lambda tiles, places: console.print(f"  Processed {tiles} tiles | Found {places} unique places", end="\r")
        )

        await http_client.close()
        inserted = 0
        for s in stubs:
            if db.upsert_place_stub(s.model_dump()):
                inserted += 1

        console.print(f"\n[bold green][OK] Discovery complete:[/] {len(stubs)} places collected ({inserted} new inserted).")

    asyncio.run(run_search())

@app.command()
def enrich(
    limit: int = typer.Option(500, "--limit", "-l", help="Max places to enrich in this run"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Place details enrichment: fetches hours, attributes, popular times."""
    cfg = load_config(config_path)
    logger = setup_logging(cfg.absolute_logs_dir)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    with db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM places WHERE status = 'pending' LIMIT ?", (limit,)).fetchall()
        places = [dict(r) for r in rows]

    if not places:
        console.print("[yellow]No pending places to enrich.[/]")
        return

    console.print(f"[bold cyan]Enriching {len(places)} pending places...[/]")

    async def run_enrich():
        http_client = HttpClient(
            user_agent=cfg.user_agent,
            accept_language=cfg.accept_language,
            hl=cfg.hl,
            gl=cfg.gl,
            concurrency=cfg.concurrency,
            per_host_rate=cfg.per_host_rate,
            max_retries=cfg.max_retries,
            backoff_base=cfg.backoff_base,
            proxies=cfg.proxies
        )

        success = 0
        for idx, p in enumerate(places, 1):
            cid = p["cid"]
            lat = p["latitude"] or 40.70
            lng = p["longitude"] or -74.02
            pb = f"!4m8!1m3!1d500!2d{lng}!3d{lat}!3m2!1i1024!2i768!4f13.1!7i20!10b1"
            url = f"https://www.google.com/search?tbm=map&authuser=0&hl={cfg.hl}&gl={cfg.gl}&pb={pb}&q={p['name']}"

            try:
                resp = await http_client.get(url)
                details = parse_place_details_response(resp.text, cid)
                if details:
                    db.update_place_details(cid, details, status="details_done")
                    success += 1
                else:
                    db.mark_place_failed(cid, "Empty details parsed")
            except Exception as e:
                db.mark_place_failed(cid, str(e))

            console.print(f"  Enriching [{idx}/{len(places)}] (Success: {success})", end="\r")

        await http_client.close()
        console.print(f"\n[bold green][OK] Enrichment complete:[/] {success}/{len(places)} places enriched.")

    asyncio.run(run_enrich())

@app.command()
def reviews(
    max_per_place: int = typer.Option(100, "--max-per-place", help="Max reviews per place"),
    limit_places: int = typer.Option(50, "--limit-places", help="Max places to process"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Review pagination loop: extracts ratings, author, relative dates, text, owner replies."""
    cfg = load_config(config_path)
    logger = setup_logging(cfg.absolute_logs_dir)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    with db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM places WHERE status = 'details_done' LIMIT ?", (limit_places,)).fetchall()
        places = [dict(r) for r in rows]

    if not places:
        console.print("[yellow]No places ready for reviews extraction.[/]")
        return

    console.print(f"[bold cyan]Fetching reviews for up to {len(places)} places...[/]")

    async def run_reviews():
        http_client = HttpClient(
            user_agent=cfg.user_agent,
            accept_language=cfg.accept_language,
            hl=cfg.hl,
            gl=cfg.gl,
            concurrency=cfg.concurrency,
            per_host_rate=cfg.per_host_rate,
            max_retries=cfg.max_retries,
            backoff_base=cfg.backoff_base,
            proxies=cfg.proxies
        )

        total_revs = 0
        for p in places:
            cid = p["cid"]
            ftid = p["ftid"]
            if not ftid:
                continue

            token = None
            place_revs = 0
            while place_revs < max_per_place:
                url = build_boq_reviews_url(ftid, page_size=10, sort_code=1, pagination_token=token)
                try:
                    resp = await http_client.get(url)
                    parsed_revs, next_token = parse_reviews_response(resp.text, cid)
                    if not parsed_revs:
                        break
                    for r in parsed_revs:
                        db.insert_review(r)
                        place_revs += 1
                        total_revs += 1
                    if not next_token or next_token == token:
                        break
                    token = next_token
                except Exception as e:
                    break

            with db.get_connection() as conn:
                conn.execute("UPDATE places SET status = 'reviews_done' WHERE cid = ?", (cid,))
                conn.commit()

        await http_client.close()
        console.print(f"\n[bold green][OK] Reviews complete:[/] Extracted {total_revs} reviews.")

    asyncio.run(run_reviews())

@app.command()
def photos(
    limit: int = typer.Option(50, "--limit", help="Max photos to download"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Photo downloader: downloads photos locally to data/photos/{cid}/."""
    cfg = load_config(config_path)
    logger = setup_logging(cfg.absolute_logs_dir)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    with db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM photos WHERE local_path IS NULL LIMIT ?", (limit,)).fetchall()
        photos_to_dl = [dict(r) for r in rows]

    if not photos_to_dl:
        console.print("[yellow]No pending photos to download.[/]")
        return

    console.print(f"[bold cyan]Downloading {len(photos_to_dl)} photos...[/]")

    async def run_photos():
        import httpx
        client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        downloaded = 0

        for ph in photos_to_dl:
            cid = ph["cid"]
            out_dir = cfg.absolute_photos_dir / cid
            res = await download_photo_file(client, ph["photo_url"], out_dir, cfg.photo_max_width, attribution=ph.get("attribution"))
            if res:
                path, size = res
                with db.get_connection() as conn:
                    conn.execute("UPDATE photos SET local_path = ?, file_size = ? WHERE id = ?", (path, size, ph["id"]))
                    conn.commit()
                downloaded += 1

        await client.aclose()
        console.print(f"[bold green][OK] Photos complete:[/] Downloaded {downloaded} photos locally.")

    asyncio.run(run_photos())

@app.command()
def export(
    format: str = typer.Option("csv", "--format", "-f", help="Export format: csv, jsonl, or parquet"),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Target output directory"),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Name prefix for export"),
    full: bool = typer.Option(False, "--full", help="Export full schema columns and derived dimensions"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Export places, reviews, hours, attributes, photos to files."""
    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    target_dir = Path(out) if out else cfg.absolute_exports_dir

    res = export_data(db, export_format=format, output_dir=target_dir, query_name=query, full=full)
    console.print(f"[bold green][OK] Export successful:[/] {res['places_count']} places written to {target_dir} (full={full})")
    for f in res["files"]:
        console.print(f"  - {f}")

@app.command()
def reclassify(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Re-derive vertical and country/location dimensions for all existing rows."""
    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    console.print("[bold cyan]Reclassifying business verticals and country dimensions...[/]")
    res = db.reclassify_all()
    console.print(f"[bold green][OK] Reclassification complete:[/] {res['total']} places inspected, {res['changed']} records updated.")

@app.command()
def run(
    query: str = typer.Option(..., "--query", "-q", help="Search query"),
    bbox: str = typer.Option(..., "--bbox", "-b", help="Bounding box: 'lat,lng,span'"),
    max_reviews: int = typer.Option(50, "--max-reviews", help="Max reviews per place"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Execute full pipeline: search -> details -> reviews -> photos -> export."""
    console.print(f"[bold magenta][>] Executing Full Scraper Pipeline for '{query}'...[/]")
    search(query=query, bbox=bbox, config_path=config_path)
    enrich(limit=500, config_path=config_path)
    reviews(max_per_place=max_reviews, limit_places=50, config_path=config_path)
    photos(limit=50, config_path=config_path)
    export(format="csv", out=None, query=query.replace(" ", "_"), config_path=config_path)
    console.print("[bold green][OK] Full Pipeline Run Completed Successfully![/]")

@app.command()
def resume(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Resume incomplete pipeline tasks."""
    console.print("[bold cyan]Resuming incomplete tasks...[/]")
    enrich(limit=500, config_path=config_path)
    reviews(max_per_place=50, limit_places=50, config_path=config_path)
    photos(limit=50, config_path=config_path)
    console.print("[bold green][OK] Resume complete.[/]")

@app.command()
def stats(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Print scraper statistics and database metrics."""
    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    s = db.get_stats()

    table = Table(title="Google Maps Scraper - Local Database Stats")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")

    if "raw_places_count" in s:
        table.add_row("Raw Discoveries (raw_places)", str(s["raw_places_count"]))
    table.add_row("Canonical Places (places)", str(s["total_places"]))
    table.add_row("Enriched Places", str(s["enriched_places"]))
    table.add_row("Pending Places", str(s["pending_places"]))
    table.add_row("Failed Places", str(s["failed_places"]))
    table.add_row("Customer Reviews", str(s["total_reviews"]))
    table.add_row("Local Photos", str(s["total_photos"]))

    # Status breakdown
    if s.get("status_breakdown"):
        for st_name, st_cnt in s["status_breakdown"].items():
            table.add_row(f"  status: {st_name}", str(st_cnt))

    console.print(table)

    if s.get("last_run"):
        lr = s["last_run"]
        r_table = Table(title="Last Run State")
        r_table.add_column("Property", style="magenta")
        r_table.add_column("Value", style="yellow")
        r_table.add_row("Run ID", str(lr.get("run_id", "N/A")))
        r_table.add_row("Query", str(lr.get("query", "N/A")))
        r_table.add_row("Status", str(lr.get("status", "N/A")))
        r_table.add_row("Current Step", str(lr.get("current_step", "N/A")))
        r_table.add_row("Errors Count", str(lr.get("errors_count", 0)))
        r_table.add_row("Discovered", str(lr.get("total_discovered", 0)))
        r_table.add_row("Enriched", str(lr.get("total_enriched", 0)))
        console.print(r_table)

@app.command()
def serve(
    port: int = typer.Option(8765, "--port", "-p", help="Port to bind server (default 8765)"),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host interface (must be 127.0.0.1 for 100% local)"),
    demo: bool = typer.Option(False, "--demo", help="Seed sample rows if DB is empty"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Launch the 100% local web dashboard on 127.0.0.1."""
    import uvicorn
    from .web.app import create_app
    from .web.demo_data import seed_demo_data

    cfg = load_config(config_path)
    cfg.web_port = port
    cfg.web_host = host
    logger = setup_logging(cfg.absolute_logs_dir)

    db = Database(cfg.absolute_db_path)
    db.init_schema()

    if demo:
        console.print("[cyan]Checking and seeding demo data if database is empty...[/]")
        seed_demo_data(db, photos_dir=cfg.absolute_photos_dir)

    console.print(f"[bold green]Starting Local Web Dashboard at:[/] [bold underline]http://{host}:{port}[/]")
    console.print("[dim]100% local server, zero CDNs, zero cloud dependencies. Press Ctrl+C to stop.[/]")

    fastapi_app = create_app(cfg)
    uvicorn.run(fastapi_app, host=host, port=port, log_level="warning")

@app.command()
def resolve(
    location: str = typer.Option(..., "--location", "-l", help="Location name, coords, or bbox"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Resolves a geographic location without API keys via Google Maps RPC."""
    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    from .geo.resolve import resolve_location
    try:
        res = asyncio.run(resolve_location(location, db=db))
        table = Table(title=f"Resolved Location: {location}")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Label", str(res["label"]))
        table.add_row("Latitude", f"{res['lat']:.5f}")
        table.add_row("Longitude", f"{res['lng']:.5f}")
        table.add_row("Span (deg)", f"{res['span_deg']}°")
        table.add_row("Feature Class", str(res.get("feature_class", "N/A")))
        table.add_row("Country", f"{res.get('country')} ({res.get('country_code')})")
        table.add_row("Resolved From", str(res.get("resolved_from")))
        table.add_row("Est. Tiles", str(res.get("estimated_tiles")))
        table.add_row("Est. Requests", str(res.get("estimated_requests")))
        table.add_row("Est. Time", f"~{res.get('estimated_seconds')}s")
        console.print(table)
    except Exception as e:
        console.print(f"[bold red]Resolution failed:[/] {e}")
        raise typer.Exit(code=1)

@app.command()
def live(
    query: str = typer.Option(..., "--query", "-q", help="Business type or query (e.g. 'hospital')"),
    location: str = typer.Option(..., "--location", "-l", help="Location (e.g. 'Chicago' or '40.71,-74.00@5km')"),
    max_places: int = typer.Option(200, "--max-places", help="Maximum places to find"),
    max_reviews: int = typer.Option(50, "--max-reviews", help="Max reviews per place"),
    photos: bool = typer.Option(False, "--photos", help="Download place photos locally"),
    enrich: bool = typer.Option(True, "--enrich/--no-enrich", help="Enrich place details (hours, attributes)"),
    reviews: bool = typer.Option(True, "--reviews/--no-reviews", help="Fetch user reviews"),
    depth: int = typer.Option(3, "--depth", help="Maximum tiling depth"),
    span: Optional[float] = typer.Option(None, "--span", help="Override span in degrees"),
    follow: bool = typer.Option(False, "--follow", help="Stream progress and discovered places live to terminal"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Run an on-demand live search & scrape job."""
    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    from .jobs import JobManager
    job_mgr = JobManager.get_instance(db, cfg)

    options = {
        "max_places": max_places,
        "max_reviews_per_place": max_reviews,
        "download_photos": photos,
        "enrich_details": enrich,
        "fetch_reviews": reviews,
        "max_depth": depth,
        "span_override_deg": span
    }

    job_id = job_mgr.create_job(query=query, location=location, options=options)
    console.print(f"[bold green]Created job:[/] [bold cyan]{job_id}[/] ('{query}' in {location})")

    if not follow:
        console.print(f"Job is running in background worker. Check status with: [cyan]python -m gmaps_scraper jobs[/]")
        return

    async def stream_terminal():
        queue = job_mgr.subscribe(job_id)
        console.print(f"[bold yellow]Streaming live progress for job {job_id}... (Ctrl+C to cancel)[/]")
        places_count = 0
        try:
            while True:
                msg = await queue.get()
                ev_type = msg.get("event")
                data = msg.get("data", {})
                if ev_type == "stage":
                    console.print(f"  [bold magenta]Stage:[/] {data.get('stage')} - {data.get('message', '')}")
                elif ev_type == "place":
                    places_count += 1
                    console.print(f"  [green]✓ Place {places_count}:[/] [bold]{data.get('name')}[/] ({data.get('category') or 'place'}) - {data.get('address')}")
                elif ev_type == "progress":
                    done = data.get("done", 0)
                    total = data.get("total", 0)
                    console.print(f"  [dim]Progress: {done}/{total} | Found: {data.get('places_found', places_count)} | ETA: {data.get('eta_s', 0)}s[/]", end="\r")
                elif ev_type == "error":
                    console.print(f"  [bold red]Error:[/] {data.get('message')}")
                    break
                elif ev_type == "done":
                    console.print(f"\n[bold green]Job {job_id} finished:[/] status={data.get('status')} | {data.get('places_found', places_count)} places found.")
                    break
        finally:
            job_mgr.unsubscribe(job_id, queue)

    try:
        asyncio.run(stream_terminal())
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelling job...[/]")
        job_mgr.cancel_job(job_id)

@app.command()
def jobs(
    cancel: Optional[str] = typer.Option(None, "--cancel", help="Job ID to cancel"),
    limit: int = typer.Option(20, "--limit", help="Max jobs to display"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """List recent scrape jobs or cancel a job."""
    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    if cancel:
        from .jobs import JobManager
        mgr = JobManager.get_instance(db, cfg)
        success = mgr.cancel_job(cancel)
        if success:
            console.print(f"[bold green]Job {cancel} cancelled successfully.[/]")
        else:
            console.print(f"[bold yellow]Job {cancel} could not be cancelled (already completed or not found).[/]")
        return

    job_list = db.list_jobs(limit=limit)
    if not job_list:
        console.print("[dim]No jobs found in database.[/]")
        return

    table = Table(title="Recent Live Scrape Jobs")
    table.add_column("Job ID", style="cyan")
    table.add_column("Query", style="white")
    table.add_column("Location", style="yellow")
    table.add_column("Status", style="magenta")
    table.add_column("Places", style="green")
    table.add_column("Created At", style="dim")

    for j in job_list:
        table.add_row(
            j["id"],
            j.get("query") or "N/A",
            j.get("location_input") or "N/A",
            j.get("status") or "N/A",
            str(j.get("places_found", 0)),
            str(j.get("created_at") or "")
        )
    console.print(table)

@app.command()
def scope(
    country: Optional[str] = typer.Option(None, "--country", "-C", help="Filter by country code: 'US' or 'MX'"),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Filter by region code (e.g. 'CA', 'TX', 'JAL')"),
    summary_only: bool = typer.Option(False, "--summary", "-s", help="Display only entity counts summary"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Inspect Pipeline 2 geographic scope (US States and Mexico Federal Entities)."""
    from .scope import get_target_countries, get_us_states, get_mexico_entities, validate_target_country, get_regions_for_country

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    us_states = get_us_states()
    mx_entities = get_mexico_entities()

    if summary_only or (not country and not region):
        table = Table(title="Pipeline 2 Target Scope Summary")
        table.add_column("Country Code", style="cyan")
        table.add_column("Country Name", style="white")
        table.add_column("Configured Entities", style="green", justify="right")
        table.add_row("US", "United States", f"{len(us_states)} States")
        table.add_row("MX", "Mexico", f"{len(mx_entities)} Federal Entities")
        console.print(table)
        if summary_only:
            return

    countries_to_show = []
    if country:
        validated_c = validate_target_country(country)
        countries_to_show = [validated_c]
    else:
        countries_to_show = ["US", "MX"]

    for c in countries_to_show:
        regions = get_regions_for_country(c)
        if region:
            reg_upper = region.strip().upper()
            if reg_upper in regions:
                regions = {reg_upper: regions[reg_upper]}
            elif c == country:
                console.print(f"[bold red]Region '{region}' not found in country '{c}'![/]")
                raise typer.Exit(code=1)
            else:
                continue

        c_name = "United States (50 States)" if c == "US" else "Mexico (32 Federal Entities)"
        r_table = Table(title=f"Pipeline 2 Scope: {c_name}")
        r_table.add_column("Code", style="cyan", width=8)
        r_table.add_column("Name", style="white")
        r_table.add_column("Total Tasks", style="yellow", justify="right")
        r_table.add_column("Completed", style="green", justify="right")
        r_table.add_column("Pending/Failed", style="magenta", justify="right")
        r_table.add_column("State Complete?", style="bold", justify="center")

        for code, reg_info in sorted(regions.items()):
            prog = db.get_region_progress(c, code)
            is_comp = "[green]YES[/]" if prog["is_complete"] else "[dim]NO[/]"
            pending_str = f"{prog['pending_tasks']}/{prog['failed_tasks']}"
            r_table.add_row(code, reg_info.name, str(prog["total_tasks"]), str(prog["completed_tasks"]), pending_str, is_comp)

        console.print(r_table)

@app.command("website-discover")
def website_discover(
    country: str = typer.Option(..., "--country", "-C", help="Target country code: 'US' or 'MX'"),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Filter by region/state code (e.g. 'WY', 'NY', 'JAL')"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max businesses to process in this run"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry previously failed or unverified website tasks"),
    force: bool = typer.Option(False, "--force", help="Re-verify already verified records"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry-run mode: evaluate candidates without writing to database"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Pipeline 2 Step 2: Discover and verify official websites for businesses."""
    from .website.service import WebsiteDiscoveryService
    from .scope import validate_target_country, get_region

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    try:
        c_code = validate_target_country(country)
        if region:
            reg_info = get_region(c_code, region)
            reg_label = f"{reg_info.name} ({reg_info.code})"
        else:
            reg_label = "All configured regions"
    except ValueError as e:
        console.print(f"[bold red]Scope Error:[/] {e}")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Starting Official Website Discovery for {c_code} - {reg_label}...[/]")
    if dry_run:
        console.print("[bold yellow]Mode: DRY-RUN (Database will not be modified)[/]")

    service = WebsiteDiscoveryService(db, user_agent=cfg.user_agent)

    def on_progress(item: Dict[str, Any]):
        status_color = "green" if item["status"] == "verified" else "yellow" if item["status"] == "needs_review" else "red"
        cand_str = item.get("candidate") or "[dim]None[/]"
        console.print(f"  [{status_color}][{item['status'].upper()}][/] {item['name']} -> {cand_str}")

    res = asyncio.run(service.run_discovery(
        country_code=c_code,
        region_code=region,
        limit=limit,
        retry_failed=retry_failed,
        force=force,
        dry_run=dry_run,
        on_progress=on_progress
    ))

    table = Table(title=f"Website Discovery Summary ({c_code} - {reg_label})")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    table.add_row("Businesses Inspected", str(res["total_inspected"]))
    table.add_row("Verified Websites", str(res["counts"].get("verified", 0)))
    table.add_row("Needs Review", str(res["counts"].get("needs_review", 0)))
    table.add_row("Website Missing", str(res["counts"].get("website_missing", 0)))
    table.add_row("Failed / Inaccessible", str(res["counts"].get("failed", 0)))
    console.print(table)

@app.command("website-enrich")
def website_enrich(
    country: str = typer.Option(..., "--country", "-C", help="Target country code: 'US' or 'MX'"),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Filter by region/state code (e.g. 'WY', 'NY', 'JAL')"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max businesses to process in this run"),
    max_pages: int = typer.Option(5, "--max-pages", help="Maximum pages crawled per website"),
    incremental: bool = typer.Option(False, "--incremental", help="Execute incremental intelligence comparison and source-adapter enrichment"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry previously failed extraction tasks"),
    force: bool = typer.Option(False, "--force", help="Re-extract for already completed records"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry-run mode: extract data without writing to database"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Pipeline 2 Step 3: Extract company info, leadership, and contacts from verified websites."""
    if incremental:
        return incremental_enrich(
            country=country,
            region=region,
            limit=limit,
            max_pages=max_pages,
            source_types="all",
            retry_failed=retry_failed,
            force=force,
            dry_run=dry_run,
            export=True,
            config_path=config_path
        )

    from .website.enrichment_service import WebsiteEnrichmentService
    from .scope import validate_target_country, get_region

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    try:
        c_code = validate_target_country(country)
        if region:
            reg_info = get_region(c_code, region)
            reg_label = f"{reg_info.name} ({reg_info.code})"
        else:
            reg_label = "All configured regions"
    except ValueError as e:
        console.print(f"[bold red]Scope Error:[/] {e}")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Starting Website Information Extraction for {c_code} - {reg_label}...[/]")
    if dry_run:
        console.print("[bold yellow]Mode: DRY-RUN (Database will not be modified)[/]")

    service = WebsiteEnrichmentService(db, max_pages=max_pages, user_agent=cfg.user_agent)

    def on_progress(item: Dict[str, Any]):
        status_color = "green" if item["status"] == "completed" else "yellow" if item["status"] == "partial" else "red"
        leader_s = f"{item['leaders_count']} leaders"
        contact_s = f"{item['contacts_count']} contacts"
        console.print(f"  [{status_color}][{item['status'].upper()}][/] {item['name']} -> {leader_s}, {contact_s}")

    res = asyncio.run(service.run_enrichment(
        country_code=c_code,
        region_code=region,
        limit=limit,
        max_pages=max_pages,
        retry_failed=retry_failed,
        force=force,
        dry_run=dry_run,
        on_progress=on_progress
    ))

    counts = res["counts"]
    table = Table(title=f"Website Information Extraction Summary ({c_code} - {reg_label})")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    table.add_row("Businesses Processed", str(counts["total_selected"]))
    table.add_row("Fully Completed", str(counts.get("completed", 0)))
    table.add_row("Partially Extracted", str(counts.get("partial", 0)))
    table.add_row("Failed / Inaccessible", str(counts.get("failed", 0)))
    table.add_row("Total Leaders Extracted", str(counts.get("total_leaders_extracted", 0)))
    table.add_row("Total Contacts Extracted", str(counts.get("total_contacts_extracted", 0)))
    console.print(table)


@app.command("incremental-enrich")
def incremental_enrich(
    country: str = typer.Option(..., "--country", "-C", help="Target country code: 'US' or 'MX'"),
    region: Optional[str] = typer.Option(None, "--region", "-r", help="Filter by region/state code (e.g. 'AK', 'WY', 'NY')"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max businesses to process"),
    max_pages: int = typer.Option(5, "--max-pages", help="Maximum pages crawled per website"),
    source_types: str = typer.Option("all", "--source-types", "-s", help="Comma-separated source types ('all', 'website', 'registry', 'profile', 'open')"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry previously failed tasks"),
    force: bool = typer.Option(False, "--force", help="Re-evaluate already completed businesses"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry-run mode: extract data without writing to database"),
    export: bool = typer.Option(True, "--export/--no-export", help="Generate additional_enrichment.csv and summary json"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Incremental Business Intelligence: Discover, compare against DB, avoid duplicates, retain source evidence."""
    from .incremental.service import IncrementalEnrichmentService
    from .incremental.export import export_additional_intelligence
    from .scope import validate_target_country, get_region

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    try:
        c_code = validate_target_country(country)
        reg_info = get_region(c_code, region) if region else None
        reg_label = f"{reg_info.name} ({reg_info.code})" if reg_info else "All configured regions"
    except ValueError as e:
        console.print(f"[bold red]Scope Error:[/] {e}")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Starting Incremental Intelligence Enrichment for {c_code} - {reg_label}...[/]")
    if dry_run:
        console.print("[bold yellow]Mode: DRY-RUN (Database will NOT be modified)[/]")

    src_list = [s.strip() for s in source_types.split(",") if s.strip()]
    service = IncrementalEnrichmentService(
        db=db,
        max_pages=max_pages,
        timeout_seconds=cfg.timeout if hasattr(cfg, "timeout") else 10.0,
        user_agent=cfg.user_agent,
        source_types=src_list
    )

    def on_progress(p: Dict[str, Any]):
        b_name = p.get("company_name", "Business")
        new_cnt = p.get("new_findings", 0)
        match_cnt = p.get("exact_matches", 0)
        conf_cnt = p.get("conflicts", 0)
        console.print(
            f"  [cyan]{b_name}[/] -> "
            f"[green]{new_cnt} new[/], "
            f"[dim]{match_cnt} matched/deduped[/], "
            f"[yellow]{conf_cnt} conflicts[/]"
        )

    res = asyncio.run(service.run_incremental_enrichment(
        country_code=c_code,
        region_code=region,
        limit=limit,
        max_pages=max_pages,
        retry_failed=retry_failed,
        force=force,
        dry_run=dry_run,
        on_progress=on_progress
    ))

    counts = res["counts"]
    table = Table(title=f"Incremental Business Intelligence Summary ({c_code} - {reg_label})")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    table.add_row("Businesses Inspected", str(counts.get("businesses_inspected", 0)))
    table.add_row("Sources Attempted", str(counts.get("sources_attempted", 0)))
    table.add_row("Sources Succeeded", str(counts.get("sources_succeeded", 0)))
    table.add_row("Sources with No Data", str(counts.get("sources_no_data", 0)))
    table.add_row("Sources Failed / Inaccessible", str(counts.get("sources_failed", 0)))
    table.add_row("Genuinely New Findings", f"[bold green]{counts.get('new_findings', 0)}[/]")
    table.add_row("Existing Matches (Duplicates Prevented)", f"[blue]{counts.get('existing_matches', 0)}[/]")
    table.add_row("Conflicts Flagged for Review", f"[bold red]{counts.get('conflicts', 0)}[/]")
    table.add_row("Possible Matches Requiring Review", f"[yellow]{counts.get('possible_matches', 0)}[/]")
    table.add_row("Newly Discovered Leaders", str(counts.get("new_leaders", 0)))
    table.add_row("Newly Discovered Contacts", str(counts.get("new_contacts", 0)))
    console.print(table)

    # Export
    if export and region:
        r_name = reg_info.name if reg_info else region
        export_res = export_additional_intelligence(
            db=db,
            base_exports_dir=cfg.absolute_exports_dir,
            country_code=c_code,
            region_code=region,
            region_name=r_name,
            in_memory_findings=res["findings"] if dry_run else None,
            summary_counts=counts,
            dry_run=dry_run
        )
        console.print(f"[bold green][OK] Additional Enrichment Export Complete:[/] {export_res['csv_path']}")
        console.print(f"     Summary Report: {export_res['summary_path']}")

# -----------------------------------------------------------------------------
# Pipeline Orchestration Sub-Commands (pipeline run, plan, status, etc.)
# -----------------------------------------------------------------------------

pipeline_app = typer.Typer(help="Automated State-by-State Pipeline Orchestrator (Stages 1, 2, and 3)")
app.add_typer(pipeline_app, name="pipeline")


def _parse_stage_flags(stages_str: str) -> Dict[str, bool]:
    s = stages_str.lower().strip()
    if s in ("all", "1,2,3", "123", "1-3"):
        return {"collection": True, "discovery": True, "enrichment": True}
    return {
        "collection": "1" in s or "coll" in s,
        "discovery": "2" in s or "disc" in s,
        "enrichment": "3" in s or "enrich" in s,
    }


@pipeline_app.command("plan")
def pipeline_plan(
    country: str = typer.Option("both", "--country", "-C", help="Target country: 'US', 'MX', or 'both'"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Comma-separated region codes (e.g. 'CA,TX,JAL')"),
    categories: Optional[str] = typer.Option(None, "--categories", help="Comma-separated categories (e.g. 'healthcare,food')"),
    category_workers: int = typer.Option(3, "--category-workers", "-w", help="Concurrent workers processing business categories"),
    request_concurrency: int = typer.Option(3, "--request-concurrency", help="Maximum shared concurrent HTTP requests across workers"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max places per region/category task (omit or 0 for unlimited)"),
    batch_size: int = typer.Option(50, "--batch-size", help="Batch size for website discovery & enrichment"),
    max_pages: int = typer.Option(5, "--max-pages", help="Max crawl pages per website during enrichment"),
    stages: str = typer.Option("1,2,3", "--stages", help="Enabled stages: '1,2,3' or comma-separated"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run simulation mode"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume from existing checkpoints"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry eligible failed tasks"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Preview the complete deterministic state-by-state execution order without running."""
    from .orchestrator import PipelineOrchestrator, PipelineConfig, DEFAULT_CATEGORIES

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    orchestrator = PipelineOrchestrator.get_instance(db, cfg)

    region_list = [r.strip().upper() for r in regions.split(",") if r.strip()] if regions else None
    cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else list(DEFAULT_CATEGORIES)
    stage_flags = _parse_stage_flags(stages)

    pipe_config = PipelineConfig(
        country=country,
        regions=region_list,
        categories=cat_list,
        category_workers=category_workers,
        request_concurrency=request_concurrency,
        collection_limit=limit,
        batch_size=batch_size,
        max_crawl_pages=max_pages,
        stages=stage_flags,
        resume_checkpoint=resume,
        retry_failed=retry_failed,
        dry_run=dry_run
    )

    plan = orchestrator.preview_plan(pipe_config)

    console.print("\n[bold cyan]═══════════════════════════════════════════════════════════════════════[/]")
    console.print("[bold green]          STATE-BY-STATE PIPELINE EXECUTION PLAN PREVIEW               [/]")
    console.print("[bold cyan]═══════════════════════════════════════════════════════════════════════[/]\n")

    summary_table = Table(title="Pipeline Execution Configuration & Scope")
    summary_table.add_column("Parameter", style="cyan")
    summary_table.add_column("Value", style="yellow")
    summary_table.add_row("Target Country", str(plan["scope_summary"]["countries"]))
    summary_table.add_row("Target Regions", f"{plan['scope_summary']['regions_count']} configured territories")
    summary_table.add_row("Categories", f"{plan['scope_summary']['categories_count']} categories: {', '.join(cat_list[:5])}{'...' if len(cat_list) > 5 else ''}")
    summary_table.add_row("Category Workers", f"{category_workers} concurrent workers per region")
    summary_table.add_row("Request Concurrency", f"{request_concurrency} shared HTTP requests max")
    summary_table.add_row("Enabled Stages", f"Stage 1 Collection: {stage_flags['collection']} | Stage 2 Discovery: {stage_flags['discovery']} | Stage 3 Enrichment: {stage_flags['enrichment']}")
    summary_table.add_row("Collection Limit / Task", f"{limit} records")
    summary_table.add_row("Enrichment Crawl Limit", f"{max_pages} pages per site")
    summary_table.add_row("Dry Run Mode", "[yellow]YES[/]" if dry_run else "[dim]NO[/]")
    summary_table.add_row("Checkpoint Resume", "[green]ENABLED[/]" if resume else "[dim]DISABLED[/]")
    summary_table.add_row("Total Stage 1 Tasks", str(plan["counts"]["total_stage1_tasks"]))
    summary_table.add_row("Already Completed Tasks", str(plan["counts"]["completed_stage1_tasks"]))
    summary_table.add_row("Failed Tasks", str(plan["counts"]["failed_stage1_tasks"]))
    console.print(summary_table)

    # Detailed Stage 1 Task Schedule Table (First 25 displayed if large)
    t1_list = plan["stage_1_tasks"]
    if t1_list and stage_flags["collection"]:
        t_table = Table(title="Stage 1 Deterministic Task Execution Schedule")
        t_table.add_column("#", style="dim", width=4)
        t_table.add_column("Country", style="cyan", width=8)
        t_table.add_column("Region Code", style="white", width=12)
        t_table.add_column("Region Name", style="white")
        t_table.add_column("Category", style="yellow")
        t_table.add_column("Status", style="magenta")
        t_table.add_column("Records", style="green", justify="right")

        for idx, t in enumerate(t1_list[:40], 1):
            st_color = "green" if t["status"] == "completed" else "yellow" if t["status"] == "in_progress" else "red" if t["status"] == "failed" else "dim"
            t_table.add_row(
                str(idx),
                t["country_code"],
                t["region_code"],
                t["region_name"],
                t["category"],
                f"[{st_color}]{t['status'].upper()}[/]",
                str(t.get("records_collected", 0))
            )

        if len(t1_list) > 40:
            t_table.add_row("...", "...", "...", f"... and {len(t1_list) - 40} more tasks ...", "...", "...", "...")
        console.print(t_table)

    console.print(f"\n[bold green]Plan valid:[/] To start this automated pipeline, run:\n[bold cyan]  python -m gmaps_scraper pipeline run --country {country}{' --dry-run' if dry_run else ''}[/]\n")


@pipeline_app.command("run")
def pipeline_run(
    country: str = typer.Option("both", "--country", "-C", help="Target country: 'US', 'MX', or 'both'"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Comma-separated region codes (e.g. 'CA,TX,JAL')"),
    categories: Optional[str] = typer.Option(None, "--categories", help="Comma-separated categories (e.g. 'healthcare,food')"),
    category_workers: str = typer.Option("3", "--category-workers", "-w", help="Concurrent workers processing business categories. Pass number, 'all', or '0' for all configured categories"),
    all_categories_concurrent: bool = typer.Option(False, "--all-categories-concurrent", "--all-categories", help="Run ALL configured business categories concurrently within the current state"),
    request_concurrency: int = typer.Option(3, "--request-concurrency", help="Maximum shared concurrent HTTP requests across workers"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max places per region/category task (omit or 0 for unlimited)"),
    batch_size: int = typer.Option(50, "--batch-size", help="Batch size for website discovery & enrichment"),
    max_pages: int = typer.Option(5, "--max-pages", help="Max crawl pages per website during enrichment"),
    stages: str = typer.Option("1,2,3", "--stages", help="Enabled stages: '1,2,3' or comma-separated"),
    collection_only: bool = typer.Option(False, "--collection-only", help="Run scraping collection only; do not run deduplication or downstream jobs"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run simulation mode"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume from existing checkpoints"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry eligible failed tasks"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream live progress in terminal"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Execute fully automated state-by-state pipeline across all configured regions and categories."""
    from .orchestrator import PipelineOrchestrator, PipelineConfig, DEFAULT_CATEGORIES

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    orchestrator = PipelineOrchestrator.get_instance(db, cfg)

    region_list = [r.strip().upper() for r in regions.split(",") if r.strip()] if regions else None
    cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else list(DEFAULT_CATEGORIES)
    stage_flags = _parse_stage_flags(stages)

    is_all_concurrent = (
        all_categories_concurrent
        or str(category_workers).strip().lower() in ("all", "0", "max", "-1", "unlimited")
        or (isinstance(category_workers, (int, float)) and category_workers <= 0)
    )
    if is_all_concurrent:
        eff_workers = "all"
    else:
        try:
            eff_workers = int(category_workers)
        except (ValueError, TypeError):
            eff_workers = "all"
            is_all_concurrent = True

    pipe_config = PipelineConfig(
        country=country,
        regions=region_list,
        categories=cat_list,
        category_workers=eff_workers,
        all_categories_concurrent=is_all_concurrent,
        request_concurrency=request_concurrency,
        collection_limit=limit,
        batch_size=batch_size,
        max_crawl_pages=max_pages,
        stages=stage_flags,
        collection_only=collection_only,
        resume_checkpoint=resume,
        retry_failed=retry_failed,
        dry_run=dry_run
    )

    console.print(f"[bold magenta]Starting Automated Pipeline Orchestrator ({'DRY-RUN' if dry_run else 'LIVE'})...[/]")

    async def run_and_stream():
        try:
            run_id = await orchestrator.start(pipe_config)
            console.print(f"[bold green]✓ Pipeline Run Started successfully:[/] [bold cyan]{run_id}[/]")
        except RuntimeError as e:
            console.print(f"[bold red]Cannot start pipeline:[/] {e}")
            raise typer.Exit(code=1)

        if not follow:
            console.print("[dim]Pipeline is running in background. Check status with: python -m gmaps_scraper pipeline status[/]")
            return

        q = orchestrator.subscribe()
        pipeline_start_time = time.time()
        initial_finished_tiles = None

        def build_dashboard_renderable():
            stats = orchestrator.get_live_collection_stats()

            finished = stats.get("finished_tiles", 0)
            nonlocal initial_finished_tiles
            if initial_finished_tiles is None:
                initial_finished_tiles = finished

            country = stats.get("country", "US")
            reg = stats.get("region", "NV")
            active_total = stats.get("active_total", 0)
            remaining = stats.get("remaining_tiles", 0)
            can_grow = stats.get("can_grow", False)
            raw_today = stats.get("raw_saved_today", 0)
            total_raw = stats.get("total_raw", 0)
            total_can = stats.get("total_canonical", 0)
            run_new = stats.get("run_new_records", 0)
            run_dups = stats.get("run_duplicate_records", 0)
            last_write = stats.get("last_db_write", "N/A")
            failed_tiles = stats.get("failed", 0)
            db_path = stats.get("db_path", "data/gmaps.sqlite")
            today_date = stats.get("today_date", datetime.now().strftime("%Y-%m-%d"))
            mode_label = "COLLECTION ONLY" if pipe_config.collection_only else "FULL PIPELINE"
            export_info = stats.get("export_summary", {})
            categories_data = stats.get("categories", {})

            now = time.time()
            elapsed_s = int(now - pipeline_start_time)
            elapsed_str = f"{elapsed_s // 60:02d}:{elapsed_s % 60:02d}"

            # Calculate defensible ETA only if enough progress and rate is measurable
            tiles_finished_in_run = finished - initial_finished_tiles
            eta_str = "dynamic"
            if elapsed_s >= 20 and tiles_finished_in_run >= 5:
                rate = tiles_finished_in_run / elapsed_s
                if rate > 0:
                    est_sec = int(remaining / rate)
                    prefix = "at least " if can_grow else "~"
                    eta_str = f"{prefix}{est_sec // 60:02d}:{est_sec % 60:02d}"

            table = Table(
                title=f"========== LIVE COLLECTION DASHBOARD ==========\nDate: {today_date} | Region: {country}-{reg} | Mode: {mode_label}",
                show_header=True,
                header_style="bold cyan",
                box=box.ASCII_DOUBLE_HEAD,
            )
            table.add_column("CATEGORY", style="cyan")
            table.add_column("TILE PROGRESS", justify="left")
            table.add_column("REMAINING", justify="left")
            table.add_column("RAW SAVED", justify="right")
            table.add_column("STATUS", justify="center")
            table.add_column("ACTIVE TILE / DISCOVERIES", justify="left")

            for cat_name, cdata in categories_data.items():
                c_fin = cdata.get("finished_tiles", 0)
                c_act = cdata.get("active_total", 0)
                c_rem = cdata.get("remaining_tiles", 0)
                c_can_grow = cdata.get("can_grow", False)
                c_status = cdata.get("status", "PENDING")
                c_saved = cdata.get("raw_saved") if cdata.get("raw_saved") is not None else cdata.get("records_collected", 0)
                c_tile = cdata.get("active_tile_id", "")
                c_recent = cdata.get("recent_names", [])

                c_pct = "growing" if c_can_grow else (f"{(c_fin / c_act * 100):.1f}%" if c_act > 0 else "0.0%")
                c_prog_str = f"{c_fin:,}/{c_act:,} ({c_pct})" if c_act > 0 else f"{c_fin:,} (0.0%)"
                c_rem_str = f">={c_rem:,}" if c_can_grow else f"{c_rem:,}"

                if c_status == "RUNNING":
                    st_styled = "[bold green]RUNNING[/]"
                elif c_status == "COMPLETED":
                    st_styled = "[bold blue]COMPLETED[/]"
                elif c_status == "FAILED":
                    st_styled = "[bold red]FAILED[/]"
                else:
                    st_styled = "[dim]PENDING[/]"

                recent_display = ", ".join(c_recent[:2]) if c_recent else ""
                if c_tile and recent_display:
                    disc_display = f"{c_tile} ({recent_display})"
                elif c_tile:
                    disc_display = c_tile
                elif recent_display:
                    disc_display = recent_display
                else:
                    disc_display = "-"

                table.add_row(
                    cat_name,
                    c_prog_str,
                    c_rem_str,
                    f"{c_saved:,}",
                    st_styled,
                    disc_display
                )

            elements = [
                table,
                Text.from_markup(f"TOTAL RAW RECORDS TODAY: [bold green]{raw_today:,}[/]"),
                Text.from_markup(f"RAW RECORDS IN DATABASE: {total_raw:,} | CANONICAL PLACES: {total_can:,}"),
                Text.from_markup(f"NEW INSERTS THIS RUN: [green]+{run_new:,}[/] | DUPLICATE/UPSERT EVENTS: [yellow]{run_dups:,}[/] | FAILED TILES: [{'red' if failed_tiles > 0 else 'dim'}]{failed_tiles}[/]"),
                Text.from_markup(f"ACTIVE DATABASE: [dim]{db_path}[/] | LAST DB WRITE: [dim]{last_write}[/]"),
            ]
            if export_info.get("export_dir"):
                exp_cnt = export_info.get("records_exported", 0)
                exp_ts = export_info.get("last_export_time", "N/A")
                elements.append(Text.from_markup(f"LIVE EXPORTS: [dim]{export_info.get('export_dir')}[/] ({exp_cnt:,} rows exported | Last: [dim]{exp_ts}[/])"))
            elements.append(Text.from_markup(f"ELAPSED: [dim]{elapsed_str}[/] | ETA: [cyan]{eta_str}[/]"))
            elements.append(Text.from_markup("[cyan]================================================[/]"))
            return Group(*elements)

        with Live(build_dashboard_renderable(), console=console, refresh_per_second=2, screen=False) as live:
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=2.5)
                    except asyncio.TimeoutError:
                        msg = None

                    # Refresh dashboard in-place
                    try:
                        live.update(build_dashboard_renderable())
                    except Exception as pe:
                        logger.warning(f"Error updating progress summary: {pe}")

                    if msg is None:
                        continue

                    ev = msg.get("event")
                    data = msg.get("data", {})
                    if ev == "stage_started":
                        live.console.print(f"\n[bold magenta]=== Stage Transition:[/] {data.get('stage')} (run: {data.get('run_id')})")
                        live.update(build_dashboard_renderable())
                    elif ev == "region_started":
                        reg_name = data.get("region_name") or data.get("region", "")
                        reg_idx = data.get("region_index", 1)
                        tot_regs = data.get("total_regions", 1)
                        cats_str = ", ".join(data.get("categories", []))
                        workers = data.get("category_workers", 3)
                        all_conc = data.get("all_categories_concurrent", False)
                        worker_desc = f"ALL {workers} CATEGORIES CONCURRENT" if all_conc else f"Parallel Workers: {workers}"
                        live.console.print(f"\n[bold cyan]--- State: {reg_name} [{data.get('country')}-{data.get('region')}] ({reg_idx}/{tot_regs}) ---[/]")
                        live.console.print(f"  [dim]Configured Categories ({worker_desc}): {cats_str}[/]")
                        live.update(build_dashboard_renderable())
                    elif ev == "task_skipped":
                        # Only display skipped messages for active issues, not clean resumed skips
                        if data.get("reason") != "already_completed":
                            reg_str = f"{data.get('country')}-{data.get('region')}"
                            cat = data.get("category")
                            st = data.get("status", "completed")
                            reason = data.get("reason", "")
                            live.console.print(f"  [dim yellow]↷ [{reg_str}] Category '[bold]{cat}[/]': {st} ({reason}). Skipping.[/]")
                    elif ev == "task_failed":
                        reg_str = f"{data.get('country')}-{data.get('region')}"
                        cat = data.get("category")
                        err = data.get("error", "Unknown error")
                        rem = data.get("remaining_tasks", 0)
                        live.console.print(f"  [bold red]✗ [{reg_str}] Category '[bold]{cat}[/]': FAILED - {err} | Rem: {rem}[/]")
                        live.update(build_dashboard_renderable())
                    elif ev == "tile_failed":
                        t_id = data.get("tile_id", "")
                        err = data.get("error", "Unknown error")
                        live.console.print(f"  [bold red]✗ Tile failed:[/] {t_id} - {err}")
                    elif ev == "task_completed":
                        elapsed_s = data.get("elapsed_seconds") if data.get("elapsed_seconds") is not None else int(time.time() - pipeline_start_time)
                        elapsed_str = f"{elapsed_s // 60:02d}:{elapsed_s % 60:02d}"
                        new_recs = data.get("new_records", data.get("records_collected", 0))
                        dups_task = data.get("duplicates_skipped", 0)
                        rem = data.get("remaining_tasks", 0)
                        live.console.print(
                            f"  [green]✓ [{data.get('country')}-{data.get('region')}][/] "
                            f"Category '[bold]{data.get('category')}[/]': "
                            f"[bold green]+{new_recs}[/] new places | "
                            f"[yellow]{dups_task}[/] dups/upserts | "
                            f"Remaining Categories: [bold]{rem}[/] | "
                            f"Elapsed: [dim]{elapsed_str}[/]"
                        )
                        live.update(build_dashboard_renderable())
                    elif ev == "region_completed":
                        reg_name = data.get("region_name") or data.get("region", "")
                        next_reg_name = data.get("next_region_name")
                        completed_cats = data.get("completed_categories", [])
                        failed_cats = data.get("failed_categories", [])
                        skipped_cats = data.get("skipped_categories", [])
                        detail_parts = [f"Done: {len(completed_cats)}"]
                        if skipped_cats:
                            detail_parts.append(f"Skipped: {len(skipped_cats)}")
                        if failed_cats:
                            detail_parts.append(f"Failed: {len(failed_cats)}")
                        summary_str = ", ".join(detail_parts)

                        if next_reg_name:
                            live.console.print(f"\n[bold green]═══ State {reg_name} Complete ({summary_str}). Starting {next_reg_name}... ═══[/]\n")
                        else:
                            live.console.print(f"\n[bold green]═══ State {reg_name} Complete ({summary_str}). All territories in scope complete! ═══[/]\n")
                        live.update(build_dashboard_renderable())
                    elif ev == "stage2_progress":
                        live.console.print(f"  [green]✓ [Stage 2 Discovery][/] {data.get('country')}-{data.get('region')}: +{data.get('verified_in_region', 0)} verified websites (Total: {data.get('total_verified', 0)})")
                    elif ev == "stage3_progress":
                        live.console.print(f"  [green]✓ [Stage 3 Enrichment][/] {data.get('country')}-{data.get('region')}: +{data.get('completed_in_region', 0)} companies enriched (Total: {data.get('total_enriched', 0)})")
                    elif ev == "completed":
                        live.update(build_dashboard_renderable())
                        live.console.print(f"\n[bold green]═══ Pipeline Run {data.get('run_id')} Completed Successfully! ═══[/]")
                        live.console.print(f"  Businesses collected: [bold]{data.get('businesses_collected', 0)}[/]")
                        live.console.print(f"  Websites verified:    [bold]{data.get('websites_verified', 0)}[/]")
                        live.console.print(f"  Companies enriched:   [bold]{data.get('companies_enriched', 0)}[/]\n")
                        break
                    elif ev == "cancelled":
                        live.update(build_dashboard_renderable())
                        live.console.print(f"\n[bold yellow]Pipeline Run {data.get('run_id')} was cancelled.[/]")
                        break
                    elif ev == "failed":
                        live.update(build_dashboard_renderable())
                        live.console.print(f"\n[bold red]Pipeline Run {data.get('run_id')} failed: {data.get('error')}[/]")
                        break
            finally:
                orchestrator.unsubscribe(q)

    try:
        asyncio.run(run_and_stream())
    except KeyboardInterrupt:
        console.print("\n[yellow]Keyboard interrupt received. Pausing pipeline...[/]")
        orchestrator.pause()
        console.print("[yellow]Pipeline paused. To resume, run: [bold cyan]python -m gmaps_scraper pipeline resume[/]")


@pipeline_app.command("status")
def pipeline_status(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """View current active or latest pipeline status and metrics."""
    from .orchestrator import PipelineOrchestrator

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    orchestrator = PipelineOrchestrator.get_instance(db, cfg)

    status = orchestrator.get_status()
    st_color = "green" if status["status"] == "running" else "yellow" if status["status"] == "paused" else "white"

    table = Table(title="Automated Pipeline Orchestrator Status")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Execution Status", f"[{st_color}]{status['status'].upper()}[/]")
    table.add_row("Active Run ID", str(status.get("active_run_id") or "None"))
    table.add_row("Current Stage", str(status.get("current_stage") or "idle"))
    table.add_row("Current Country / Region", f"{status.get('current_country') or 'None'} - {status.get('current_region') or 'None'}")
    table.add_row("Current Category", str(status.get("current_category") or "None"))
    table.add_row("Dry Run Mode", str(status.get("dry_run", False)))

    tc = status.get("task_counts", {})
    table.add_row("Total Collection Tasks", str(tc.get("total", 0)))
    table.add_row("Completed Tasks", f"[green]{tc.get('completed', 0)}[/]")
    table.add_row("Pending Tasks", f"[yellow]{tc.get('pending', 0)}[/]")
    table.add_row("Failed Tasks", f"[red]{tc.get('failed', 0)}[/]")

    table.add_row("Total Businesses Collected", str(status.get("total_businesses_collected", 0)))
    table.add_row("Websites Verified", str(status.get("websites_verified", 0)))
    table.add_row("Companies Enriched", str(status.get("companies_enriched", 0)))
    table.add_row("Company Profiles", str(status.get("company_profiles_count", 0)))
    table.add_row("Public Leaders Extracted", str(status.get("company_leaders_count", 0)))
    table.add_row("Business Contacts Extracted", str(status.get("company_contacts_count", 0)))
    console.print(table)

    col_stats = db.get_collection_progress_stats(
        country_code=status.get("current_country"),
        region_code=status.get("current_region")
    )
    col_table = Table(title="Geographic Tiles & Database Collection Metrics")
    col_table.add_column("Metric", style="cyan")
    col_table.add_column("Value", style="green")
    col_table.add_row("Raw Records Saved Today", f"[bold green]{col_stats.get('raw_saved_today', 0):,}[/]")
    col_table.add_row("Total Raw Records (raw_places)", f"{col_stats.get('total_raw', 0):,}")
    col_table.add_row("Total Canonical Records (places)", f"{col_stats.get('total_canonical', 0):,}")
    col_table.add_row("Last SQLite Commit", f"[dim]{col_stats.get('last_db_write', 'N/A')}[/]")

    fin = col_stats.get("finished_tiles", 0)
    act = col_stats.get("active_total", 0)
    rem = col_stats.get("remaining_tiles", 0)
    pct_str = "growing" if col_stats.get("can_grow") else (f"{(fin / act * 100):.1f}%" if act > 0 else "0.0%")
    rem_str = f"at least {rem:,}" if col_stats.get("can_grow") else f"{rem:,}"

    col_table.add_row("Tiles Progress", f"{fin:,}/{act:,} ({pct_str})")
    col_table.add_row("Tiles Remaining", f"[bold cyan]{rem_str}[/]")
    col_table.add_row("Completed / Limit Tiles", f"{col_stats.get('completed', 0):,} completed / {col_stats.get('limit_reached', 0):,} limit")
    col_table.add_row("Running / Pending Tiles", f"{col_stats.get('running', 0):,} running / {col_stats.get('pending', 0):,} pending")
    col_table.add_row("Subdivided Parents", f"{col_stats.get('subdivided_parents', 0):,} (awaiting children: {col_stats.get('child_tiles_awaiting', 0):,})")
    col_table.add_row("Failed Tiles", f"[{'red' if col_stats.get('failed', 0) > 0 else 'dim'}]{col_stats.get('failed', 0):,}[/]")
    console.print(col_table)

    cats_dict = col_stats.get("categories", {})
    if cats_dict:
        cat_table = Table(title="Category Collection Breakdown")
        cat_table.add_column("Category", style="cyan")
        cat_table.add_column("Tile Progress", justify="left")
        cat_table.add_column("Remaining", justify="left")
        cat_table.add_column("Raw Saved", justify="right")
        cat_table.add_column("Status", justify="center")
        for cname, cinfo in sorted(cats_dict.items()):
            c_fin = cinfo.get("finished_tiles", 0)
            c_act = cinfo.get("active_total", 0)
            c_rem = cinfo.get("remaining_tiles", 0)
            c_cg = cinfo.get("can_grow", False)
            c_pct = "(growing)" if c_cg else (f"{(c_fin / c_act * 100):.1f}%" if c_act > 0 else "0.0%")
            c_rem_str = f">={c_rem:,}" if c_cg else f"{c_rem:,}"
            c_st = cinfo.get("status", "PENDING")
            st_color = "green" if c_st == "RUNNING" else "blue" if c_st == "COMPLETED" else "red" if c_st == "FAILED" else "dim"
            cat_table.add_row(
                cname,
                f"{c_fin:,}/{c_act:,} ({c_pct})" if c_act > 0 else f"{c_fin:,} (0.0%)",
                c_rem_str,
                f"{cinfo.get('records_collected', 0):,}",
                f"[{st_color}]{c_st}[/]"
            )
        console.print(cat_table)


@pipeline_app.command("pause")
def pipeline_pause(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Pause the currently running pipeline."""
    from .orchestrator import PipelineOrchestrator

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    orchestrator = PipelineOrchestrator.get_instance(db, cfg)

    if orchestrator.pause():
        console.print("[bold yellow]Pipeline paused successfully.[/]")
    else:
        console.print("[yellow]No active pipeline run to pause.[/]")


@pipeline_app.command("resume")
def pipeline_resume(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Resume the paused pipeline."""
    from .orchestrator import PipelineOrchestrator

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    orchestrator = PipelineOrchestrator.get_instance(db, cfg)

    if orchestrator.resume():
        console.print("[bold green]Pipeline resumed successfully.[/]")
    else:
        console.print("[yellow]No paused pipeline run to resume.[/]")


@pipeline_app.command("cancel")
def pipeline_cancel(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Cancel the active pipeline run."""
    from .orchestrator import PipelineOrchestrator

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    orchestrator = PipelineOrchestrator.get_instance(db, cfg)

    if orchestrator.cancel():
        console.print("[bold yellow]Pipeline cancellation requested.[/]")
    else:
        console.print("[yellow]No active pipeline run to cancel.[/]")


@pipeline_app.command("retry")
def pipeline_retry(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Reset failed collection tasks so they can be retried."""
    from .orchestrator import PipelineOrchestrator

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    orchestrator = PipelineOrchestrator.get_instance(db, cfg)

    count = orchestrator.retry_failed()
    console.print(f"[bold green]Reset {count} failed tasks to 'pending' status.[/]")


@pipeline_app.command("start")
def pipeline_start(
    country: str = typer.Option("both", "--country", "-C", help="Target country: 'US', 'MX', or 'both'"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Comma-separated region codes (e.g. 'CA,TX,JAL')"),
    categories: Optional[str] = typer.Option(None, "--categories", help="Comma-separated categories (e.g. 'healthcare,food')"),
    category_workers: str = typer.Option("3", "--category-workers", "-w", help="Concurrent workers processing business categories. Pass number, 'all', or '0' for all configured categories"),
    all_categories_concurrent: bool = typer.Option(False, "--all-categories-concurrent", "--all-categories", help="Run ALL configured business categories concurrently within the current state"),
    request_concurrency: int = typer.Option(3, "--request-concurrency", help="Maximum shared concurrent HTTP requests across workers"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max places per region/category task (omit or 0 for unlimited)"),
    batch_size: int = typer.Option(50, "--batch-size", help="Batch size for website discovery & enrichment"),
    max_pages: int = typer.Option(5, "--max-pages", help="Max crawl pages per website during enrichment"),
    stages: str = typer.Option("1,2,3", "--stages", help="Enabled stages: '1,2,3' or comma-separated"),
    collection_only: bool = typer.Option(False, "--collection-only", help="Run scraping collection only; do not run deduplication or downstream jobs"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run simulation mode"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume from existing checkpoints"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry eligible failed tasks"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream live progress in terminal"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Start automated state-by-state pipeline across all configured regions and categories."""
    pipeline_run(
        country=country,
        regions=regions,
        categories=categories,
        category_workers=category_workers,
        all_categories_concurrent=all_categories_concurrent,
        request_concurrency=request_concurrency,
        limit=limit,
        batch_size=batch_size,
        max_pages=max_pages,
        stages=stages,
        collection_only=collection_only,
        dry_run=dry_run,
        resume=resume,
        retry_failed=retry_failed,
        follow=follow,
        config_path=config_path,
    )


@pipeline_app.command("tiles")
def pipeline_tiles(
    country: str = typer.Option("US", "--country", "-C", help="Country code: 'US' or 'MX'"),
    region: str = typer.Option(..., "--region", "-r", help="Territory code (e.g. 'AK', 'CA', 'CMX')"),
    category: Optional[str] = typer.Option(None, "--category", help="Category filter"),
    status: Optional[str] = typer.Option(None, "--status", help="Status filter ('pending', 'running', 'completed', 'subdivided', 'failed', etc.)"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Inspect generated geographic tiles, subdivision hierarchy, and results for a region."""
    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    counts = db.get_pipeline_tile_counts(country=country.upper(), region=region.upper())
    tiles = db.get_geographic_tiles(
        country=country.upper(),
        region=region.upper(),
        category=category,
        status=status,
        limit=100
    )

    console.print(f"\n[bold cyan]Geographic Tiles Summary for {country.upper()}-{region.upper()}:[/]")
    summary_table = Table()
    summary_table.add_column("Total", style="cyan")
    summary_table.add_column("Completed", style="green")
    summary_table.add_column("Running", style="cyan")
    summary_table.add_column("Subdivided", style="magenta")
    summary_table.add_column("Waiting Retry", style="yellow")
    summary_table.add_column("Failed", style="red")
    summary_table.add_column("Limit Reached", style="yellow")
    summary_table.add_column("Pending", style="dim")
    summary_table.add_row(
        str(counts.get("total", 0)),
        str(counts.get("completed", 0)),
        str(counts.get("running", 0)),
        str(counts.get("subdivided", 0)),
        str(counts.get("retry_pending", 0)),
        str(counts.get("failed", 0)),
        str(counts.get("limit_reached", 0)),
        str(counts.get("pending", 0)),
    )
    console.print(summary_table)

    if not tiles:
        console.print(f"[yellow]No tiles found matching criteria for {country}-{region}.[/]")
        return

    table = Table(title=f"Tiles ({min(len(tiles), 100)} shown)")
    table.add_column("Tile ID", style="cyan")
    table.add_column("Depth", style="dim", justify="right")
    table.add_column("Category", style="yellow")
    table.add_column("Bounds (Lat / Lng)", style="white")
    table.add_column("Status", style="magenta")
    table.add_column("Results", style="green", justify="right")
    table.add_column("New", style="green", justify="right")
    table.add_column("Dups", style="dim", justify="right")
    table.add_column("Att/Retries", style="dim", justify="right")
    table.add_column("Error", style="red")

    for t in tiles:
        st_color = (
            "green" if t["status"] == "completed"
            else "cyan" if t["status"] == "running"
            else "magenta" if t["status"] == "subdivided"
            else "yellow" if t["status"] in ("retry_pending", "limit_reached")
            else "red" if t["status"] == "failed"
            else "dim"
        )
        bounds_str = f"[{t['min_lat']:.2f},{t['max_lat']:.2f}] / [{t['min_lng']:.2f},{t['max_lng']:.2f}]"
        table.add_row(
            t["tile_id"],
            str(t.get("depth", 0)),
            t.get("category", "-"),
            bounds_str,
            f"[{st_color}]{t['status'].upper()}[/]",
            str(t.get("results_count", 0)),
            str(t.get("new_records_count", 0)),
            str(t.get("duplicate_cids_count", 0)),
            f"{t.get('attempts_count', 0)}/{t.get('retry_count', 0)}",
            t.get("last_error") or ""
        )
    console.print(table)


@pipeline_app.command("list-exports")
def pipeline_list_exports(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """List all available state-wise, date-wise exports."""
    from .export import list_all_state_exports

    cfg = load_config(config_path)
    exports = list_all_state_exports(cfg.absolute_exports_dir)

    if not exports:
        console.print("[yellow]No state exports found in exports directory.[/]")
        return

    table = Table(title="State-Wise & Date-Wise Data Exports")
    table.add_column("Date", style="cyan")
    table.add_column("Country", style="white")
    table.add_column("Folder", style="green")
    table.add_column("Businesses", style="bold green", justify="right")
    table.add_column("Categories", style="yellow")
    table.add_column("Completeness", style="magenta")
    table.add_column("Summary File", style="dim")

    for exp in exports:
        comp_st = exp.get("completeness_status", "unknown")
        comp_color = "green" if comp_st == "fully_completed" else "yellow" if comp_st == "partially_completed" else "red"
        table.add_row(
            exp.get("run_date", "-"),
            exp.get("country", "-"),
            exp.get("folder_name", "-"),
            str(exp.get("total_businesses", 0)),
            ", ".join(exp.get("categories", [])) or "None",
            f"[{comp_color}]{comp_st.upper()}[/]",
            exp.get("run_summary_json", "-")
        )
    console.print(table)


@pipeline_app.command("export")
def pipeline_export(
    country: str = typer.Option(..., "--country", "-C", help="Country code: 'US' or 'MX'"),
    region: str = typer.Option(..., "--region", "-r", help="Region code (e.g. 'AK', 'CA', 'CMX')"),
    run_date: Optional[str] = typer.Option(None, "--date", "-d", help="Run date (YYYY-MM-DD), defaults to today"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Export complete state package (CSVs and run_summary.json) for a region on demand."""
    from datetime import datetime, timezone
    from .export import export_state_complete_package

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    date_str = run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    console.print(f"[bold cyan]Generating state export package for {country.upper()}-{region.upper()} on date {date_str}...[/]")

    summary = export_state_complete_package(
        db=db,
        exports_base_dir=cfg.absolute_exports_dir,
        country=country.upper(),
        region_code=region.upper(),
        run_date=date_str
    )

    console.print(f"[bold green]✓ Export Complete![/] Status: [bold]{summary.get('completeness_status', '').upper()}[/]")
    console.print(f"  Target Folder: {summary.get('export_folder')}")
    console.print(f"  Total Businesses: {summary.get('total_businesses', 0)}")
    console.print(f"  Categories Exported: {', '.join(summary.get('categories', []))}")


# -----------------------------------------------------------------------------
# Storage Sub-Commands (storage sync, status, backup)
# -----------------------------------------------------------------------------

storage_app = typer.Typer(help="Manage central and state-wise SQLite databases and exports")
app.add_typer(storage_app, name="storage")


@storage_app.command("sync")
def storage_sync(
    country: Optional[str] = typer.Option(None, "--country", "-C", help="Target country: 'US', 'MX', or 'both'"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Comma-separated region codes (e.g. 'CA,TX')"),
    all_regions: bool = typer.Option(False, "--all", help="Synchronize all populated/configured regions"),
    export: bool = typer.Option(True, "--export/--no-export", help="Also generate CSV and JSON exports"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Synchronize state-wise SQLite databases and exports from central SQLite database."""
    from .storage import RegionalStorageManager
    from .scope import get_region, ALLOWED_COUNTRIES

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    mgr = RegionalStorageManager(db, cfg)

    if all_regions:
        c_code = country.upper() if country and country.upper() in ALLOWED_COUNTRIES else None
        console.print(f"[bold cyan]Synchronizing all state-wise databases from central database...[/]")
        summary = mgr.sync_all(country_code=c_code, export_also=export)

        table = Table(title="Regional Synchronization Results")
        table.add_column("Country", style="white")
        table.add_column("Region", style="cyan")
        table.add_column("Places Synced", style="bold green", justify="right")
        table.add_column("Status", style="green")
        table.add_column("Database Path", style="dim")

        for r in summary["regions"]:
            st_color = "green" if r.get("status") == "success" else "red"
            table.add_row(
                r.get("country_code", "-"),
                f"{r.get('region_name', '')} ({r.get('region_code', '-')})",
                str(r.get("places_count", r.get("places_synced", 0))),
                f"[{st_color}]{r.get('status', 'unknown').upper()}[/]",
                str(r.get("database_path", r.get("regional_db_path", "-")))
            )
        console.print(table)
        console.print(f"\n[bold green]Sync completed:[/] {summary['success_count']} succeeded, {summary['failed_count']} failed.")
        return

    if not regions:
        console.print("[yellow]Please specify either --regions <codes> or --all.[/]")
        console.print("Example: [bold cyan]python -m gmaps_scraper storage sync --country US --regions CA,TX[/]")
        console.print("Example: [bold cyan]python -m gmaps_scraper storage sync --all[/]")
        raise typer.Exit(code=1)

    c_code = (country or "US").upper()
    region_codes = [r.strip().upper() for r in regions.split(",") if r.strip()]

    table = Table(title=f"Regional Synchronization: {c_code}")
    table.add_column("Region", style="cyan")
    table.add_column("Places Synced", style="bold green", justify="right")
    table.add_column("Database Path", style="dim")
    table.add_column("Exports Folder", style="yellow")
    table.add_column("Status", style="green")

    for rc in region_codes:
        try:
            reg_info = get_region(c_code, rc)
            r_name = reg_info.name
        except Exception:
            r_name = rc

        try:
            if export:
                res = mgr.sync_and_export_region(country_code=c_code, region_code=rc, region_name=r_name)
                places = res.get("places_count", 0)
                db_p = str(res.get("database_path", "-"))
                exp_p = str(res.get("export_folder", "-"))
            else:
                res = mgr.sync_region(country_code=c_code, region_code=rc, region_name=r_name)
                places = res.get("places_synced", 0)
                db_p = str(res.get("regional_db_path", "-"))
                exp_p = "Skipped (--no-export)"

            table.add_row(f"{r_name} ({rc})", str(places), db_p, exp_p, "[bold green]SUCCESS[/]")
        except Exception as e:
            logger.error(f"Sync error for {c_code}-{rc}: {e}")
            table.add_row(f"{r_name} ({rc})", "0", "-", "-", f"[bold red]FAILED: {e}[/]")

    console.print(table)


@storage_app.command("status")
def storage_status(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """View central and state-wise SQLite database status, sync records, and export packages."""
    from .storage import RegionalStorageManager

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()
    mgr = RegionalStorageManager(db, cfg)

    status = mgr.get_storage_status()
    central = status["central_database"]

    # Central DB Table
    c_table = Table(title="Authoritative Central Database")
    c_table.add_column("Property", style="cyan")
    c_table.add_column("Value", style="bold green")
    c_table.add_row("Database Path", central["path"])
    c_table.add_row("Total Places", str(central["total_places"]))
    c_table.add_row("Total Reviews", str(central["total_reviews"]))
    c_table.add_row("Total Photos", str(central["total_photos"]))
    c_table.add_row("Details Complete", str(central["details_done"]))
    console.print(c_table)

    # Regional DBs Table
    reg_dbs = status.get("regional_databases", [])
    r_table = Table(title=f"State-Wise SQLite Databases ({len(reg_dbs)} initialized)")
    r_table.add_column("Country", style="white")
    r_table.add_column("Region File", style="cyan")
    r_table.add_column("Places Count", style="bold green", justify="right")
    r_table.add_column("File Size", style="dim", justify="right")
    r_table.add_column("Last Sync Time", style="yellow")
    r_table.add_column("Sync Status", style="green")

    for rdb in reg_dbs:
        size_kb = f"{rdb.get('file_size_bytes', 0) / 1024:.1f} KB" if "file_size_bytes" in rdb else "-"
        st_color = "green" if rdb.get("sync_status") == "success" else "yellow"
        r_table.add_row(
            rdb.get("country_code", "-"),
            rdb.get("filename", "-"),
            str(rdb.get("places_count", "-")),
            size_kb,
            str(rdb.get("last_sync_time") or "Not yet synced"),
            f"[{st_color}]{rdb.get('sync_status', 'unknown').upper()}[/]"
        )

    if not reg_dbs:
        console.print("[yellow]No regional SQLite databases initialized yet. Run `storage sync` to initialize.[/]")
    else:
        console.print(r_table)

    # Exports summary
    console.print(f"\n[bold cyan]Total Export Packages:[/] {status.get('total_exports_packages', 0)}")


@storage_app.command("backup")
def storage_backup(
    output_path: Optional[str] = typer.Option(None, "--output", "-o", help="Custom backup destination path"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Create a verified timestamped backup of the central SQLite database."""
    from .storage import RegionalStorageManager

    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    mgr = RegionalStorageManager(db, cfg)

    console.print(f"[bold cyan]Creating online backup of central database: {cfg.absolute_db_path}...[/]")
    out_p = Path(output_path) if output_path else None
    res = mgr.create_backup(out_p)

    table = Table(title="Central Database Backup Verified")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="bold green")
    table.add_row("Source Path", res["source_path"])
    table.add_row("Backup Path", res["backup_path"])
    table.add_row("Size", f"{res['file_size_bytes'] / (1024 * 1024):.2f} MB")
    table.add_row("Places Verified", str(res["places_count"]))
    table.add_row("Integrity Check", res["integrity"])
    table.add_row("Timestamp", res["timestamp"])
    table.add_row("Status", f"[bold green]{res['status'].upper()}[/]")
    console.print(table)


# -----------------------------------------------------------------------------
# Dedicated Collection-Only, Deduplication, and Counting Commands
# -----------------------------------------------------------------------------

def _exec_collection(
    country: str = "both",
    regions: Optional[str] = None,
    categories: Optional[str] = None,
    category_workers: str = "all",
    all_categories_concurrent: bool = True,
    request_concurrency: int = 3,
    limit: Optional[int] = None,
    dry_run: bool = False,
    resume: bool = True,
    retry_failed: bool = False,
    follow: bool = True,
    config_path: Optional[str] = None,
):
    """Executes collection-only scraping job without running deduplication or downstream stages."""
    pipeline_run(
        country=country,
        regions=regions,
        categories=categories,
        category_workers=category_workers,
        all_categories_concurrent=all_categories_concurrent,
        request_concurrency=request_concurrency,
        limit=limit,
        batch_size=50,
        max_pages=5,
        stages="1",
        collection_only=True,
        dry_run=dry_run,
        resume=resume,
        retry_failed=retry_failed,
        follow=follow,
        config_path=config_path,
    )


@app.command("collect")
def collect_cmd(
    country: str = typer.Option("both", "--country", "-C", help="Target country: 'US', 'MX', or 'both'"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Comma-separated region codes (e.g. 'CA,TX,JAL')"),
    categories: Optional[str] = typer.Option(None, "--categories", help="Comma-separated business categories"),
    category_workers: str = typer.Option("all", "--category-workers", "-w", help="Concurrent workers processing business categories ('all' or number)"),
    all_categories_concurrent: bool = typer.Option(True, "--all-categories-concurrent/--worker-pool", help="Run all configured business categories concurrently within current state"),
    request_concurrency: int = typer.Option(3, "--request-concurrency", help="Maximum shared concurrent HTTP requests across workers"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max places per region/category task (omit or 0 for unlimited)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run simulation mode"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume from existing checkpoints"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry eligible failed tasks"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream live progress in terminal"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Run collection-only scraping job (Stage 1 only, no deduplication, no downstream enrichment)."""
    _exec_collection(
        country=country,
        regions=regions,
        categories=categories,
        category_workers=category_workers,
        all_categories_concurrent=all_categories_concurrent,
        request_concurrency=request_concurrency,
        limit=limit,
        dry_run=dry_run,
        resume=resume,
        retry_failed=retry_failed,
        follow=follow,
        config_path=config_path,
    )


@pipeline_app.command("collect")
def pipeline_collect_cmd(
    country: str = typer.Option("both", "--country", "-C", help="Target country: 'US', 'MX', or 'both'"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Comma-separated region codes (e.g. 'CA,TX,JAL')"),
    categories: Optional[str] = typer.Option(None, "--categories", help="Comma-separated business categories"),
    category_workers: str = typer.Option("all", "--category-workers", "-w", help="Concurrent workers processing business categories ('all' or number)"),
    all_categories_concurrent: bool = typer.Option(True, "--all-categories-concurrent/--worker-pool", help="Run all configured business categories concurrently within current state"),
    request_concurrency: int = typer.Option(3, "--request-concurrency", help="Maximum shared concurrent HTTP requests across workers"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Max places per region/category task (omit or 0 for unlimited)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run simulation mode"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume from existing checkpoints"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Retry eligible failed tasks"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream live progress in terminal"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Run dedicated collection-only pipeline (no deduplication, no downstream stages)."""
    _exec_collection(
        country=country,
        regions=regions,
        categories=categories,
        category_workers=category_workers,
        all_categories_concurrent=all_categories_concurrent,
        request_concurrency=request_concurrency,
        limit=limit,
        dry_run=dry_run,
        resume=resume,
        retry_failed=retry_failed,
        follow=follow,
        config_path=config_path,
    )


def _exec_dedup(
    country: Optional[str] = None,
    regions: Optional[str] = None,
    run_id: Optional[str] = None,
    batch_size: int = 1000,
    dry_run: bool = False,
    config_path: Optional[str] = None
):
    """Reads raw collection from raw_places, normalizes and clusters records, and writes canonical places to places table."""
    from .dedup import DeduplicationService
    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    dedup_svc = DeduplicationService(db)
    console.print(f"[bold magenta]Starting Manual Deduplication Job ({'DRY-RUN' if dry_run else 'LIVE'})...[/]")
    if country:
        console.print(f"  [dim]Country filter: {country.upper()}[/]")
    if regions:
        console.print(f"  [dim]Region filter: {regions.upper()}[/]")
    if run_id:
        console.print(f"  [dim]Run ID filter: {run_id}[/]")

    region_list = [r.strip().upper() for r in regions.split(",") if r.strip()] if regions else None

    if region_list and len(region_list) > 1:
        total_scanned = 0
        total_unique = 0
        total_dups = 0
        total_created = 0
        total_updated = 0
        for r_code in region_list:
            res = dedup_svc.run_deduplication(
                country_code=country,
                region_code=r_code,
                run_id=run_id,
                batch_size=batch_size,
                dry_run=dry_run
            )
            total_scanned += res["raw_records_scanned"]
            total_unique += res["unique_canonical_places"]
            total_dups += res["duplicate_events_merged"]
            total_created += res["new_places_created"]
            total_updated += res["existing_places_updated"]
        metrics = {
            "raw_records_scanned": total_scanned,
            "unique_canonical_places": total_unique,
            "duplicate_events_merged": total_dups,
            "new_places_created": total_created,
            "existing_places_updated": total_updated,
            "dry_run": dry_run
        }
    else:
        single_reg = region_list[0] if region_list else None
        metrics = dedup_svc.run_deduplication(
            country_code=country,
            region_code=single_reg,
            run_id=run_id,
            batch_size=batch_size,
            dry_run=dry_run
        )

    table = Table(title="Deduplication Job Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    table.add_row("Raw Records Scanned", str(metrics["raw_records_scanned"]))
    table.add_row("Unique Canonical Places", str(metrics["unique_canonical_places"]))
    table.add_row("Duplicate Events Merged", str(metrics["duplicate_events_merged"]))
    table.add_row("New Canonical Places Inserted", str(metrics["new_places_created"]))
    table.add_row("Existing Canonical Places Updated", str(metrics["existing_places_updated"]))
    table.add_row("Execution Mode", "DRY-RUN (Preview)" if dry_run else "LIVE (Committed)")
    console.print(table)
    console.print("[bold green][OK] Deduplication completed successfully without modifying raw records.[/]")


@app.command("dedup")
def dedup_cmd(
    country: Optional[str] = typer.Option(None, "--country", "-C", help="Filter raw records by country ('US', 'MX', or omit for all)"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Filter by comma-separated region codes (e.g. 'CA,TX')"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Filter by specific collection run ID"),
    batch_size: int = typer.Option(1000, "--batch-size", help="Batch size for writing canonical places"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report deduplication preview without writing to canonical places table"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Run manual deduplication job: read raw collection, normalize & match records, and write canonical results."""
    _exec_dedup(
        country=country,
        regions=regions,
        run_id=run_id,
        batch_size=batch_size,
        dry_run=dry_run,
        config_path=config_path,
    )


@pipeline_app.command("dedup")
def pipeline_dedup_cmd(
    country: Optional[str] = typer.Option(None, "--country", "-C", help="Filter raw records by country ('US', 'MX', or omit for all)"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Filter by comma-separated region codes (e.g. 'CA,TX')"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Filter by specific collection run ID"),
    batch_size: int = typer.Option(1000, "--batch-size", help="Batch size for writing canonical places"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report deduplication preview without writing to canonical places table"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Run manual deduplication on raw business collection."""
    _exec_dedup(
        country=country,
        regions=regions,
        run_id=run_id,
        batch_size=batch_size,
        dry_run=dry_run,
        config_path=config_path,
    )


def _exec_counts(
    country: Optional[str] = None,
    regions: Optional[str] = None,
    config_path: Optional[str] = None
):
    """Displays raw business record counts alongside deduplicated canonical counts."""
    cfg = load_config(config_path)
    db = Database(cfg.absolute_db_path)
    db.init_schema()

    summary = db.get_collection_counts(country_code=country)

    table = Table(title="Google Maps Scraper - Raw Discoveries vs Canonical Places")
    table.add_column("Dataset / Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")

    table.add_row("Raw Business Discoveries (raw_places)", str(summary["raw_total"]))
    table.add_row("Canonical Deduplicated Places (places)", str(summary["canonical_total"]))
    table.add_row("Duplicate Events Collapsed", str(summary["duplicates_collapsed"]))
    table.add_row("Deduplication Ratio", f"{summary['dedup_ratio_pct']}%")
    console.print(table)

    if summary.get("raw_by_country") or summary.get("canonical_by_country"):
        c_table = Table(title="Country Distribution (Raw vs Canonical)")
        c_table.add_column("Country", style="magenta")
        c_table.add_column("Raw Discoveries", style="yellow", justify="right")
        c_table.add_column("Canonical Places", style="green", justify="right")
        all_countries = sorted(set(list(summary["raw_by_country"].keys()) + list(summary["canonical_by_country"].keys())))
        for c in all_countries:
            c_table.add_row(c, str(summary["raw_by_country"].get(c, 0)), str(summary["canonical_by_country"].get(c, 0)))
        console.print(c_table)


@app.command("counts")
def counts_cmd(
    country: Optional[str] = typer.Option(None, "--country", "-C", help="Filter counts by country ('US', 'MX', or omit for all)"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Filter counts by comma-separated region codes"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Check raw record counts and deduplicated canonical counts."""
    _exec_counts(country=country, regions=regions, config_path=config_path)


@pipeline_app.command("counts")
def pipeline_counts_cmd(
    country: Optional[str] = typer.Option(None, "--country", "-C", help="Filter counts by country ('US', 'MX', or omit for all)"),
    regions: Optional[str] = typer.Option(None, "--regions", "-r", help="Filter counts by comma-separated region codes"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config")
):
    """Check raw collection record counts and canonical places counts."""
    _exec_counts(country=country, regions=regions, config_path=config_path)


if __name__ == "__main__":
    app()



