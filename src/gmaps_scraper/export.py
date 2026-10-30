import io
import os
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import logging
import pandas as pd
from .db import Database

logger = logging.getLogger("gmaps_scraper.export")

SCHEMA_COLUMNS = [
    "cid", "ftid", "place_id", "name", "address", "street_address",
    "latitude", "longitude", "website", "phone", "phone_e164",
    "category", "vertical", "vertical_raw", "country", "country_code",
    "region", "city", "postal_code", "rating", "review_count",
    "timezone", "claimed_status", "photo_thumbnail", "status",
    "attempts", "error", "created_at", "updated_at"
]

COMPACT_COLUMNS = [
    "cid", "name", "category", "rating", "review_count",
    "address", "phone", "website", "status"
]

def export_data(
    db: Database,
    export_format: str = "csv",
    output_dir: Optional[Path] = None,
    query_name: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    full: bool = False
) -> Dict[str, Any]:
    """
    Exports scraped places, reviews, hours, attributes, and photos.
    Returns generated file paths and records counts.
    """
    filters = filters or {}
    places, _ = db.query_places(
        q=filters.get("q"),
        status=filters.get("status"),
        category=filters.get("category"),
        min_rating=filters.get("min_rating"),
        min_reviews=filters.get("min_reviews"),
        has_phone=filters.get("has_phone"),
        has_website=filters.get("has_website"),
        has_photos=filters.get("has_photos"),
        claimed=filters.get("claimed"),
        vertical=filters.get("vertical"),
        country=filters.get("country"),
        city=filters.get("city"),
        has_reviews=filters.get("has_reviews"),
        has_hours=filters.get("has_hours"),
        has_attributes=filters.get("has_attributes"),
        page=1,
        page_size=1000000  # full export
    )

    if places:
        df_places = pd.DataFrame(places)
        cols_to_use = SCHEMA_COLUMNS if full else COMPACT_COLUMNS
        for col in cols_to_use:
            if col not in df_places.columns:
                df_places[col] = None
        df_places = df_places[cols_to_use]
        cids = [p["cid"] for p in places if p.get("cid")]
    else:
        cols_to_use = SCHEMA_COLUMNS if full else COMPACT_COLUMNS
        df_places = pd.DataFrame(columns=cols_to_use)
        cids = []

    with db.get_connection() as conn:
        if cids:
            placeholders = ",".join(["?"] * len(cids))
            hours_rows = conn.execute(f"SELECT * FROM hours WHERE cid IN ({placeholders})", cids).fetchall()
            attr_rows = conn.execute(f"SELECT * FROM attributes WHERE cid IN ({placeholders})", cids).fetchall()
            rev_rows = conn.execute(f"SELECT * FROM reviews WHERE cid IN ({placeholders})", cids).fetchall()
            photo_rows = conn.execute(f"SELECT * FROM photos WHERE cid IN ({placeholders})", cids).fetchall()
        else:
            hours_rows = []
            attr_rows = []
            rev_rows = []
            photo_rows = []

    df_hours = pd.DataFrame([dict(r) for r in hours_rows])
    df_attrs = pd.DataFrame([dict(r) for r in attr_rows])
    df_reviews = pd.DataFrame([dict(r) for r in rev_rows])
    df_photos = pd.DataFrame([dict(r) for r in photo_rows])

    results = {
        "places_count": len(df_places),
        "reviews_count": len(df_reviews),
        "hours_count": len(df_hours),
        "attributes_count": len(df_attrs),
        "photos_count": len(df_photos),
        "files": []
    }

    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"{query_name}_" if query_name else "gmaps_"

        dfs = {
            f"{prefix}places": df_places,
            f"{prefix}hours": df_hours,
            f"{prefix}attributes": df_attrs,
            f"{prefix}reviews": df_reviews,
            f"{prefix}photos": df_photos,
        }

        for name, df in dfs.items():
            ext = "csv" if export_format == "csv" else "jsonl" if export_format == "jsonl" else "parquet"
            filepath = output_dir / f"{name}.{ext}"
            try:
                if export_format == "csv":
                    df.to_csv(filepath, index=False, encoding="utf-8")
                elif export_format == "jsonl":
                    df.to_json(filepath, orient="records", lines=True, force_ascii=False)
                elif export_format == "parquet":
                    df.to_parquet(filepath, index=False)
            except PermissionError:
                # If file is locked by an editor or Excel on Windows, write to fallback file
                filepath = output_dir / f"{name}_latest.{ext}"
                if export_format == "csv":
                    df.to_csv(filepath, index=False, encoding="utf-8")
                elif export_format == "jsonl":
                    df.to_json(filepath, orient="records", lines=True, force_ascii=False)
                elif export_format == "parquet":
                    df.to_parquet(filepath, index=False)
            results["files"].append(str(filepath))

    return results

def export_stream_bytes(
    db: Database,
    export_format: str = "csv",
    filters: Optional[Dict[str, Any]] = None,
    full: bool = False
) -> Tuple[bytes, str]:
    """Returns places bytes and mime-type for API download."""
    filters = filters or {}
    places, _ = db.query_places(
        q=filters.get("q"),
        status=filters.get("status"),
        category=filters.get("category"),
        min_rating=filters.get("min_rating"),
        min_reviews=filters.get("min_reviews"),
        has_phone=filters.get("has_phone"),
        has_website=filters.get("has_website"),
        has_photos=filters.get("has_photos"),
        claimed=filters.get("claimed"),
        vertical=filters.get("vertical"),
        country=filters.get("country"),
        city=filters.get("city"),
        has_reviews=filters.get("has_reviews"),
        has_hours=filters.get("has_hours"),
        has_attributes=filters.get("has_attributes"),
        page=1,
        page_size=1000000
    )
    cols_to_use = SCHEMA_COLUMNS if full else COMPACT_COLUMNS
    if places:
        df = pd.DataFrame(places)
        for col in cols_to_use:
            if col not in df.columns:
                df[col] = None
        df = df[cols_to_use]
    else:
        df = pd.DataFrame(columns=cols_to_use)

    if export_format == "csv":
        buf = io.StringIO()
        df.to_csv(buf, index=False, encoding="utf-8")
        return buf.getvalue().encode("utf-8"), "text/csv"
    elif export_format == "jsonl":
        buf = io.StringIO()
        df.to_json(buf, orient="records", lines=True, force_ascii=False)
        return buf.getvalue().encode("utf-8"), "application/x-ndjson"
    elif export_format == "parquet":
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        return buf.getvalue(), "application/octet-stream"
    else:
        buf = io.StringIO()
        df.to_csv(buf, index=False, encoding="utf-8")
        return buf.getvalue().encode("utf-8"), "text/csv"


# -----------------------------------------------------------------------------
# State-Wise, Date-Wise, and Category-Wise Pipeline Exports (Section 6)
# -----------------------------------------------------------------------------

import re
import unicodedata
from datetime import datetime

STATE_EXPORT_COLUMNS = [
    "name", "cid", "country", "country_code", "region", "category",
    "address", "street_address", "latitude", "longitude", "phone",
    "phone_e164", "website", "rating", "review_count", "website_status",
    "official_website_url", "enrichment_status", "first_seen_job_id",
    "tile_id", "created_at", "updated_at"
]


def format_region_folder_name(country_code: str, region_code: str, region_name: str) -> str:
    """
    Safely normalizes region name and code into standard folder name:
    e.g. 'Alaska', 'AK' -> 'Alaska_AK'
    e.g. 'Ciudad de México', 'CMX' -> 'Ciudad_de_Mexico_CMX'
    e.g. 'Estado de México', 'MEX' -> 'Estado_de_Mexico_MEX'
    """
    norm = unicodedata.normalize('NFKD', region_name).encode('ASCII', 'ignore').decode('utf-8')
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', norm.strip()).strip('_')
    rc = region_code.strip().upper()
    if slug.upper().endswith(f"_{rc}"):
        return slug
    return f"{slug}_{rc}"


def resolve_safe_export_path(base_dir: Path, requested_rel_path: str) -> Path:
    """
    Validates that requested relative path stays strictly within base exports directory.
    Guards against path traversal (e.g. '../..').
    """
    clean_rel = Path(requested_rel_path).as_posix().lstrip("/\\")
    target_path = (base_dir / clean_rel).resolve()
    base_resolved = base_dir.resolve()
    try:
        target_path.relative_to(base_resolved)
    except ValueError:
        raise PermissionError(f"Access denied: Path '{requested_rel_path}' is outside exports directory.")
    return target_path


def export_state_category_data(
    db: Database,
    base_exports_dir: Path,
    run_id: str,
    run_date: str,
    country_code: str,
    region_code: str,
    region_name: str,
    category: str,
    export_format: str = "csv"
) -> Dict[str, Any]:
    """
    Exports a single category's collected businesses for a state into its organized folder:
    data/exports/YYYY-MM-DD/COUNTRY/REGION_NAME_REGION_CODE/<category_slug>.csv
    """
    cc = country_code.strip().upper()
    rc = region_code.strip().upper()
    folder_name = format_region_folder_name(cc, rc, region_name)
    export_dir = base_exports_dir / run_date / cc / folder_name
    export_dir.mkdir(parents=True, exist_ok=True)

    cat_slug = re.sub(r'[^a-zA-Z0-9]+', '_', category.strip().lower()).strip('_') or "all"

    # Query businesses matching country, region, category/vertical
    with db.get_connection() as conn:
        if cat_slug in ("all", "all_businesses"):
            rows = conn.execute("""
                SELECT * FROM places
                WHERE country_code = ? AND region = ?
                ORDER BY name ASC, cid ASC
            """, (cc, rc)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM places
                WHERE country_code = ? AND region = ?
                AND (lower(vertical) = lower(?) OR lower(category) = lower(?))
                ORDER BY name ASC, cid ASC
            """, (cc, rc, category, category)).fetchall()
        places = [dict(r) for r in rows]

    df = pd.DataFrame(places) if places else pd.DataFrame(columns=STATE_EXPORT_COLUMNS)
    for col in STATE_EXPORT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[STATE_EXPORT_COLUMNS]

    # Guard and verify: exported row count must strictly match database query result
    if len(df) != len(places):
        raise ValueError(
            f"Export row count mismatch for {cc}-{rc} category '{category}': "
            f"DataFrame has {len(df)} rows, database query returned {len(places)}."
        )

    filename = f"{cat_slug}.{export_format}"
    filepath = export_dir / filename

    if export_format == "csv":
        df.to_csv(filepath, index=False, encoding="utf-8")
    elif export_format == "jsonl":
        df.to_json(filepath, orient="records", lines=True, force_ascii=False)
    else:
        df.to_csv(filepath, index=False, encoding="utf-8")

    return {
        "category": category,
        "count": len(df),
        "file": str(filepath),
        "rel_path": str(filepath.relative_to(base_exports_dir)),
    }


def export_state_complete_package(
    db: Database,
    base_exports_dir: Optional[Path] = None,
    run_id: str = "manual_export",
    run_date: Optional[str] = None,
    country_code: Optional[str] = None,
    region_code: Optional[str] = None,
    region_name: Optional[str] = None,
    categories: Optional[List[str]] = None,
    export_format: str = "csv",
    **kwargs: Any
) -> Dict[str, Any]:
    """
    Generates or refreshes the full state package after all selected tasks finish:
    - <category_slug>.csv for each category
    - all_businesses.csv
    - run_summary.json (with tile completeness metrics)
    """
    if base_exports_dir is None:
        base_exports_dir = kwargs.get("exports_base_dir")
    if base_exports_dir is None:
        raise ValueError("base_exports_dir is required")
    base_exports_dir = Path(base_exports_dir)

    cc = (country_code or kwargs.get("country", "US")).strip().upper()
    rc = (region_code or kwargs.get("region", "")).strip().upper()

    if not run_date:
        run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not region_name:
        try:
            from .scope import get_region
            region_name = get_region(cc, rc).name
        except Exception:
            region_name = rc

    if categories is None:
        from .orchestrator import DEFAULT_CATEGORIES
        db_cats = set()
        with db.get_connection() as conn:
            c_rows = conn.execute("""
                SELECT DISTINCT category FROM places WHERE country_code = ? AND region = ? AND category IS NOT NULL
                UNION
                SELECT DISTINCT vertical FROM places WHERE country_code = ? AND region = ? AND vertical IS NOT NULL
                UNION
                SELECT DISTINCT category FROM geographic_tiles WHERE country_code = ? AND region_code = ? AND category IS NOT NULL
            """, (cc, rc, cc, rc, cc, rc)).fetchall()
            for r in c_rows:
                if r[0] and str(r[0]).strip():
                    db_cats.add(str(r[0]).strip())

        seen = set()
        categories = []
        for c in list(DEFAULT_CATEGORIES) + sorted(db_cats):
            if c not in seen:
                seen.add(c)
                categories.append(c)

    folder_name = format_region_folder_name(cc, rc, region_name)
    export_dir = base_exports_dir / run_date / cc / folder_name
    export_dir.mkdir(parents=True, exist_ok=True)

    category_files: List[Dict[str, Any]] = []
    category_counts: Dict[str, int] = {}

    for cat in categories:
        res = export_state_category_data(
            db=db,
            base_exports_dir=base_exports_dir,
            run_id=run_id,
            run_date=run_date,
            country_code=cc,
            region_code=rc,
            region_name=region_name,
            category=cat,
            export_format=export_format
        )
        category_files.append(res)
        category_counts[cat] = res["count"]

    # Generate all_businesses.csv
    with db.get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM places
            WHERE country_code = ? AND region = ?
            ORDER BY name ASC, cid ASC
        """, (cc, rc)).fetchall()
        all_places = [dict(r) for r in rows]

    df_all = pd.DataFrame(all_places) if all_places else pd.DataFrame(columns=STATE_EXPORT_COLUMNS)
    for col in STATE_EXPORT_COLUMNS:
        if col not in df_all.columns:
            df_all[col] = None
    df_all = df_all[STATE_EXPORT_COLUMNS]

    all_file = export_dir / f"all_businesses.{export_format}"
    if export_format == "csv":
        df_all.to_csv(all_file, index=False, encoding="utf-8")
    elif export_format == "jsonl":
        df_all.to_json(all_file, orient="records", lines=True, force_ascii=False)
    else:
        df_all.to_csv(all_file, index=False, encoding="utf-8")

    # Fetch geographic tile metrics for this region
    tile_counts = db.get_pipeline_tile_counts(country_code=cc, region_code=rc)
    has_incomplete = (
        tile_counts.get("pending", 0) > 0 or
        tile_counts.get("running", 0) > 0 or
        tile_counts.get("retry_pending", 0) > 0 or
        tile_counts.get("failed", 0) > 0
    )

    if tile_counts.get("total", 0) == 0:
        completeness = "pending"
    elif has_incomplete:
        completeness = "partially_completed"
    else:
        completeness = "fully_completed"

    # Count websites verified & enriched in this state
    with db.get_connection() as conn:
        row_disc = conn.execute("""
            SELECT count(*) as cnt FROM places
            WHERE country_code = ? AND region = ? AND website_status = 'verified'
        """, (cc, rc)).fetchone()
        websites_verified = row_disc["cnt"] if row_disc else 0

        row_enr = conn.execute("""
            SELECT count(*) as cnt FROM places
            WHERE country_code = ? AND region = ? AND enrichment_status = 'completed'
        """, (cc, rc)).fetchone()
        companies_enriched = row_enr["cnt"] if row_enr else 0

        # Collection errors for tasks in this region
        err_rows = conn.execute("""
            SELECT task_id, category, last_error FROM collection_tasks
            WHERE country_code = ? AND region_code = ? AND last_error IS NOT NULL
        """, (cc, rc)).fetchall()
        task_errors = [dict(e) for e in err_rows]

    summary_data = {
        "run_id": run_id,
        "run_date": run_date,
        "export_timestamp": datetime.now().isoformat(),
        "country_code": cc,
        "region_code": rc,
        "region_name": region_name,
        "folder_name": folder_name,
        "relative_folder": f"{run_date}/{cc}/{folder_name}",
        "categories_processed": categories,
        "total_businesses_in_region": len(df_all),
        "category_counts": category_counts,
        "tile_metrics": tile_counts,
        "completeness_status": completeness,
        "has_incomplete_tiles": has_incomplete,
        "websites_verified": websites_verified,
        "companies_enriched": companies_enriched,
        "errors": task_errors,
    }

    summary_file = export_dir / "run_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)

    return {
        "country": cc,
        "region_code": rc,
        "region_name": region_name,
        "export_folder": str(export_dir),
        "export_dir": str(export_dir),
        "relative_dir": f"{run_date}/{cc}/{folder_name}",
        "total_businesses": len(df_all),
        "all_businesses_count": len(df_all),
        "categories": categories,
        "completeness_status": completeness,
        "summary": summary_data,
        "files": [str(all_file), str(summary_file)] + [cf["file"] for cf in category_files]
    }


def list_all_state_exports(base_exports_dir: Path) -> List[Dict[str, Any]]:
    """
    Discovers all generated state export packages and parses run_summary.json.
    Returns structured list sorted newest date first.
    """
    if not base_exports_dir.exists():
        return []

    results: List[Dict[str, Any]] = []
    # Match data/exports/YYYY-MM-DD/COUNTRY/REGION_NAME_REGION_CODE/run_summary.json
    for summary_path in base_exports_dir.glob("*/*/*/run_summary.json"):
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            folder = summary_path.parent
            files = [p.name for p in folder.glob("*") if p.is_file()]
            data["files"] = files
            data["full_path"] = str(folder)
            data["total_businesses"] = data.get("total_businesses_in_region", 0)
            data["country"] = data.get("country_code")
            data["categories"] = data.get("categories_processed", [])
            data["all_businesses_csv"] = str(folder / "all_businesses.csv")
            data["run_summary_json"] = str(summary_path)
            results.append(data)
        except Exception:
            continue

    # Sort by run_date descending, then country, then region
    results.sort(key=lambda x: (x.get("run_date", ""), x.get("country_code", ""), x.get("region_code", "")), reverse=True)
    return results


RAW_EXPORT_COLUMNS = [
    "cid", "raw_id", "name", "vertical", "category", "address",
    "street_address", "city", "region", "country_code", "postal_code",
    "phone", "phone_e164", "website", "latitude", "longitude",
    "rating", "review_count", "tile_id", "search_task_id", "run_id", "collected_at"
]


DEFAULT_CONFIGURED_CATEGORIES = [
    "healthcare", "hospitality", "food", "retail", "fitness",
    "beauty", "automotive", "finance", "education", "real_estate"
]


def resolve_record_configured_category(
    record: Dict[str, Any],
    configured_categories: Optional[List[str]] = None
) -> str:
    """
    Resolves the pipeline collection category for a raw business discovery record.
    Matches search_task_id, tile_id, vertical, or taxonomy classification against
    configured collection categories. Never returns individual business types as category names.
    """
    target_cats = list(configured_categories or DEFAULT_CONFIGURED_CATEGORIES)
    valid_map = {c.strip().lower(): c.strip().lower() for c in target_cats if c.strip()}

    # 1. Match search_task_id suffix/pattern (e.g. col_us_nv_healthcare, col_us_nv_real_estate, task_food)
    task_id = (record.get("search_task_id") or "").strip().lower()
    if task_id:
        for c in sorted(valid_map.keys(), key=len, reverse=True):
            if task_id.endswith(f"_{c}") or task_id.endswith(f":{c}") or task_id == c or f"_{c}_" in task_id:
                return valid_map[c]

    # 2. Match tile_id pattern (e.g. t_us_nv_healthcare_p0_y0_x0, tile_nv_healthcare_1)
    tile_id = (record.get("tile_id") or "").strip().lower()
    if tile_id:
        for c in sorted(valid_map.keys(), key=len, reverse=True):
            if f"_{c}_" in tile_id or tile_id.startswith(f"{c}_") or tile_id.endswith(f"_{c}"):
                return valid_map[c]

    # 3. Match record vertical directly
    vert = (record.get("vertical") or "").strip().lower()
    if vert and vert in valid_map:
        return valid_map[vert]

    # 4. Check category or vertical_raw
    for cat_key in ("category", "vertical_raw"):
        val = (record.get(cat_key) or "").strip()
        if val:
            if val.lower() in valid_map:
                return valid_map[val.lower()]
            try:
                from .taxonomy import classify_vertical
                m_vert, _ = classify_vertical(val)
                if m_vert and m_vert.lower() in valid_map:
                    return valid_map[m_vert.lower()]
            except Exception:
                pass

    return "other"


def export_live_raw_places(
    db: Database,
    base_exports_dir: Union[str, Path] = "data/exports",
    country_code: str = "US",
    region_code: str = "NV",
    region_name: Optional[str] = None,
    categories: Optional[List[str]] = None,
    collection_date: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Exports newly committed raw records to date-wise, category-wise structure:
    data/exports/live_raw/{date}/{country_code}/{region_name}_{region_code}/

    Produces:
    - all_businesses.csv and all_businesses.jsonl
    - {category}.csv and {category}.jsonl for each configured category
    - export_summary.json

    Guarantees:
    - Only configured categories get files (no individual business-type files).
    - Multi-date support: earlier dates remain in their own directories.
    - Zero duplicates on repeat exports / resumes.
    - Atomic file writes using temp files.
    - Primary SQLite database is never modified or corrupted.
    """
    if "base_dir" in kwargs:
        base_exports_dir = kwargs["base_dir"]
    cc = country_code.strip().upper()
    rc = region_code.strip().upper()

    # Resolve full region name if missing
    resolved_reg_name = region_name or kwargs.get("region") or rc
    if not resolved_reg_name or resolved_reg_name.upper() == rc:
        try:
            from .scope import get_region
            reg_obj = get_region(cc, rc)
            if reg_obj and reg_obj.name:
                resolved_reg_name = reg_obj.name
        except Exception:
            resolved_reg_name = rc

    region_folder = format_region_folder_name(cc, rc, resolved_reg_name)
    configured_cats = list(categories or DEFAULT_CONFIGURED_CATEGORIES)
    base_export_path = Path(base_exports_dir)

    try:
        # Fetch all records for this state from SQLite
        with db.get_connection() as conn:
            rows = conn.execute(f"""
                SELECT {', '.join(RAW_EXPORT_COLUMNS)},
                       COALESCE(date(collected_at, 'localtime'), substr(collected_at, 1, 10)) as col_date
                FROM raw_places
                WHERE UPPER(country_code) = ? AND UPPER(region) = ?
                ORDER BY id ASC
            """, (cc, rc)).fetchall()
            all_records = [dict(r) for r in rows]

        # Determine target collection dates to export
        if collection_date:
            target_dates = [collection_date]
        else:
            found_dates = sorted(list(set(r.get("col_date") for r in all_records if r.get("col_date"))))
            if not found_dates:
                target_dates = [datetime.now().strftime("%Y-%m-%d")]
            else:
                target_dates = found_dates

        last_summary = {}
        now_str = datetime.now().strftime("%H:%M:%S")

        for date_str in target_dates:
            target_dir = base_export_path / "live_raw" / date_str / cc / region_folder
            target_dir.mkdir(parents=True, exist_ok=True)

            # Filter records strictly for this collection date
            date_records = [r for r in all_records if r.get("col_date") == date_str] if all_records else []
            total_count = len(date_records)

            # Clean and prepare records with resolved category
            prepared_records = []
            by_cat: Dict[str, List[Dict[str, Any]]] = {c: [] for c in configured_cats}

            for r in date_records:
                rec_copy = {col: r.get(col) for col in RAW_EXPORT_COLUMNS}
                resolved_cat = resolve_record_configured_category(r, configured_cats)
                rec_copy["vertical"] = resolved_cat
                rec_copy["category"] = r.get("category") or r.get("vertical_raw") or resolved_cat
                prepared_records.append(rec_copy)
                if resolved_cat in by_cat:
                    by_cat[resolved_cat].append(rec_copy)
                else:
                    by_cat.setdefault(resolved_cat, []).append(rec_copy)

            # 1. State-level all_businesses.csv and all_businesses.jsonl
            all_csv_path = target_dir / "all_businesses.csv"
            all_jsonl_path = target_dir / "all_businesses.jsonl"
            tmp_csv = target_dir / ".all_businesses.csv.tmp"
            tmp_jsonl = target_dir / ".all_businesses.jsonl.tmp"

            with open(tmp_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=RAW_EXPORT_COLUMNS)
                writer.writeheader()
                if prepared_records:
                    writer.writerows(prepared_records)
            os.replace(tmp_csv, all_csv_path)

            with open(tmp_jsonl, "w", encoding="utf-8") as f:
                for r in prepared_records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(tmp_jsonl, all_jsonl_path)

            # Compatible aliases for legacy consumers: all_raw_places.csv / all_raw_places.jsonl
            alt_csv_path = target_dir / "all_raw_places.csv"
            alt_jsonl_path = target_dir / "all_raw_places.jsonl"
            tmp_alt_csv = target_dir / ".all_raw_places.csv.tmp"
            tmp_alt_jsonl = target_dir / ".all_raw_places.jsonl.tmp"
            with open(tmp_alt_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=RAW_EXPORT_COLUMNS)
                writer.writeheader()
                if prepared_records:
                    writer.writerows(prepared_records)
            os.replace(tmp_alt_csv, alt_csv_path)

            with open(tmp_alt_jsonl, "w", encoding="utf-8") as f:
                for r in prepared_records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(tmp_alt_jsonl, alt_jsonl_path)

            # 2. Per configured-category CSV and JSONL (ONLY configured categories!)
            category_counts = {}
            cat_files = [str(all_csv_path), str(all_jsonl_path)]

            for cat_name in configured_cats:
                cat_records = by_cat.get(cat_name, [])
                cat_slug = re.sub(r'[^a-zA-Z0-9]+', '_', cat_name).strip('_') or "all"
                category_counts[cat_name] = len(cat_records)

                cat_csv_path = target_dir / f"{cat_slug}.csv"
                cat_jsonl_path = target_dir / f"{cat_slug}.jsonl"
                tmp_cat_csv = target_dir / f".{cat_slug}.csv.tmp"
                tmp_cat_jsonl = target_dir / f".{cat_slug}.jsonl.tmp"

                with open(tmp_cat_csv, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=RAW_EXPORT_COLUMNS)
                    writer.writeheader()
                    if cat_records:
                        writer.writerows(cat_records)
                os.replace(tmp_cat_csv, cat_csv_path)

                with open(tmp_cat_jsonl, "w", encoding="utf-8") as f:
                    for r in cat_records:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                os.replace(tmp_cat_jsonl, cat_jsonl_path)

                cat_files.extend([str(cat_csv_path), str(cat_jsonl_path)])

            # 3. Export summary metadata
            summary_path = target_dir / "export_summary.json"
            tmp_summary = target_dir / ".export_summary.json.tmp"
            summary_data = {
                "date": date_str,
                "country": cc,
                "country_code": cc,
                "region": rc,
                "region_code": rc,
                "region_name": resolved_reg_name,
                "last_successful_export_time": now_str,
                "last_export_timestamp": datetime.now().isoformat(),
                "total_records": total_count,
                "total_exported_rows": total_count,
                "by_category": category_counts,
                "category_counts": category_counts,
                "export_directory": str(target_dir),
            }
            with open(tmp_summary, "w", encoding="utf-8") as f:
                json.dump(summary_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_summary, summary_path)

            last_summary = {
                "status": "success",
                "date": date_str,
                "export_dir": str(target_dir),
                "export_path": str(target_dir),
                "total_exported_rows": total_count,
                "records_exported": total_count,
                "last_export_time": now_str,
                "last_successful_export_time": now_str,
                "category_counts": category_counts,
                "by_category": category_counts,
                "files": cat_files,
                "dates_exported": target_dates
            }

        return last_summary or {
            "status": "success",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "export_dir": str(base_export_path / "live_raw" / datetime.now().strftime("%Y-%m-%d") / cc / region_folder),
            "export_path": str(base_export_path / "live_raw" / datetime.now().strftime("%Y-%m-%d") / cc / region_folder),
            "total_exported_rows": 0,
            "records_exported": 0,
            "last_export_time": now_str,
            "last_successful_export_time": now_str,
            "category_counts": {c: 0 for c in configured_cats},
            "by_category": {c: 0 for c in configured_cats},
            "files": [],
            "dates_exported": [datetime.now().strftime("%Y-%m-%d")]
        }
    except Exception as e:
        logger.warning(f"Error during live raw export: {e}")
        return {
            "status": "error",
            "error": str(e),
            "export_dir": str(base_export_path / "live_raw" / datetime.now().strftime("%Y-%m-%d") / cc / region_folder),
            "records_exported": 0
        }

