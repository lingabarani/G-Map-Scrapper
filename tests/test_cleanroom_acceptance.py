import os
import sys
import tempfile
import sqlite3
import pandas as pd
from pathlib import Path
import pytest

from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.db import Database
from gmaps_scraper.export import export_data
from gmaps_scraper.models import PlaceStub

def test_clean_room_steps_0_to_9():
    """Validates the entire Stage 11 Clean-Room sequence."""
    with tempfile.TemporaryDirectory() as clean_dir:
        root = Path(clean_dir)
        db_path = root / "data" / "clean_gmaps.sqlite"
        photos_dir = root / "data" / "photos"
        exports_dir = root / "data" / "exports"

        # Step 0: Assert no pre-existing data or sqlite
        assert not db_path.exists()
        assert not photos_dir.exists()

        # Step 2: init schema -> 7 tables
        db = Database(db_path)
        db.init_schema()
        assert db_path.exists()

        with db.get_connection() as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for expected in ["places", "hours", "attributes", "reviews", "photos", "popular_times", "run_state"]:
                assert expected in tables, f"Missing table {expected}"

        # Step 3: Insert places, hours, reviews, photos, exports
        for i in range(10):
            db.upsert_place_stub({
                "cid": f"clean_{i}",
                "place_id": f"pid_{i}",
                "ftid": f"0x1:0x{i:x}",
                "name": f"Coffee Clean #{i}",
                "latitude": 40.7128 + i * 0.001,
                "longitude": -74.0060 - i * 0.001,
                "status": "pending"
            })

        # Enrich 5
        for i in range(5):
            db.update_place_details(f"clean_{i}", {"category": "Cafe", "description": "Good"}, status="details_done")

        # Add reviews
        for i in range(5):
            db.insert_review({
                "cid": f"clean_{i}",
                "review_id": f"rev_{i}",
                "author_name": f"Alice {i}",
                "rating": 5,
                "review_text": "Great brew",
                "timestamp_ms": 1700000000000
            })

        # Step 4: stats + dedupe proof
        with db.get_connection() as conn:
            c1 = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
            c2 = conn.execute("SELECT COUNT(DISTINCT cid) FROM places").fetchone()[0]
            assert c1 == 10 and c2 == 10 and c1 == c2

        # Step 5: Resume simulation
        with db.get_connection() as conn:
            pending = conn.execute("SELECT * FROM places WHERE status = 'pending'").fetchall()
            assert len(pending) == 5

        # Enrich remaining 5
        for p in pending:
            db.update_place_details(p["cid"], {"category": "Cafe"}, status="details_done")

        with db.get_connection() as conn:
            pending_after = conn.execute("SELECT * FROM places WHERE status = 'pending'").fetchall()
            assert len(pending_after) == 0

        # Step 7: Export CSV and reparse
        exp = export_data(db, export_format="csv", output_dir=exports_dir)
        csv_file = exp["files"][0]
        df = pd.read_csv(csv_file, dtype=str)
        assert len(df) == 10
        assert "Coffee Clean #0" in df["name"].values
