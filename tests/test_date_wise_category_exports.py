import csv
import json
import pytest
from datetime import datetime
from pathlib import Path

from gmaps_scraper.db import Database
from gmaps_scraper.export import (
    RAW_EXPORT_COLUMNS,
    DEFAULT_CONFIGURED_CATEGORIES,
    export_live_raw_places,
    resolve_record_configured_category,
    format_region_folder_name,
)
from gmaps_scraper.orchestrator import PipelineOrchestrator, PipelineConfig


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_gmaps.sqlite"
    db = Database(db_file)
    db.init_schema()
    return db


def test_multiple_business_types_group_into_one_category_file(temp_db: Database, tmp_path: Path):
    """
    Verifies Requirement 1, 3 & 10:
    - Multiple specific Google Maps business types (e.g. Accountant, Accounting firm, Bank)
      all group into the single configured collection category file ('finance.csv' and 'finance.jsonl').
    - Multiple medical types (e.g. Doctor, Acupuncture clinic) group into 'healthcare.csv'.
    - Multiple gym types (e.g. Gym, Fitness center) group into 'fitness.csv'.
    - NO separate files for individual business types (e.g. accountant.csv, acupuncture_clinic.csv) are created.
    """
    base_dir = tmp_path / "exports"
    records = [
        # Finance subcategories
        {
            "raw_id": "r_fin_1", "cid": "c_fin_1", "name": "Vegas CPA",
            "country_code": "US", "region": "NV", "category": "Accountant",
            "vertical": "finance", "search_task_id": "col_us_nv_finance",
            "collected_at": "2026-10-29 10:00:00"
        },
        {
            "raw_id": "r_fin_2", "cid": "c_fin_2", "name": "Nevada Accounting LLC",
            "country_code": "US", "region": "NV", "category": "Accounting firm",
            "vertical": "finance", "search_task_id": "col_us_nv_finance",
            "collected_at": "2026-10-29 10:01:00"
        },
        {
            "raw_id": "r_fin_3", "cid": "c_fin_3", "name": "Wells Fargo Bank",
            "country_code": "US", "region": "NV", "category": "Bank",
            "vertical": "finance", "search_task_id": "col_us_nv_finance",
            "collected_at": "2026-10-29 10:02:00"
        },
        # Healthcare subcategories
        {
            "raw_id": "r_hlth_1", "cid": "c_hlth_1", "name": "Dr. Smith Medical",
            "country_code": "US", "region": "NV", "category": "Doctor",
            "vertical": "healthcare", "search_task_id": "col_us_nv_healthcare",
            "collected_at": "2026-10-29 10:03:00"
        },
        {
            "raw_id": "r_hlth_2", "cid": "c_hlth_2", "name": "Acupuncture Healing Center",
            "country_code": "US", "region": "NV", "category": "Acupuncture clinic",
            "vertical": "healthcare", "search_task_id": "col_us_nv_healthcare",
            "collected_at": "2026-10-29 10:04:00"
        },
        # Fitness subcategories
        {
            "raw_id": "r_fit_1", "cid": "c_fit_1", "name": "Gold's Gym",
            "country_code": "US", "region": "NV", "category": "Gym",
            "vertical": "fitness", "search_task_id": "col_us_nv_fitness",
            "collected_at": "2026-10-29 10:05:00"
        },
        {
            "raw_id": "r_fit_2", "cid": "c_fit_2", "name": "Anytime Fitness Center",
            "country_code": "US", "region": "NV", "category": "Fitness center",
            "vertical": "fitness", "search_task_id": "col_us_nv_fitness",
            "collected_at": "2026-10-29 10:06:00"
        },
    ]

    for r in records:
        temp_db.insert_raw_place(r)

    res = export_live_raw_places(
        db=temp_db,
        base_exports_dir=base_dir,
        country_code="US",
        region_code="NV",
        region_name="Nevada",
        collection_date="2026-10-29",
    )

    assert res["status"] == "success"
    target_dir = Path(res["export_dir"])
    assert target_dir.exists()

    # 1. Combined category files exist
    assert (target_dir / "finance.csv").exists()
    assert (target_dir / "finance.jsonl").exists()
    assert (target_dir / "healthcare.csv").exists()
    assert (target_dir / "healthcare.jsonl").exists()
    assert (target_dir / "fitness.csv").exists()
    assert (target_dir / "fitness.jsonl").exists()

    # 2. Individual business-type files MUST NOT exist
    assert not (target_dir / "accountant.csv").exists()
    assert not (target_dir / "accounting_firm.csv").exists()
    assert not (target_dir / "bank.csv").exists()
    assert not (target_dir / "doctor.csv").exists()
    assert not (target_dir / "acupuncture_clinic.csv").exists()
    assert not (target_dir / "gym.csv").exists()
    assert not (target_dir / "fitness_center.csv").exists()

    # 3. Check contents of finance.csv
    with open(target_dir / "finance.csv", "r", encoding="utf-8") as f:
        fin_rows = list(csv.DictReader(f))
        assert len(fin_rows) == 3
        fin_names = {row["name"] for row in fin_rows}
        assert fin_names == {"Vegas CPA", "Nevada Accounting LLC", "Wells Fargo Bank"}
        # Check original subcategories preserved in 'category' field
        fin_cats = {row["category"] for row in fin_rows}
        assert fin_cats == {"Accountant", "Accounting firm", "Bank"}
        # Configured category preserved in 'vertical' field
        for row in fin_rows:
            assert row["vertical"] == "finance"

    # 4. Check contents of healthcare.csv
    with open(target_dir / "healthcare.csv", "r", encoding="utf-8") as f:
        hlth_rows = list(csv.DictReader(f))
        assert len(hlth_rows) == 2
        hlth_names = {row["name"] for row in hlth_rows}
        assert hlth_names == {"Dr. Smith Medical", "Acupuncture Healing Center"}

    # 5. Check contents of fitness.csv
    with open(target_dir / "fitness.csv", "r", encoding="utf-8") as f:
        fit_rows = list(csv.DictReader(f))
        assert len(fit_rows) == 2
        fit_names = {row["name"] for row in fit_rows}
        assert fit_names == {"Gold's Gym", "Anytime Fitness Center"}

    # 6. Check export_summary.json metadata
    summary_path = target_dir / "export_summary.json"
    assert summary_path.exists()
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
        assert summary["date"] == "2026-10-29"
        assert summary["country_code"] == "US"
        assert summary["region_code"] == "NV"
        assert summary["total_records"] == 7
        assert summary["by_category"]["finance"] == 3
        assert summary["by_category"]["healthcare"] == 2
        assert summary["by_category"]["fitness"] == 2


def test_all_categories_in_combined_all_businesses_files(temp_db: Database, tmp_path: Path):
    """
    Verifies Requirement 2 & 4:
    - all_businesses.csv and all_businesses.jsonl contain ALL raw discoveries for that state and date.
    - Full schema columns (RAW_EXPORT_COLUMNS) are preserved.
    """
    base_dir = tmp_path / "exports"
    records = [
        {"raw_id": "r1", "cid": "c1", "name": "Place Food", "country_code": "US", "region": "NV", "category": "food", "vertical": "food", "city": "Reno", "search_task_id": "col_us_nv_food", "collected_at": "2026-10-29 10:00:00"},
        {"raw_id": "r2", "cid": "c2", "name": "Place Retail", "country_code": "US", "region": "NV", "category": "retail", "vertical": "retail", "city": "Las Vegas", "search_task_id": "col_us_nv_retail", "collected_at": "2026-10-29 10:01:00"},
        {"raw_id": "r3", "cid": "c3", "name": "Place Beauty", "country_code": "US", "region": "NV", "category": "beauty", "vertical": "beauty", "city": "Henderson", "search_task_id": "col_us_nv_beauty", "collected_at": "2026-10-29 10:02:00"},
    ]
    for r in records:
        temp_db.insert_raw_place(r)

    res = export_live_raw_places(
        db=temp_db,
        base_exports_dir=base_dir,
        country_code="US",
        region_code="NV",
        collection_date="2026-10-29"
    )
    target_dir = Path(res["export_dir"])

    # 1. all_businesses.csv
    csv_path = target_dir / "all_businesses.csv"
    assert csv_path.exists()
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 3
        assert {row["name"] for row in reader} == {"Place Food", "Place Retail", "Place Beauty"}
        assert {row["city"] for row in reader} == {"Reno", "Las Vegas", "Henderson"}
        # Verify columns match RAW_EXPORT_COLUMNS
        assert list(reader[0].keys()) == RAW_EXPORT_COLUMNS

    # 2. all_businesses.jsonl
    jsonl_path = target_dir / "all_businesses.jsonl"
    assert jsonl_path.exists()
    with open(jsonl_path, "r", encoding="utf-8") as f:
        json_rows = [json.loads(line) for line in f if line.strip()]
        assert len(json_rows) == 3
        assert {r["cid"] for r in json_rows} == {"c1", "c2", "c3"}


def test_repeated_exports_do_not_duplicate_rows(temp_db: Database, tmp_path: Path):
    """
    Verifies Requirement 6:
    - Multiple consecutive exports do NOT append or duplicate rows.
    - Files are regenerated from authoritative SQLite records cleanly.
    """
    base_dir = tmp_path / "exports"
    records = [
        {"raw_id": "r1", "cid": "c1", "name": "Alpha Clinic", "country_code": "US", "region": "NV", "category": "healthcare", "vertical": "healthcare", "search_task_id": "col_us_nv_healthcare", "collected_at": "2026-10-29 10:00:00"},
        {"raw_id": "r2", "cid": "c2", "name": "Beta Dental", "country_code": "US", "region": "NV", "category": "healthcare", "vertical": "healthcare", "search_task_id": "col_us_nv_healthcare", "collected_at": "2026-10-29 10:01:00"},
    ]
    for r in records:
        temp_db.insert_raw_place(r)

    # First export
    res1 = export_live_raw_places(db=temp_db, base_exports_dir=base_dir, country_code="US", region_code="NV", collection_date="2026-10-29")
    target_dir = Path(res1["export_dir"])

    with open(target_dir / "all_businesses.csv", "r", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 2

    # Second export (repeated)
    res2 = export_live_raw_places(db=temp_db, base_exports_dir=base_dir, country_code="US", region_code="NV", collection_date="2026-10-29")

    with open(target_dir / "all_businesses.csv", "r", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 2  # Still exactly 2!

    with open(target_dir / "healthcare.csv", "r", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 2  # Still exactly 2!


def test_separate_dates_generate_separate_directories(temp_db: Database, tmp_path: Path):
    """
    Verifies Requirement 7 & 8:
    - Records collected on different dates (e.g. 2026-10-28 and 2026-10-29) are saved in separate date folders.
    - 2026-10-28 export contains ONLY 2026-10-28 records.
    - 2026-10-29 export contains ONLY 2026-10-29 records.
    """
    base_dir = tmp_path / "exports"

    # Day 1: 2026-10-28
    temp_db.insert_raw_place({
        "raw_id": "r_day1", "cid": "c_day1", "name": "Yesterday Cafe",
        "country_code": "US", "region": "NV", "category": "food", "vertical": "food",
        "search_task_id": "col_us_nv_food", "collected_at": "2026-10-28 15:30:00"
    })

    # Day 2: 2026-10-29
    temp_db.insert_raw_place({
        "raw_id": "r_day2", "cid": "c_day2", "name": "Today Diner",
        "country_code": "US", "region": "NV", "category": "food", "vertical": "food",
        "search_task_id": "col_us_nv_food", "collected_at": "2026-10-29 09:15:00"
    })

    # Export all active dates
    export_live_raw_places(
        db=temp_db,
        base_exports_dir=base_dir,
        country_code="US",
        region_code="NV",
        region_name="Nevada"
    )

    dir_day1 = base_dir / "live_raw" / "2026-10-28" / "US" / "Nevada_NV"
    dir_day2 = base_dir / "live_raw" / "2026-10-29" / "US" / "Nevada_NV"

    assert dir_day1.exists()
    assert dir_day2.exists()

    # Day 1 files
    with open(dir_day1 / "all_businesses.csv", "r", encoding="utf-8") as f:
        rows_day1 = list(csv.DictReader(f))
        assert len(rows_day1) == 1
        assert rows_day1[0]["name"] == "Yesterday Cafe"

    with open(dir_day1 / "export_summary.json", "r", encoding="utf-8") as f:
        sum_day1 = json.load(f)
        assert sum_day1["date"] == "2026-10-28"
        assert sum_day1["total_records"] == 1

    # Day 2 files
    with open(dir_day2 / "all_businesses.csv", "r", encoding="utf-8") as f:
        rows_day2 = list(csv.DictReader(f))
        assert len(rows_day2) == 1
        assert rows_day2[0]["name"] == "Today Diner"

    with open(dir_day2 / "export_summary.json", "r", encoding="utf-8") as f:
        sum_day2 = json.load(f)
        assert sum_day2["date"] == "2026-10-29"
        assert sum_day2["total_records"] == 1


def test_resume_does_not_overwrite_previous_date_export(temp_db: Database, tmp_path: Path):
    """
    Verifies Requirement 7 & 8:
    - Resuming on a new date does NOT overwrite, delete or alter a previous date's export directory.
    - Midnight collection resumption associates new records with the new date only.
    """
    base_dir = tmp_path / "exports"

    # Initial collection on 2026-10-28
    temp_db.insert_raw_place({
        "raw_id": "r_old", "cid": "c_old", "name": "Old Pizza",
        "country_code": "US", "region": "NV", "category": "food", "vertical": "food",
        "search_task_id": "col_us_nv_food", "collected_at": "2026-10-28 23:45:00"
    })
    export_live_raw_places(db=temp_db, base_exports_dir=base_dir, country_code="US", region_code="NV", collection_date="2026-10-28")

    path_old_csv = base_dir / "live_raw" / "2026-10-28" / "US" / "Nevada_NV" / "all_businesses.csv"
    assert path_old_csv.exists()
    old_content = path_old_csv.read_text(encoding="utf-8")

    # Resume after midnight on 2026-10-29
    temp_db.insert_raw_place({
        "raw_id": "r_new", "cid": "c_new", "name": "Midnight Snack",
        "country_code": "US", "region": "NV", "category": "food", "vertical": "food",
        "search_task_id": "col_us_nv_food", "collected_at": "2026-10-29 00:15:00"
    })
    export_live_raw_places(db=temp_db, base_exports_dir=base_dir, country_code="US", region_code="NV", collection_date="2026-10-29")

    path_new_csv = base_dir / "live_raw" / "2026-10-29" / "US" / "Nevada_NV" / "all_businesses.csv"
    assert path_new_csv.exists()

    # Previous date's export is completely untouched!
    assert path_old_csv.read_text(encoding="utf-8") == old_content


def test_new_committed_records_appear_in_current_date_export(temp_db: Database, tmp_path: Path):
    """
    Verifies Requirement 5, 6 & 9:
    - New committed records during collection dynamically appear in the current date's export.
    - SQLite database remains authoritative and uncorrupted.
    """
    base_dir = tmp_path / "exports"
    date_today = datetime.now().strftime("%Y-%m-%d")

    # Step 1: Initial record
    temp_db.insert_raw_place({
        "raw_id": "r_live_1", "cid": "c_live_1", "name": "Hospital One",
        "country_code": "US", "region": "NV", "category": "healthcare", "vertical": "healthcare",
        "search_task_id": "col_us_nv_healthcare", "collected_at": f"{date_today} 08:00:00"
    })
    res1 = export_live_raw_places(db=temp_db, base_exports_dir=base_dir, country_code="US", region_code="NV", collection_date=date_today)
    assert res1["records_exported"] == 1

    # Step 2: Live scraper commits 2 new records
    temp_db.insert_raw_place({
        "raw_id": "r_live_2", "cid": "c_live_2", "name": "Hospital Two",
        "country_code": "US", "region": "NV", "category": "healthcare", "vertical": "healthcare",
        "search_task_id": "col_us_nv_healthcare", "collected_at": f"{date_today} 08:30:00"
    })
    temp_db.insert_raw_place({
        "raw_id": "r_live_3", "cid": "c_live_3", "name": "Dental Three",
        "country_code": "US", "region": "NV", "category": "healthcare", "vertical": "healthcare",
        "search_task_id": "col_us_nv_healthcare", "collected_at": f"{date_today} 09:00:00"
    })

    # Step 3: Trigger live raw export refresh
    res2 = export_live_raw_places(db=temp_db, base_exports_dir=base_dir, country_code="US", region_code="NV", collection_date=date_today)
    assert res2["records_exported"] == 3

    target_dir = Path(res2["export_dir"])
    with open(target_dir / "all_businesses.csv", "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        assert len(rows) == 3
        assert {r["name"] for r in rows} == {"Hospital One", "Hospital Two", "Dental Three"}

    # SQLite is completely intact
    with temp_db.get_connection() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM raw_places WHERE UPPER(region) = 'NV'").fetchone()[0]
        assert cnt == 3

