import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
from .db import Database

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
