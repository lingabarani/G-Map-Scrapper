import logging
import json
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from .db import Database
from .taxonomy import enrich_place_dimensions

logger = logging.getLogger(__name__)


def normalize_business_name(name: Optional[str]) -> str:
    """Normalizes business name by trimming whitespace and normalizing internal spaces."""
    if not name:
        return ""
    # Collapse multiple whitespaces
    return " ".join(name.strip().split())


def normalize_url(url: Optional[str]) -> Optional[str]:
    """Cleans up website URL."""
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned or cleaned.lower() in ("null", "none", "n/a"):
        return None
    return cleaned


def merge_cluster_records(cluster: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Normalizes and merges multiple raw discovery records for the same business
    into a single canonical place record.
    """
    if not cluster:
        raise ValueError("Cannot merge empty record cluster")

    # Sort cluster by collected_at or id so earliest discovery is first
    sorted_cluster = sorted(cluster, key=lambda r: (r.get("collected_at") or "", r.get("id") or 0))

    primary = sorted_cluster[0]
    cid = primary["cid"]

    # Names: pick the longest normalized name across discovery events
    names = [normalize_business_name(r.get("name")) for r in sorted_cluster if r.get("name")]
    best_name = max(names, key=len) if names else normalize_business_name(primary.get("name") or "")

    # FTID and Place ID
    best_ftid = next((r.get("ftid") for r in sorted_cluster if r.get("ftid")), primary.get("ftid"))
    best_place_id = next((r.get("place_id") for r in sorted_cluster if r.get("place_id")), primary.get("place_id"))

    # Street Address & Address
    street_addresses = [r.get("street_address").strip() for r in sorted_cluster if r.get("street_address") and r.get("street_address").strip()]
    best_street_address = street_addresses[0] if street_addresses else None

    addresses = [r.get("address").strip() for r in sorted_cluster if r.get("address") and r.get("address").strip()]
    best_address = best_street_address or (addresses[0] if addresses else None)

    # Coordinates: pick first valid lat/lng
    best_lat = next((float(r["latitude"]) for r in sorted_cluster if r.get("latitude") is not None), None)
    best_lng = next((float(r["longitude"]) for r in sorted_cluster if r.get("longitude") is not None), None)

    # Phone numbers: prefer E.164 if available
    phones_e164 = [r.get("phone_e164").strip() for r in sorted_cluster if r.get("phone_e164") and r.get("phone_e164").strip()]
    phones_raw = [r.get("phone").strip() for r in sorted_cluster if r.get("phone") and r.get("phone").strip()]
    best_phone_e164 = phones_e164[0] if phones_e164 else None
    best_phone = phones_raw[0] if phones_raw else best_phone_e164

    # Website
    websites = [normalize_url(r.get("website")) for r in sorted_cluster if normalize_url(r.get("website"))]
    best_website = websites[0] if websites else None

    # Categories: aggregate all seen categories
    all_categories: Set[str] = set()
    category_counts: Dict[str, int] = {}
    for r in sorted_cluster:
        cat = r.get("category")
        if cat and cat.strip():
            clean_c = cat.strip()
            all_categories.add(clean_c)
            category_counts[clean_c] = category_counts.get(clean_c, 0) + 1

    # Primary category is the most frequent
    primary_category = (
        max(category_counts.items(), key=lambda item: item[1])[0]
        if category_counts
        else primary.get("category")
    )

    # Ratings & Reviews: take highest review count and latest rating
    max_reviews = max((int(r.get("review_count") or 0) for r in sorted_cluster), default=0)
    ratings = [float(r["rating"]) for r in sorted_cluster if r.get("rating") is not None]
    best_rating = ratings[-1] if ratings else None

    # Country & Region
    best_country = next((r.get("country") for r in sorted_cluster if r.get("country")), primary.get("country"))
    best_country_code = next((r.get("country_code") for r in sorted_cluster if r.get("country_code")), primary.get("country_code"))
    best_region = next((r.get("region") for r in sorted_cluster if r.get("region")), primary.get("region"))
    best_city = next((r.get("city") for r in sorted_cluster if r.get("city")), primary.get("city"))
    best_postal_code = next((r.get("postal_code") for r in sorted_cluster if r.get("postal_code")), primary.get("postal_code"))

    first_seen_job = primary.get("run_id") or primary.get("search_task_id")

    canonical = {
        "cid": cid,
        "ftid": best_ftid,
        "place_id": best_place_id,
        "name": best_name,
        "address": best_address,
        "street_address": best_street_address,
        "latitude": best_lat,
        "longitude": best_lng,
        "website": best_website,
        "phone": best_phone,
        "phone_e164": best_phone_e164,
        "category": primary_category,
        "country": best_country,
        "country_code": best_country_code,
        "region": best_region,
        "city": best_city,
        "postal_code": best_postal_code,
        "rating": best_rating,
        "review_count": max_reviews,
        "status": "pending",
        "first_seen_job_id": first_seen_job,
    }

    # Taxonomy dimensional enrichment
    enrich_place_dimensions(canonical)
    return canonical


class DeduplicationService:
    """
    Dedicated offline and manual deduplication pipeline.
    Reads raw business discovery events from raw_places,
    normalizes and matches records into clusters by CID / place_id,
    and writes canonical business entities into the canonical 'places' table.
    Strictly preserves raw_places without modification.
    """

    def __init__(self, db: Database):
        self.db = db

    def run_deduplication(
        self,
        country_code: Optional[str] = None,
        region_code: Optional[str] = None,
        run_id: Optional[str] = None,
        batch_size: int = 1000,
        dry_run: bool = False,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Executes deduplication of raw collection records.
        - Reads records from raw_places matching optional filters.
        - Clusters records by business identity (primary: CID).
        - Merges attributes to form canonical places.
        - Writes canonical results to 'places' (unless dry_run).
        - Leaves 'raw_places' untouched.
        """
        logger.info(
            f"Starting Deduplication: country={country_code}, region={region_code}, "
            f"run_id={run_id}, dry_run={dry_run}"
        )

        # 1. Fetch raw places from database
        raw_records = self.db.get_raw_places(
            country_code=country_code,
            region_code=region_code,
            run_id=run_id,
        )

        total_scanned = len(raw_records)
        if total_scanned == 0:
            return {
                "raw_records_scanned": 0,
                "unique_canonical_places": 0,
                "duplicate_events_merged": 0,
                "new_places_created": 0,
                "existing_places_updated": 0,
                "dry_run": dry_run,
                "by_country": {},
                "by_region": {},
            }

        # 2. Cluster raw records by CID (and fallback to place_id or name+address if CID missing)
        clusters: Dict[str, List[Dict[str, Any]]] = {}
        for rec in raw_records:
            key = rec.get("cid")
            if not key:
                key = rec.get("place_id") or rec.get("ftid") or f"{rec.get('name')}:{rec.get('address')}"
            key = str(key)
            if key not in clusters:
                clusters[key] = []
            clusters[key].append(rec)

        unique_canonical = len(clusters)
        duplicate_events_merged = total_scanned - unique_canonical

        # Check existing canonical places to track new vs updated
        existing_places_map: Set[str] = set()
        with self.db.get_connection() as conn:
            p_rows = conn.execute("SELECT cid FROM places WHERE cid IS NOT NULL").fetchall()
            existing_places_map = {r[0] for r in p_rows}

        new_places_created = 0
        existing_places_updated = 0
        by_country: Dict[str, int] = {}
        by_region: Dict[str, int] = {}

        # 3. Merge each cluster and write to places
        canonical_batch: List[Dict[str, Any]] = []

        for idx, (cid_key, cluster) in enumerate(clusters.items()):
            canonical = merge_cluster_records(cluster)
            c_code = canonical.get("country_code") or "Unknown"
            r_code = canonical.get("region") or "Unknown"
            by_country[c_code] = by_country.get(c_code, 0) + 1
            by_region[r_code] = by_region.get(r_code, 0) + 1

            is_existing = canonical["cid"] in existing_places_map
            if is_existing:
                existing_places_updated += 1
            else:
                new_places_created += 1

            canonical_batch.append(canonical)

            if len(canonical_batch) >= batch_size:
                if not dry_run:
                    for item in canonical_batch:
                        self.db.upsert_place_stub(item)
                canonical_batch.clear()

            if on_progress and (idx + 1) % 100 == 0:
                on_progress({
                    "processed_clusters": idx + 1,
                    "total_clusters": unique_canonical,
                    "raw_scanned": total_scanned,
                })

        # Flush remainder
        if canonical_batch and not dry_run:
            for item in canonical_batch:
                self.db.upsert_place_stub(item)
            canonical_batch.clear()

        metrics = {
            "raw_records_scanned": total_scanned,
            "unique_canonical_places": unique_canonical,
            "duplicate_events_merged": duplicate_events_merged,
            "new_places_created": new_places_created,
            "existing_places_updated": existing_places_updated,
            "dry_run": dry_run,
            "by_country": by_country,
            "by_region": by_region,
        }

        logger.info(f"Deduplication finished: {metrics}")
        return metrics
