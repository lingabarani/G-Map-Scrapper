import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd

from ..db import Database
from ..export import format_region_folder_name

INCREMENTAL_EXPORT_COLUMNS = [
    "cid",
    "place_id",
    "company_name",
    "country_code",
    "region",
    "entity_type",
    "person_name",
    "job_title",
    "field_name",
    "field_value",
    "normalized_value",
    "match_status",
    "conflict_existing_value",
    "match_reason",
    "verification_status",
    "confidence",
    "source_url",
    "source_type",
    "first_seen_at",
    "last_checked_at"
]


def export_additional_intelligence(
    db: Database,
    base_exports_dir: Path,
    country_code: str,
    region_code: Optional[str] = None,
    region_name: Optional[str] = None,
    run_date: Optional[str] = None,
    in_memory_findings: Optional[List[Dict[str, Any]]] = None,
    summary_counts: Optional[Dict[str, Any]] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Exports incremental business intelligence findings to:
    - additional_enrichment.csv
    - additional_enrichment_summary.json
    Preserving date-wise and region-wise conventions.
    """
    base_exports_dir = Path(base_exports_dir)
    cc = country_code.strip().upper()
    rc = (region_code or "").strip().upper()

    if not run_date:
        run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Resolve folder name
    r_name = region_name or rc
    folder_name = format_region_folder_name(cc, rc, r_name)
    export_dir = base_exports_dir / run_date / cc / folder_name
    export_dir.mkdir(parents=True, exist_ok=True)

    csv_path = export_dir / "additional_enrichment.csv"
    summary_path = export_dir / "additional_enrichment_summary.json"

    # If in_memory_findings is provided (e.g. from run result), use it;
    # otherwise query from database
    if in_memory_findings is not None:
        raw_rows = in_memory_findings
    else:
        raw_rows, _ = db.get_additional_intelligence(
            country_code=cc,
            region_code=rc,
            limit=100000
        )

    # Convert to DataFrame
    df = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame(columns=INCREMENTAL_EXPORT_COLUMNS)
    for col in INCREMENTAL_EXPORT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[INCREMENTAL_EXPORT_COLUMNS]

    # Save CSV
    df.to_csv(csv_path, index=False, encoding="utf-8")

    # Build summary
    db_summary = db.get_intelligence_summary(country_code=cc, region_code=rc)
    eff_counts = summary_counts or db_summary

    summary_data = {
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "run_date": run_date,
        "country_code": cc,
        "region_code": rc,
        "region_name": r_name,
        "dry_run": dry_run,
        "total_records_exported": len(df),
        "businesses_inspected": eff_counts.get("businesses_inspected", eff_counts.get("businesses_with_intel", 0)),
        "sources_attempted": eff_counts.get("sources_attempted", eff_counts.get("sources_checked", 0)),
        "sources_succeeded": eff_counts.get("sources_succeeded", 0),
        "sources_no_data": eff_counts.get("sources_no_data", 0),
        "sources_failed": eff_counts.get("sources_failed", 0),
        "new_findings": eff_counts.get("new_findings", 0),
        "existing_matches": eff_counts.get("existing_matches", eff_counts.get("exact_matches", 0)),
        "duplicates_prevented": eff_counts.get("duplicates_prevented", eff_counts.get("exact_matches", 0)),
        "conflicts": eff_counts.get("conflicts", 0),
        "records_requiring_review": eff_counts.get("conflicts", 0) + eff_counts.get("possible_matches", 0),
        "newly_discovered_leaders": eff_counts.get("new_leaders", 0),
        "newly_discovered_contacts": eff_counts.get("new_contacts", 0),
        "export_files": {
            "csv": str(csv_path),
            "csv_relative": str(csv_path.relative_to(base_exports_dir)),
            "summary_json": str(summary_path),
            "summary_json_relative": str(summary_path.relative_to(base_exports_dir))
        }
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)

    return {
        "csv_path": str(csv_path),
        "summary_path": str(summary_path),
        "records_count": len(df),
        "summary": summary_data
    }
