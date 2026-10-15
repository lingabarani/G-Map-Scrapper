import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from .db import Database

class CheckpointManager:
    def __init__(self, db: Database, run_id: Optional[str] = None):
        self.db = db
        self.run_id = run_id or f"run_{int(time.time())}"
        self.total_discovered = 0
        self.total_enriched = 0
        self.total_reviews = 0
        self.total_photos = 0
        self.errors_count = 0
        self.status = "idle"
        self.current_step = "idle"
        self.start_time = datetime.now()

    def start_run(self, query: str, bbox: str, step: str = "search"):
        self.status = "running"
        self.current_step = step
        with self.db.get_connection() as conn:
            conn.execute("""
                INSERT INTO run_state (
                    run_id, query, bbox, status, current_step,
                    total_discovered, total_enriched, total_reviews, total_photos,
                    errors_count, start_time, last_active
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = 'running',
                    last_active = CURRENT_TIMESTAMP
            """, (self.run_id, query, bbox, self.status, self.current_step))
            conn.commit()

    def update_progress(self, step: Optional[str] = None, discovered: int = 0, enriched: int = 0, reviews: int = 0, photos: int = 0, errors: int = 0):
        if step:
            self.current_step = step
        self.total_discovered += discovered
        self.total_enriched += enriched
        self.total_reviews += reviews
        self.total_photos += photos
        self.errors_count += errors

        with self.db.get_connection() as conn:
            conn.execute("""
                UPDATE run_state SET
                    current_step = :step,
                    total_discovered = total_discovered + :discovered,
                    total_enriched = total_enriched + :enriched,
                    total_reviews = total_reviews + :reviews,
                    total_photos = total_photos + :photos,
                    errors_count = errors_count + :errors,
                    last_active = CURRENT_TIMESTAMP
                WHERE run_id = :run_id
            """, {
                "run_id": self.run_id,
                "step": self.current_step,
                "discovered": discovered,
                "enriched": enriched,
                "reviews": reviews,
                "photos": photos,
                "errors": errors,
            })
            conn.commit()

    def complete_run(self):
        self.status = "completed"
        with self.db.get_connection() as conn:
            conn.execute("""
                UPDATE run_state SET
                    status = 'completed',
                    end_time = CURRENT_TIMESTAMP,
                    last_active = CURRENT_TIMESTAMP
                WHERE run_id = ?
            """, (self.run_id,))
            conn.commit()

    def finish_run(self):
        self.complete_run()

    def get_progress(self) -> Dict[str, Any]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM run_state ORDER BY start_time DESC LIMIT 1").fetchone()
            if not row:
                return {
                    "run_id": None, "status": "idle", "current_step": "idle",
                    "total_discovered": 0, "total_enriched": 0, "total_reviews": 0,
                    "total_photos": 0, "errors_count": 0, "eta_seconds": None
                }
            d = dict(row)
            # Estimate ETA if running
            eta = None
            if d.get("status") == "running" and d.get("total_discovered", 0) > 0:
                pending = d["total_discovered"] - d.get("total_enriched", 0)
                if pending > 0:
                    eta = pending * 1.5  # ~1.5s per item
            d["eta_seconds"] = eta
            return d
