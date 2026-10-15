import os
import sys
import json
import sqlite3
import tempfile
import pandas as pd
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gmaps_scraper.config import load_config
from gmaps_scraper.db import Database
from gmaps_scraper.export import export_data, export_stream_bytes
from gmaps_scraper.photos import is_valid_image_magic

def verify_all():
    print("=== STARTING ADVERSARIAL VERIFICATION MATRIX ===")
    results = {}

    db_path = Path(__file__).resolve().parent.parent / "data" / "gmaps.sqlite"
    db = Database(db_path)

    # B. Schema & Data Integrity
    print("\n--- SECTION B: Schema & Data Integrity ---")
    with db.get_connection() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"B1: Tables present: {tables}")
        assert all(t in tables for t in ["places", "hours", "attributes", "reviews", "photos", "run_state", "places_fts"])

        # Check indexes
        indexes = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
        print(f"B1: Indexes: {indexes}")
        assert "idx_places_status" in indexes and "idx_places_rating" in indexes

        # B2: CID unique
        cids_count = conn.execute("SELECT COUNT(cid), COUNT(DISTINCT cid) FROM places").fetchone()
        print(f"B2: Places total={cids_count[0]}, distinct CIDs={cids_count[1]}")
        assert cids_count[0] == cids_count[1]

        # B3: No null name or null lat/lng
        null_coords = conn.execute("SELECT COUNT(*) FROM places WHERE name IS NULL OR latitude IS NULL OR longitude IS NULL").fetchone()[0]
        print(f"B3: Null name/coords count={null_coords}")
        assert null_coords == 0

        # B4: Coordinates range
        outliers = conn.execute("SELECT COUNT(*) FROM places WHERE latitude < -90 OR latitude > 90 OR longitude < -180 OR longitude > 180").fetchone()[0]
        print(f"B4: Lat/lng outliers count={outliers}")
        assert outliers == 0

        # B5: Valid status enum
        invalid_status = conn.execute("SELECT COUNT(*) FROM places WHERE status NOT IN ('pending', 'details_done', 'reviews_done', 'failed')").fetchone()[0]
        print(f"B5: Invalid status count={invalid_status}")
        assert invalid_status == 0

        # B6: Every review has parent place
        orphan_reviews = conn.execute("SELECT COUNT(*) FROM reviews WHERE cid NOT IN (SELECT cid FROM places)").fetchone()[0]
        print(f"B6: Orphan reviews count={orphan_reviews}")
        assert orphan_reviews == 0

        # B7: No orphan photos
        orphan_photos = conn.execute("SELECT COUNT(*) FROM photos WHERE cid NOT IN (SELECT cid FROM places)").fetchone()[0]
        print(f"B7: Orphan photos count={orphan_photos}")
        assert orphan_photos == 0

        # B8: Per-table consistency
        total_p = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        total_r = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        total_ph = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        print(f"B8: Places={total_p}, Reviews={total_r}, Photos={total_ph}")

    # G. Exports
    print("\n--- SECTION G: Exports ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        csv_res = export_data(db, export_format="csv", output_dir=tmp_path / "csv")
        csv_file = csv_res["files"][0]
        df_csv = pd.read_csv(csv_file, dtype=str)
        print(f"G1: CSV reparsed rows={len(df_csv)}, matches DB places={total_p}")
        assert len(df_csv) == total_p

        jsonl_res = export_data(db, export_format="jsonl", output_dir=tmp_path / "jsonl")
        jsonl_file = jsonl_res["files"][0]
        with open(jsonl_file, "r", encoding="utf-8") as f:
            j_lines = [json.loads(line) for line in f if line.strip()]
        print(f"G2: JSONL parsed rows={len(j_lines)}, matches DB places={total_p}")
        assert len(j_lines) == total_p

        parquet_res = export_data(db, export_format="parquet", output_dir=tmp_path / "parquet")
        parquet_file = parquet_res["files"][0]
        df_parquet = pd.read_parquet(parquet_file)
        print(f"G3: Parquet parsed rows={len(df_parquet)}, matches DB places={total_p}")
        assert len(df_parquet) == total_p

        # G4: Filtered export
        filtered_bytes, _ = export_stream_bytes(db, export_format="csv", filters={"status": "details_done"})
        df_filtered = pd.read_csv(pd.io.common.BytesIO(filtered_bytes), dtype=str)
        with db.get_connection() as conn:
            expected_details_done = conn.execute("SELECT COUNT(*) FROM places WHERE status = 'details_done'").fetchone()[0]
        print(f"G4: Filtered export rows={len(df_filtered)}, expected={expected_details_done}")
        assert len(df_filtered) == expected_details_done

    # E. Photos magic bytes
    print("\n--- SECTION E4: Photo Magic Bytes ---")
    photos_dir = Path(__file__).resolve().parent.parent / "data" / "photos"
    valid_photos = 0
    total_found_photos = 0
    for p in photos_dir.rglob("*.*"):
        if p.name.endswith(".json"): continue
        total_found_photos += 1
        data = p.read_bytes()
        if is_valid_image_magic(data):
            valid_photos += 1
    print(f"E4: Found local photos={total_found_photos}, Valid magic bytes={valid_photos}")
    assert total_found_photos > 0 and valid_photos == total_found_photos

    # A2: Grep API Keys in src
    print("\n--- SECTION A2: Assert No API Keys Required ---")
    import re
    src_dir = Path(__file__).resolve().parent.parent / "src"
    key_pattern = re.compile(r'(api[_-]?key|secret[_-]?key|google_api_key)\s*=\s*["\'][a-zA-Z0-9_\-]{20,}["\']', re.I)
    matches = []
    for pyfile in src_dir.rglob("*.py"):
        text = pyfile.read_text(encoding="utf-8", errors="replace")
        m = key_pattern.findall(text)
        if m:
            matches.append((str(pyfile), m))
    print(f"A2: API key pattern matches in code: {len(matches)}")
    assert len(matches) == 0

    print("\n=== ALL ADVERSARIAL MATRIX ASSERTIONS PASSED! ===")

if __name__ == "__main__":
    verify_all()
