import contextlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, Any
import pandas as pd
import pytest

from gmaps_scraper.db import Database
from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.storage import RegionalStorageManager
from gmaps_scraper.scope import get_region, RegionInfo


@contextlib.contextmanager
def open_sqlite(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def storage_environment():
    """Provides a fresh isolated test environment with central DB and directories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db_path = root / "data" / "gmaps.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)
        db.init_schema()

        cfg = ScraperConfig(
            db_path=str(db_path),
            photos_dir=str(root / "data" / "photos"),
            exports_dir=str(root / "data" / "exports"),
            logs_dir=str(root / "logs"),
            databases_dir=str(root / "data" / "databases"),
        )
        mgr = RegionalStorageManager(db, cfg)
        yield {
            "root": root,
            "db": db,
            "cfg": cfg,
            "mgr": mgr,
            "db_path": db_path,
        }


def _insert_sample_place(
    db: Database,
    cid: str,
    country_code: str,
    region: str,
    category: str,
    vertical: str,
    name: str = "Test Business"
):
    with db.get_connection() as conn:
        with conn:
            conn.execute("""
                INSERT OR REPLACE INTO places (
                    cid, ftid, place_id, name, address, category, vertical,
                    country_code, region, latitude, longitude
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cid, f"ftid_{cid}", f"pid_{cid}", name,
                f"123 Main St, {region}, {country_code}",
                category, vertical, country_code, region, 37.77, -122.41
            ))


# 1. Regional database initialization & schema compatibility
def test_regional_db_initialization(storage_environment):
    mgr: RegionalStorageManager = storage_environment["mgr"]
    reg_db = mgr.init_regional_database("US", "CA", "California")

    assert reg_db.db_path.exists()
    with open_sqlite(reg_db.db_path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

    assert "places" in tables
    assert "hours" in tables
    assert "reviews" in tables
    assert "attributes" in tables
    assert "photos" in tables
    assert "company_profiles" in tables
    assert "company_leaders" in tables
    assert "company_contacts" in tables
    assert "additional_intelligence" in tables
    assert "intelligence_sources" in tables
    assert "_sync_metadata" in tables


# 2. US and Mexico region-to-database path mapping
def test_region_path_mapping(storage_environment):
    mgr: RegionalStorageManager = storage_environment["mgr"]

    # US state
    us_path = mgr.get_regional_db_path("US", "CA", "California")
    assert us_path.parts[-3:] == ("databases", "US", "California_CA.sqlite")

    # Mexico federal entity
    mx_path = mgr.get_regional_db_path("MX", "JAL", "Jalisco")
    assert mx_path.parts[-3:] == ("databases", "MX", "Jalisco_JAL.sqlite")

    # Another US state
    tx_path = mgr.get_regional_db_path("US", "TX", "Texas")
    assert tx_path.parts[-3:] == ("databases", "US", "Texas_TX.sqlite")


# 3. Correct filtering of records by country and region
def test_country_and_region_filtering(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    # Insert places for CA, TX, and MX-JAL
    _insert_sample_place(db, "1001", "US", "CA", "Dentist", "healthcare", "CA Dental Clinic")
    _insert_sample_place(db, "1002", "US", "CA", "Hospital", "healthcare", "CA General Hospital")
    _insert_sample_place(db, "2001", "US", "TX", "Family Clinic", "healthcare", "TX Health Hub")
    _insert_sample_place(db, "3001", "MX", "JAL", "Dentista", "healthcare", "Guadalajara Dental")

    res_ca = mgr.sync_region("US", "CA")
    assert res_ca["places_synced"] == 2

    # Check CA regional database contents
    with open_sqlite(res_ca["regional_db_path"]) as ca_conn:
        cids = [r[0] for r in ca_conn.execute("SELECT cid FROM places").fetchall()]
    assert set(cids) == {"1001", "1002"}
    assert "2001" not in cids
    assert "3001" not in cids

    # Sync TX
    res_tx = mgr.sync_region("US", "TX")
    assert res_tx["places_synced"] == 1
    with open_sqlite(res_tx["regional_db_path"]) as tx_conn:
        tx_cids = [r[0] for r in tx_conn.execute("SELECT cid FROM places").fetchall()]
    assert tx_cids == ["2001"]


# 4. Related-table synchronization and foreign-key integrity
def test_related_tables_and_foreign_keys(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    cid = "5001"
    _insert_sample_place(db, cid, "US", "CA", "Medical Clinic", "healthcare", "Sacramento Health")

    with db.get_connection() as conn:
        with conn:
            # Reviews
            conn.execute("INSERT INTO reviews (review_id, cid, author_name, rating, text) VALUES (?, ?, ?, ?, ?)",
                         ("rev_1", cid, "Alice", 5.0, "Great doctor"))
            # Photos
            conn.execute("INSERT INTO photos (cid, photo_url) VALUES (?, ?)",
                         (cid, "https://photo.test/1.jpg"))
            # Hours
            conn.execute("INSERT INTO hours (cid, day, day_name, formatted_text) VALUES (?, ?, ?, ?)",
                         (cid, 1, "Monday", "08:00 - 17:00"))
            # Company Profile
            conn.execute("INSERT INTO company_profiles (cid, overview, industry) VALUES (?, ?, ?)",
                         (cid, "Sacramento Health Clinic Overview", "Healthcare"))
            # Company Contacts
            conn.execute("INSERT INTO company_contacts (cid, contact_type, contact_value) VALUES (?, ?, ?)",
                         (cid, "email", "info@sachealth.test"))
            # Additional Intelligence
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO additional_intelligence (cid, entity_type, field_name, field_value, normalized_value, match_status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (cid, "company", "license", "MD12345", "md12345", "exact_match"))
            intel_id = cur.lastrowid
            # Intelligence Sources
            conn.execute("""
                INSERT INTO intelligence_sources (intelligence_id, cid, source_url, source_type, source_title)
                VALUES (?, ?, ?, ?, ?)
            """, (intel_id, cid, "https://mb.ca.gov/12345", "official_registry", "State Medical Board"))

    res = mgr.sync_region("US", "CA")
    ca_path = res["regional_db_path"]

    # Open with foreign keys enabled and verify PRAGMA foreign_key_check
    with open_sqlite(ca_path) as conn_ca:
        conn_ca.execute("PRAGMA foreign_keys = ON;")
        fk_violations = conn_ca.execute("PRAGMA foreign_key_check;").fetchall()
        assert len(fk_violations) == 0

        assert conn_ca.execute("SELECT count(*) FROM reviews WHERE cid = ?", (cid,)).fetchone()[0] == 1
        assert conn_ca.execute("SELECT count(*) FROM photos WHERE cid = ?", (cid,)).fetchone()[0] == 1
        assert conn_ca.execute("SELECT count(*) FROM hours WHERE cid = ?", (cid,)).fetchone()[0] == 1
        assert conn_ca.execute("SELECT count(*) FROM company_profiles WHERE cid = ?", (cid,)).fetchone()[0] == 1
        assert conn_ca.execute("SELECT count(*) FROM company_contacts WHERE cid = ?", (cid,)).fetchone()[0] == 1
        assert conn_ca.execute("SELECT count(*) FROM additional_intelligence WHERE cid = ?", (cid,)).fetchone()[0] == 1
        assert conn_ca.execute("SELECT count(*) FROM intelligence_sources WHERE cid = ?", (cid,)).fetchone()[0] == 1


# 5. CID deduplication and idempotent repeated sync
def test_idempotent_repeated_sync(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    _insert_sample_place(db, "6001", "US", "CA", "Clinic A", "healthcare")
    _insert_sample_place(db, "6002", "US", "CA", "Clinic B", "healthcare")

    # First sync
    res1 = mgr.sync_region("US", "CA")
    assert res1["places_synced"] == 2

    # Second sync (idempotency check)
    res2 = mgr.sync_region("US", "CA")
    assert res2["places_synced"] == 2

    with open_sqlite(res2["regional_db_path"]) as conn:
        count = conn.execute("SELECT count(*) FROM places").fetchone()[0]
    assert count == 2


# 6. Updating existing records and handling changed region assignments
def test_changed_region_assignment_purging(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    # Place initially in CA
    _insert_sample_place(db, "7001", "US", "CA", "Mobile Clinic", "healthcare")
    mgr.sync_region("US", "CA")

    with open_sqlite(mgr.get_regional_db_path("US", "CA")) as ca_conn:
        assert ca_conn.execute("SELECT count(*) FROM places WHERE cid='7001'").fetchone()[0] == 1

    # Place region corrected to NV in central database
    with db.get_connection() as conn:
        with conn:
            conn.execute("UPDATE places SET region = 'NV' WHERE cid = '7001'")

    # Re-sync CA -> record must be purged from CA regional DB
    mgr.sync_region("US", "CA")
    with open_sqlite(mgr.get_regional_db_path("US", "CA")) as ca_conn:
        assert ca_conn.execute("SELECT count(*) FROM places WHERE cid='7001'").fetchone()[0] == 0

    # Sync NV -> record appears in NV regional DB
    mgr.sync_region("US", "NV")
    with open_sqlite(mgr.get_regional_db_path("US", "NV")) as nv_conn:
        assert nv_conn.execute("SELECT count(*) FROM places WHERE cid='7001'").fetchone()[0] == 1


# 7. CSV row counts matching database query results
def test_csv_export_count_matches_db(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    # 3 healthcare places, 2 food places in CA
    _insert_sample_place(db, "8001", "US", "CA", "Dental Care", "healthcare")
    _insert_sample_place(db, "8002", "US", "CA", "Eye Care", "healthcare")
    _insert_sample_place(db, "8003", "US", "CA", "Pediatric Care", "healthcare")
    _insert_sample_place(db, "8004", "US", "CA", "Taco Truck", "food")
    _insert_sample_place(db, "8005", "US", "CA", "Bakery", "food")

    exp_res = mgr.export_region(
        country_code="US",
        region_code="CA",
        region_name="California",
        categories=["healthcare", "food"]
    )

    export_folder = Path(exp_res["export_folder"])
    health_csv = export_folder / "healthcare.csv"
    food_csv = export_folder / "food.csv"

    assert health_csv.exists()
    assert food_csv.exists()

    df_health = pd.read_csv(health_csv)
    df_food = pd.read_csv(food_csv)

    assert len(df_health) == 3
    assert len(df_food) == 2


# 8. JSON summary generation
def test_json_summary_generation(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    _insert_sample_place(db, "9001", "US", "CA", "Hospital", "healthcare")
    exp_res = mgr.export_region("US", "CA", "California", run_id="test_run_123")

    export_folder = Path(exp_res["export_folder"])
    summary_file = export_folder / "run_summary.json"
    assert summary_file.exists()

    with open(summary_file, "r", encoding="utf-8") as f:
        summary_data = json.load(f)

    assert summary_data["run_id"] == "test_run_123"
    assert summary_data["country_code"] == "US"
    assert summary_data["region_code"] == "CA"
    assert summary_data.get("total_businesses_in_region") == 1
    assert "category_counts" in summary_data


# 9. Empty-result exports (when zero records match)
def test_empty_result_exports_valid(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    # Place in CA, but none in retail
    _insert_sample_place(db, "9002", "US", "CA", "Dental Clinic", "healthcare")

    exp_res = mgr.export_region(
        country_code="US",
        region_code="CA",
        categories=["retail"]
    )
    export_folder = Path(exp_res["export_folder"])
    retail_csv = export_folder / "retail.csv"
    assert retail_csv.exists()

    df_retail = pd.read_csv(retail_csv)
    assert len(df_retail) == 0  # Header-only CSV
    assert "name" in df_retail.columns
    assert "cid" in df_retail.columns


# 10. Interrupted sync and transaction rollback
def test_interrupted_sync_transaction_safety(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    _insert_sample_place(db, "9003", "US", "CA", "Hospital", "healthcare")
    res1 = mgr.sync_region("US", "CA")
    assert res1["places_synced"] == 1

    # Simulate error by passing invalid country code
    with pytest.raises(Exception):
        mgr.sync_region("INVALID", "XXX")

    # Central database and CA regional database remain untouched and valid
    with db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM places").fetchone()[0] == 1


# 11. Preservation of the central database
def test_central_database_preservation(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    _insert_sample_place(db, "9004", "US", "CA", "Hospital A", "healthcare")
    _insert_sample_place(db, "9005", "US", "TX", "Hospital B", "healthcare")

    with db.get_connection() as conn:
        before_count = conn.execute("SELECT count(*) FROM places").fetchone()[0]

    # Perform full sync and export
    mgr.sync_all()

    with db.get_connection() as conn:
        after_count = conn.execute("SELECT count(*) FROM places").fetchone()[0]

    assert before_count == after_count == 2


# 12. Online Backup Creation & Verification
def test_create_and_verify_backup(storage_environment):
    db: Database = storage_environment["db"]
    mgr: RegionalStorageManager = storage_environment["mgr"]

    _insert_sample_place(db, "9006", "US", "CA", "Clinic", "healthcare")
    backup_res = mgr.create_backup()

    assert backup_res["status"] == "verified"
    assert backup_res["integrity"] == "ok"
    assert backup_res["places_count"] == 1
    assert Path(backup_res["backup_path"]).exists()


# 13. Pipeline completion hooks and failure handling
def test_orchestrator_sync_and_export_hook(storage_environment):
    from gmaps_scraper.orchestrator import PipelineOrchestrator
    db: Database = storage_environment["db"]
    cfg: ScraperConfig = storage_environment["cfg"]

    _insert_sample_place(db, "9007", "US", "CA", "Medical Center", "healthcare")
    orchestrator = PipelineOrchestrator.get_instance(db, cfg)

    reg_ca = get_region("US", "CA")
    scope_pairs = [("US", reg_ca)]

    # Test completed run hook
    res = orchestrator._sync_and_export_affected_regions(scope_pairs, run_id="hook_test_run", is_failed=False)
    assert len(res["synced"]) == 1
    assert len(res["failed"]) == 0
    assert Path(res["synced"][0]["regional_db_path"]).exists()

    # Test failed run hook (committed data is still safely synced without raising)
    res_fail = orchestrator._sync_and_export_affected_regions(scope_pairs, run_id="hook_fail_run", is_failed=True)
    assert len(res_fail["synced"]) == 1
    assert len(res_fail["failed"]) == 0


# 14. Storage CLI commands
def test_storage_cli_commands(storage_environment):
    from typer.testing import CliRunner
    from gmaps_scraper.cli import app
    import yaml

    root = storage_environment["root"]
    db: Database = storage_environment["db"]
    cfg = storage_environment["cfg"]

    _insert_sample_place(db, "9008", "US", "CA", "Dental Clinic", "healthcare")

    # Write temp config file for CLI
    cfg_file = root / "config.yaml"
    with open(cfg_file, "w", encoding="utf-8") as f:
        yaml.safe_dump({
            "db_path": str(cfg.db_path),
            "photos_dir": str(cfg.photos_dir),
            "exports_dir": str(cfg.exports_dir),
            "logs_dir": str(cfg.logs_dir),
            "databases_dir": str(cfg.databases_dir),
        }, f)

    runner = CliRunner()

    # 1. status
    res_status = runner.invoke(app, ["storage", "status", "--config", str(cfg_file)])
    assert res_status.exit_code == 0
    assert "Authoritative Central Database" in res_status.output

    # 2. sync
    res_sync = runner.invoke(app, ["storage", "sync", "--country", "US", "--regions", "CA", "--config", str(cfg_file)])
    assert res_sync.exit_code == 0
    assert "SUCCESS" in res_sync.output

    # 3. backup
    res_backup = runner.invoke(app, ["storage", "backup", "--config", str(cfg_file)])
    assert res_backup.exit_code == 0
    assert "VERIFIED" in res_backup.output

