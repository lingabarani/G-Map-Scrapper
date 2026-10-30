import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from ..db import Database
from ..export import export_state_complete_package, format_region_folder_name, list_all_state_exports
from ..scope import get_region, get_regions_for_country, validate_target_country, ALLOWED_COUNTRIES

logger = logging.getLogger(__name__)


def _chunk_list(lst: List[Any], size: int = 500) -> List[List[Any]]:
    """Splits a list into chunks to respect SQLite query parameter limits."""
    return [lst[i:i + size] for i in range(0, len(lst), size)]


class RegionalStorageManager:
    """
    Manages state-wise SQLite databases and automatic exports while strictly
    preserving the central database (data/gmaps.sqlite) as the single source of truth.

    Directory Layout:
    data/
    ├── gmaps.sqlite
    ├── databases/
    │   ├── US/
    │   │   ├── California_CA.sqlite
    │   │   └── Texas_TX.sqlite
    │   └── MX/
    │       ├── Jalisco_JAL.sqlite
    │       └── Ciudad_de_Mexico_CMX.sqlite
    └── exports/
        └── YYYY-MM-DD/
            ├── US/
            │   └── California_CA/
            └── MX/
                └── Jalisco_JAL/
    """

    def __init__(
        self,
        central_db: Database,
        config: Optional[Any] = None,
        base_databases_dir: Optional[Union[str, Path]] = None,
        base_exports_dir: Optional[Union[str, Path]] = None
    ):
        self.central_db = central_db

        # Handle config passed as 2nd argument or keyword argument
        if config is not None:
            if hasattr(config, "absolute_databases_dir"):
                base_databases_dir = config.absolute_databases_dir
            elif isinstance(config, (str, Path)):
                base_databases_dir = config

            if hasattr(config, "absolute_exports_dir") and not base_exports_dir:
                base_exports_dir = config.absolute_exports_dir

        if base_databases_dir:
            self.base_databases_dir = Path(base_databases_dir)
        else:
            self.base_databases_dir = self.central_db.db_path.parent / "databases"

        if base_exports_dir:
            self.base_exports_dir = Path(base_exports_dir)
        else:
            self.base_exports_dir = self.central_db.db_path.parent / "exports"

        self.base_databases_dir.mkdir(parents=True, exist_ok=True)
        self.base_exports_dir.mkdir(parents=True, exist_ok=True)

    def get_regional_db_path(
        self,
        country_code: str,
        region_code: str,
        region_name: Optional[str] = None
    ) -> Path:
        """
        Computes the deterministic path to a state or federal entity SQLite database:
        data/databases/{COUNTRY}/{Region_Name}_{CODE}.sqlite
        """
        cc = validate_target_country(country_code)
        rc = region_code.strip().upper()

        if not region_name:
            try:
                region_name = get_region(cc, rc).name
            except Exception:
                region_name = rc

        folder_name = format_region_folder_name(cc, rc, region_name)
        country_dir = self.base_databases_dir / cc
        country_dir.mkdir(parents=True, exist_ok=True)
        return country_dir / f"{folder_name}.sqlite"

    def init_regional_database(
        self,
        country_code: str,
        region_code: str,
        region_name: Optional[str] = None
    ) -> Database:
        """
        Initializes a state/entity database with the project's standard schema,
        indexes, FTS triggers, and synchronization metadata table.
        """
        db_path = self.get_regional_db_path(country_code, region_code, region_name)
        reg_db = Database(db_path)
        reg_db.init_schema()

        # Create synchronization metadata table in regional database
        with reg_db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _sync_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

        return reg_db

    def sync_region(
        self,
        country_code: str,
        region_code: str,
        region_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Performs atomic, idempotent synchronization of all businesses and related
        child data belonging to (country_code, region_code) from the authoritative
        central database into its regional SQLite database.
        """
        cc = validate_target_country(country_code)
        rc = region_code.strip().upper()

        if not region_name:
            try:
                region_name = get_region(cc, rc).name
            except Exception:
                region_name = rc

        # 1. Initialize regional database
        reg_db = self.init_regional_database(cc, rc, region_name)
        reg_db_path = reg_db.db_path

        # 2. Query central database for matching businesses
        with self.central_db.get_connection() as conn:
            # Match country and region
            place_rows = conn.execute("""
                SELECT * FROM places
                WHERE country_code = ? AND region = ?
                ORDER BY cid ASC
            """, (cc, rc)).fetchall()
            places_data = [dict(r) for r in place_rows]
            current_cids = [p["cid"] for p in places_data if p.get("cid")]

            # Related child tables by CID
            child_tables = [
                "hours",
                "attributes",
                "reviews",
                "photos",
                "popular_times",
                "company_profiles",
                "company_leaders",
                "company_contacts",
                "additional_intelligence",
                "intelligence_sources"
            ]
            related_data: Dict[str, List[Dict[str, Any]]] = {t: [] for t in child_tables}

            if current_cids:
                for chunk in _chunk_list(current_cids, 500):
                    ph = ",".join(["?"] * len(chunk))
                    for t in child_tables:
                        t_rows = conn.execute(f"SELECT * FROM {t} WHERE cid IN ({ph})", chunk).fetchall()
                        related_data[t].extend([dict(r) for r in t_rows])

            # Tasks & geographic tiles by region
            task_rows = conn.execute("""
                SELECT * FROM collection_tasks
                WHERE country_code = ? AND region_code = ?
            """, (cc, rc)).fetchall()
            tasks_data = [dict(r) for r in task_rows]

            tile_rows = conn.execute("""
                SELECT * FROM geographic_tiles
                WHERE country_code = ? AND region_code = ?
            """, (cc, rc)).fetchall()
            tiles_data = [dict(r) for r in tile_rows]

        # 3. Synchronize regional database inside an atomic transaction
        with reg_db.get_connection() as conn:
            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys = ON;")

            # 3a. Cleanup stale records (places that changed region or were deleted in central DB)
            existing_cids_rows = conn.execute("SELECT cid FROM places").fetchall()
            existing_cids = {r["cid"] for r in existing_cids_rows}
            stale_cids = list(existing_cids - set(current_cids))

            if stale_cids:
                for chunk in _chunk_list(stale_cids, 500):
                    ph = ",".join(["?"] * len(chunk))
                    conn.execute(f"DELETE FROM places WHERE cid IN ({ph})", chunk)
                logger.info(f"Removed {len(stale_cids)} stale place(s) from {reg_db_path.name}")

            # 3b. Upsert places
            if places_data:
                cols = list(places_data[0].keys())
                col_names = ", ".join(cols)
                ph_str = ", ".join(["?"] * len(cols))
                update_assignments = ", ".join(f"{col} = excluded.{col}" for col in cols if col != "cid")
                upsert_sql = f"""
                    INSERT INTO places ({col_names})
                    VALUES ({ph_str})
                    ON CONFLICT(cid) DO UPDATE SET {update_assignments};
                """
                place_tuples = [[p.get(c) for c in cols] for p in places_data]
                conn.executemany(upsert_sql, place_tuples)

            # 3c. Sync child tables (delete existing for current_cids and insert fresh from central)
            if current_cids:
                for chunk in _chunk_list(current_cids, 500):
                    ph = ",".join(["?"] * len(chunk))
                    # Child tables in reverse dependency order for deletion
                    for t in reversed(child_tables):
                        conn.execute(f"DELETE FROM {t} WHERE cid IN ({ph})", chunk)

            # Insert child tables in forward dependency order (additional_intelligence before intelligence_sources)
            for t in child_tables:
                rows_to_insert = related_data.get(t, [])
                if rows_to_insert:
                    cols = list(rows_to_insert[0].keys())
                    col_names = ", ".join(cols)
                    ph_str = ", ".join(["?"] * len(cols))
                    insert_sql = f"INSERT INTO {t} ({col_names}) VALUES ({ph_str});"
                    tuples_to_insert = [[r.get(c) for c in cols] for r in rows_to_insert]
                    conn.executemany(insert_sql, tuples_to_insert)

            # 3d. Sync tasks and geographic tiles
            conn.execute("DELETE FROM collection_tasks WHERE country_code = ? AND region_code = ?;", (cc, rc))
            if tasks_data:
                t_cols = list(tasks_data[0].keys())
                t_col_str = ", ".join(t_cols)
                t_ph_str = ", ".join(["?"] * len(t_cols))
                conn.executemany(
                    f"INSERT INTO collection_tasks ({t_col_str}) VALUES ({t_ph_str});",
                    [[t.get(c) for c in t_cols] for t in tasks_data]
                )

            conn.execute("DELETE FROM geographic_tiles WHERE country_code = ? AND region_code = ?;", (cc, rc))
            if tiles_data:
                g_cols = list(tiles_data[0].keys())
                g_col_str = ", ".join(g_cols)
                g_ph_str = ", ".join(["?"] * len(g_cols))
                conn.executemany(
                    f"INSERT INTO geographic_tiles ({g_col_str}) VALUES ({g_ph_str});",
                    [[g.get(c) for c in g_cols] for g in tiles_data]
                )

            # 3e. Update synchronization metadata
            now_iso = datetime.now(timezone.utc).isoformat()
            meta_entries = [
                ("last_sync_time", now_iso),
                ("source_database", str(self.central_db.db_path)),
                ("country_code", cc),
                ("region_code", rc),
                ("region_name", region_name),
                ("places_count", str(len(places_data))),
                ("reviews_count", str(len(related_data.get("reviews", [])))),
                ("company_profiles_count", str(len(related_data.get("company_profiles", [])))),
                ("company_contacts_count", str(len(related_data.get("company_contacts", [])))),
                ("company_leaders_count", str(len(related_data.get("company_leaders", [])))),
                ("schema_version", "1.0"),
                ("sync_status", "success"),
            ]
            conn.executemany("""
                INSERT INTO _sync_metadata (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP;
            """, meta_entries)
            conn.commit()

        logger.info(f"Successfully synchronized {len(places_data)} place(s) to regional DB {reg_db_path.name}")
        return {
            "country_code": cc,
            "region_code": rc,
            "region_name": region_name,
            "database_path": str(reg_db_path),
            "regional_db_path": str(reg_db_path),
            "places_count": len(places_data),
            "places_synced": len(places_data),
            "reviews_count": len(related_data.get("reviews", [])),
            "photos_count": len(related_data.get("photos", [])),
            "company_profiles_count": len(related_data.get("company_profiles", [])),
            "company_contacts_count": len(related_data.get("company_contacts", [])),
            "company_leaders_count": len(related_data.get("company_leaders", [])),
            "tasks_count": len(tasks_data),
            "tiles_count": len(tiles_data),
            "stale_cleaned": len(stale_cids),
            "sync_time": now_iso,
            "status": "success",
        }

    def export_region(
        self,
        country_code: str,
        region_code: str,
        region_name: Optional[str] = None,
        run_id: str = "manual_sync",
        run_date: Optional[str] = None,
        categories: Optional[List[str]] = None,
        export_format: str = "csv"
    ) -> Dict[str, Any]:
        """
        Exports category CSV files, all_businesses.csv, and run_summary.json
        into data/exports/YYYY-MM-DD/{COUNTRY}/{REGION}/ using data from the central database.
        """
        cc = validate_target_country(country_code)
        rc = region_code.strip().upper()

        if not region_name:
            try:
                region_name = get_region(cc, rc).name
            except Exception:
                region_name = rc

        return export_state_complete_package(
            db=self.central_db,
            base_exports_dir=self.base_exports_dir,
            run_id=run_id,
            run_date=run_date,
            country_code=cc,
            region_code=rc,
            region_name=region_name,
            categories=categories,
            export_format=export_format
        )

    def sync_and_export_region(
        self,
        country_code: str,
        region_code: str,
        region_name: Optional[str] = None,
        run_id: str = "manual_sync",
        run_date: Optional[str] = None,
        categories: Optional[List[str]] = None,
        export_format: str = "csv"
    ) -> Dict[str, Any]:
        """
        Convenience workflow that synchronizes the state SQLite database and
        generates the matching state-wise CSV and JSON exports in one atomic call.
        """
        sync_result = self.sync_region(country_code, region_code, region_name)
        export_result = self.export_region(
            country_code=country_code,
            region_code=region_code,
            region_name=region_name,
            run_id=run_id,
            run_date=run_date,
            categories=categories,
            export_format=export_format
        )
        return {
            "country_code": sync_result["country_code"],
            "region_code": sync_result["region_code"],
            "region_name": sync_result["region_name"],
            "database_path": sync_result["database_path"],
            "regional_db_path": sync_result["database_path"],
            "places_count": sync_result["places_count"],
            "places_synced": sync_result["places_count"],
            "export_folder": export_result["export_folder"],
            "files_exported": export_result["files"],
            "category_counts": export_result["summary"].get("category_counts", {}),
            "status": "success"
        }

    def sync_all(
        self,
        country_code: Optional[str] = None,
        export_also: bool = True
    ) -> Dict[str, Any]:
        """
        Discovers all regions populated in the central database (or configured for the country)
        and synchronizes their state-wise SQLite databases and exports.
        """
        countries_to_check = [validate_target_country(country_code)] if country_code else list(ALLOWED_COUNTRIES)

        results = []
        with self.central_db.get_connection() as conn:
            # Query all (country_code, region) pairs present in places or collection_tasks
            rows = conn.execute("""
                SELECT DISTINCT country_code, region FROM places
                WHERE country_code IS NOT NULL AND region IS NOT NULL
                UNION
                SELECT DISTINCT country_code, region_code as region FROM collection_tasks
                WHERE country_code IS NOT NULL AND region_code IS NOT NULL
            """).fetchall()

        populated_pairs: Set[Tuple[str, str]] = set()
        for r in rows:
            c = str(r["country_code"]).strip().upper()
            rg = str(r["region"]).strip().upper()
            if c in countries_to_check:
                populated_pairs.add((c, rg))

        for c, rg in sorted(populated_pairs):
            try:
                if export_also:
                    res = self.sync_and_export_region(c, rg)
                else:
                    res = self.sync_region(c, rg)
                results.append(res)
            except Exception as e:
                logger.error(f"Failed to sync region {c}-{rg}: {e}", exc_info=True)
                results.append({
                    "country_code": c,
                    "region_code": rg,
                    "status": "failed",
                    "error": str(e)
                })

        return {
            "total_regions_synced": len(results),
            "success_count": sum(1 for r in results if r.get("status") == "success"),
            "failed_count": sum(1 for r in results if r.get("status") != "success"),
            "regions": results
        }

    def get_storage_status(self) -> Dict[str, Any]:
        """
        Inspects the central database, discovers all regional SQLite databases,
        and aggregates synchronization metadata and export packages.
        """
        # Central DB metrics
        central_stats = self.central_db.get_stats()

        # Regional DBs discovery
        regional_dbs: List[Dict[str, Any]] = []
        for cc in ALLOWED_COUNTRIES:
            cdir = self.base_databases_dir / cc
            if not cdir.exists():
                continue
            for sqlite_path in sorted(cdir.glob("*.sqlite")):
                try:
                    conn = sqlite3.connect(str(sqlite_path), timeout=5.0)
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    places_count = cur.execute("SELECT count(*) FROM places").fetchone()[0]

                    meta_dict = {}
                    try:
                        m_rows = cur.execute("SELECT key, value FROM _sync_metadata").fetchall()
                        meta_dict = {row["key"]: row["value"] for row in m_rows}
                    except Exception:
                        pass
                    conn.close()

                    regional_dbs.append({
                        "country_code": cc,
                        "filename": sqlite_path.name,
                        "path": str(sqlite_path),
                        "file_size_bytes": sqlite_path.stat().st_size,
                        "places_count": places_count,
                        "last_sync_time": meta_dict.get("last_sync_time"),
                        "region_name": meta_dict.get("region_name"),
                        "region_code": meta_dict.get("region_code"),
                        "sync_status": meta_dict.get("sync_status", "unknown")
                    })
                except Exception as e:
                    regional_dbs.append({
                        "country_code": cc,
                        "filename": sqlite_path.name,
                        "path": str(sqlite_path),
                        "places_count": -1,
                        "error": str(e)
                    })

        # Discover exports
        exports = list_all_state_exports(self.base_exports_dir)

        return {
            "central_database": {
                "path": str(self.central_db.db_path),
                "total_places": central_stats.get("total_places", 0),
                "total_reviews": central_stats.get("total_reviews", 0),
                "total_photos": central_stats.get("total_photos", 0),
                "details_done": central_stats.get("details_done", 0),
            },
            "regional_databases_count": len(regional_dbs),
            "regional_databases": regional_dbs,
            "total_exports_packages": len(exports),
            "exports": exports[:30]
        }

    def create_backup(self, backup_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Creates a timestamped backup of the central SQLite database using SQLite's
        online backup API and verifies database integrity.
        """
        source_path = Path(self.central_db.db_path)
        if not source_path.exists():
            raise FileNotFoundError(f"Central database does not exist: {source_path}")

        if backup_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = source_path.parent / f"{source_path.stem}_backup_{ts}{source_path.suffix}"
        else:
            backup_path = Path(backup_path)

        backup_path.parent.mkdir(parents=True, exist_ok=True)

        # Safe online backup
        src_conn = sqlite3.connect(str(source_path))
        dst_conn = sqlite3.connect(str(backup_path))
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()

        # Verify integrity and count places
        v_conn = sqlite3.connect(str(backup_path))
        integrity = v_conn.execute("PRAGMA integrity_check").fetchone()[0]
        places_count = v_conn.execute("SELECT count(*) FROM places").fetchone()[0]
        v_conn.close()

        if integrity != "ok":
            raise RuntimeError(f"Backup integrity check failed: {integrity}")

        return {
            "source_path": str(source_path),
            "backup_path": str(backup_path),
            "file_size_bytes": backup_path.stat().st_size,
            "places_count": places_count,
            "integrity": integrity,
            "timestamp": datetime.now().isoformat(),
            "status": "verified"
        }

