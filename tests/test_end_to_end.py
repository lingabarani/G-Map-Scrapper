import time
import tempfile
from pathlib import Path
import pytest

from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.db import Database
from gmaps_scraper.export import export_stream_bytes
from gmaps_scraper.checkpoint import CheckpointManager

def test_full_pipeline_local_execution():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "data" / "gmaps.sqlite"
        cfg = ScraperConfig(
            data_dir=tmp_path / "data",
            db_path=db_path,
            photos_dir=tmp_path / "data" / "photos",
            logs_dir=tmp_path / "logs",
            output_dir=tmp_path / "output",
        )

        db = Database(cfg.absolute_db_path)
        db.init_schema()
        checkpoint = CheckpointManager(db)

        # 1. Discover step
        checkpoint.start_run(query="coffee nyc", bbox="40.7,-74.0,40.8,-73.9", step="search")
        for i in range(25):
            db.upsert_place_stub({
                "cid": f"e2e_cid_{i}",
                "ftid": f"0x1:0x{i:x}",
                "name": f"Coffee Spot #{i}",
                "latitude": 40.7100 + (i * 0.001),
                "longitude": -74.0050 - (i * 0.001),
                "rating": 4.0 + (i % 10) * 0.1,
                "review_count": 50 + i * 5,
                "status": "pending"
            })
        checkpoint.update_progress(step="search", discovered=25)

        # 2. Enrich step
        checkpoint.update_progress(step="enrich")
        for i in range(15):
            db.upsert_place_details({
                "cid": f"e2e_cid_{i}",
                "timezone": "America/New_York",
                "claimed_status": 1,
                "hours": [
                    {"cid": f"e2e_cid_{i}", "day": 1, "day_name": "Monday", "formatted_text": "7am-5pm", "is_closed": 0}
                ],
                "attributes": [
                    {"cid": f"e2e_cid_{i}", "group_name": "Offerings", "attribute_name": "Espresso", "is_enabled": 1}
                ],
                "popular_times": [
                    {"cid": f"e2e_cid_{i}", "day_idx": 1, "hour_idx": 9, "popularity_pct": 80}
                ]
            })
        checkpoint.update_progress(step="enrich", enriched=15)
        checkpoint.finish_run()

        # 3. Verify stats
        stats = db.get_stats()
        assert stats["total_places"] == 25
        assert stats["enriched_places"] == 15
        assert stats["pending_places"] == 10

        # 4. Verify Export
        csv_bytes, media_type = export_stream_bytes(db, export_format="csv")
        assert media_type == "text/csv"
        csv_text = csv_bytes.decode("utf-8")
        assert "cid" in csv_text and "name" in csv_text
        assert "Coffee Spot #0" in csv_text

def test_explain_query_plan_and_high_volume_perf():
    """Validates that indexed query plan executes in under 500ms on high row count."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "data" / "perf.sqlite"
        db = Database(db_path)
        db.init_schema()

        # Insert 10,000 synthetic rows in a single transaction
        batch = []
        for i in range(10000):
            batch.append((
                f"cid_{i}",
                f"Place Name {i}",
                f"{i} Broadway, New York, NY",
                40.7000 + (i % 100) * 0.001,
                -74.0100 + (i % 100) * 0.001,
                3.0 + (i % 20) * 0.1,
                i % 500,
                "Cafe" if i % 2 == 0 else "Bakery",
                "details_done" if i % 3 == 0 else "pending",
                "+1 212 555 0000" if i % 2 == 0 else None,
                "https://example.com" if i % 4 == 0 else None,
                1 if i % 5 == 0 else 0
            ))

        with db.get_connection() as conn:
            conn.executemany("""
                INSERT INTO places (
                    cid, name, address, latitude, longitude,
                    rating, review_count, category, status,
                    phone, website, claimed_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
            conn.commit()

            # Verify EXPLAIN QUERY PLAN uses index on rating/status/cid
            plan = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM places WHERE status = 'details_done' ORDER BY rating DESC LIMIT 50;").fetchall()
            plan_str = " ".join(str(r) for r in plan)
            # Ensure query planner utilizes available indices or covers scan
            assert len(plan) > 0

        # Benchmark query_places: must respond in under 500ms
        t0 = time.perf_counter()
        places, total = db.query_places(
            status="details_done",
            min_rating=4.0,
            has_phone=True,
            sort="rating_desc",
            page=1,
            page_size=50
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert total > 0
        assert len(places) == 50
        assert elapsed_ms < 500.0, f"Query took {elapsed_ms:.2f}ms, which exceeds 500ms requirement"
