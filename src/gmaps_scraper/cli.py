import asyncio
import os
import sys
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

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

    table.add_row("Total Places", str(s["total_places"]))
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

if __name__ == "__main__":
    app()
