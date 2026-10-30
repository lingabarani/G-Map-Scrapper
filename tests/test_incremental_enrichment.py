import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from typer.testing import CliRunner

from gmaps_scraper.db import Database
from gmaps_scraper.cli import app
from gmaps_scraper.incremental.models import CandidateFinding, MatchStatus, SourceType
from gmaps_scraper.incremental.normalizer import (
    canonical_title_key,
    normalize_email,
    normalize_name,
    normalize_name_key,
    normalize_phone,
    normalize_phone_digits,
    normalize_title,
    normalize_url,
)
from gmaps_scraper.incremental.comparison import ComparisonEngine
from gmaps_scraper.incremental.service import IncrementalEnrichmentService
from gmaps_scraper.incremental.adapters import (
    CompanyWebsiteExtendedAdapter,
    StateRegistryAdapter,
    ProfessionalProfileAdapter,
    OpenBusinessSourcesAdapter,
)
from gmaps_scraper.incremental.export import export_additional_intelligence

runner = CliRunner()


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_incremental.sqlite"
    db = Database(db_path)
    db.init_schema()
    return db


# -----------------------------------------------------------------------------
# 1. Normalization Tests
# -----------------------------------------------------------------------------

def test_normalization_email():
    assert normalize_email("  John.Doe@Example.com ") == "john.doe@example.com"
    assert normalize_email("invalid-email") is None
    assert normalize_email("logo@2x.png") is None


def test_normalization_phone():
    assert normalize_phone("+1 (907) 562-4155") == "(907) 562-4155"
    assert normalize_phone("1-907-562-4155") == "(907) 562-4155"
    assert normalize_phone("9075624155") == "(907) 562-4155"
    assert normalize_phone("1111111111") is None  # Filter dummy repeated digits


def test_normalization_name_and_title():
    assert normalize_name("Dr. Alice M. Smith, PhD") == "Alice M. Smith"
    assert normalize_name_key("Dr. Alice M. Smith, PhD") == "alicemsmith"
    assert normalize_title("  - chief executive officer  ") == "Chief Executive Officer"
    assert canonical_title_key("Chief Executive Officer") == "ceo"
    assert canonical_title_key("Vice President") == "vp"


def test_normalization_url():
    url = "https://WWW.Example.com:443/about/?utm_source=twitter&ref=123"
    assert normalize_url(url) == "https://www.example.com/about"


# -----------------------------------------------------------------------------
# 2. Incremental Comparison Engine Tests
# -----------------------------------------------------------------------------

def test_comparison_exact_match_for_existing_contact():
    existing_data = {
        "place": {"cid": "cid_001", "name": "Acme Corp"},
        "profile": {},
        "leaders": [],
        "contacts": [
            {"contact_type": "email", "contact_value": "info@acme.com", "label": "general"},
            {"contact_type": "phone", "contact_value": "(907) 555-1234", "label": "office"}
        ],
        "existing_intelligence": []
    }
    engine = ComparisonEngine(existing_data)

    cand_email = CandidateFinding(
        cid="cid_001",
        entity_type="contact",
        field_name="email",
        field_value="INFO@ACME.COM",
        source_url="https://acme.com/contact",
        source_type=SourceType.COMPANY_WEBSITE.value
    )
    res_email = engine.evaluate(cand_email)
    assert res_email.match_status == MatchStatus.EXACT_MATCH
    assert "already exists" in res_email.match_reason

    cand_phone = CandidateFinding(
        cid="cid_001",
        entity_type="contact",
        field_name="phone",
        field_value="1-907-555-1234",
        source_url="https://acme.com/contact",
        source_type=SourceType.COMPANY_WEBSITE.value
    )
    res_phone = engine.evaluate(cand_phone)
    assert res_phone.match_status == MatchStatus.EXACT_MATCH


def test_comparison_new_information_for_new_contact():
    existing_data = {
        "place": {"cid": "cid_001", "name": "Acme Corp"},
        "profile": {},
        "leaders": [],
        "contacts": [{"contact_type": "email", "contact_value": "info@acme.com"}],
        "existing_intelligence": []
    }
    engine = ComparisonEngine(existing_data)

    cand_new = CandidateFinding(
        cid="cid_001",
        entity_type="contact",
        field_name="email",
        field_value="executives@acme.com",
        source_url="https://acme.com/team",
        source_type=SourceType.COMPANY_WEBSITE.value
    )
    res = engine.evaluate(cand_new)
    assert res.match_status == MatchStatus.NEW_INFORMATION
    assert res.requires_review is False


def test_comparison_new_field_for_existing_leader():
    existing_data = {
        "place": {"cid": "cid_001", "name": "Acme Corp"},
        "profile": {},
        "leaders": [
            {"full_name": "Jane Doe", "title": "Chief Executive Officer", "role_type": "CEO"}
        ],
        "contacts": [],
        "existing_intelligence": []
    }
    engine = ComparisonEngine(existing_data)

    # 1. Same leader name and title -> EXACT_MATCH
    cand_lead = CandidateFinding(
        cid="cid_001",
        entity_type="leader",
        person_name="Jane Doe",
        job_title="CEO",
        field_name="leader",
        field_value="Jane Doe - CEO",
        source_url="https://acme.com/leadership",
        source_type=SourceType.COMPANY_WEBSITE.value
    )
    res_lead = engine.evaluate(cand_lead)
    assert res_lead.match_status == MatchStatus.EXACT_MATCH

    # 2. LinkedIn URL discovered for Jane Doe -> NEW_INFORMATION
    cand_profile = CandidateFinding(
        cid="cid_001",
        entity_type="leader",
        person_name="Jane Doe",
        job_title="CEO",
        field_name="profile_url",
        field_value="https://www.linkedin.com/in/janedoe-ceo",
        source_url="https://acme.com/leadership",
        source_type=SourceType.PROFESSIONAL_PROFILE.value
    )
    res_profile = engine.evaluate(cand_profile)
    assert res_profile.match_status == MatchStatus.NEW_INFORMATION
    assert "New profile_url discovered for existing leader 'Jane Doe'" in res_profile.match_reason


def test_comparison_role_conflict_preserves_old_value():
    existing_data = {
        "place": {"cid": "cid_001", "name": "Acme Corp"},
        "profile": {},
        "leaders": [
            {"full_name": "Alice Smith", "title": "Chief Executive Officer", "role_type": "CEO"}
        ],
        "contacts": [],
        "existing_intelligence": []
    }
    engine = ComparisonEngine(existing_data)

    # Candidate source claims Bob Jones is CEO
    cand_conflict = CandidateFinding(
        cid="cid_001",
        entity_type="leader",
        person_name="Bob Jones",
        job_title="CEO",
        field_name="leader",
        field_value="Bob Jones - CEO",
        source_url="https://external-news.com/article",
        source_type=SourceType.ANNOUNCEMENT_PAGE.value
    )
    res = engine.evaluate(cand_conflict)
    assert res.match_status == MatchStatus.CONFLICT
    assert res.requires_review is True
    assert "Alice Smith" in res.existing_value
    assert "Role conflict" in res.match_reason


def test_comparison_distinct_people_with_similar_names_not_merged():
    existing_data = {
        "place": {"cid": "cid_001", "name": "Acme Corp"},
        "profile": {},
        "leaders": [
            {"full_name": "Robert Miller", "title": "Chief Financial Officer", "role_type": "CFO"}
        ],
        "contacts": [],
        "existing_intelligence": []
    }
    engine = ComparisonEngine(existing_data)

    # Candidate is Roberta Miller (VP Marketing)
    cand_similar = CandidateFinding(
        cid="cid_001",
        entity_type="leader",
        person_name="Roberta Miller",
        job_title="VP Marketing",
        field_name="leader",
        field_value="Roberta Miller - VP Marketing",
        source_url="https://acme.com/team",
        source_type=SourceType.COMPANY_WEBSITE.value
    )
    res = engine.evaluate(cand_similar)
    assert res.match_status == MatchStatus.NEW_INFORMATION
    assert "distinct from" in res.match_reason.lower()


# -----------------------------------------------------------------------------
# 3. Source Adapters SSRF and Access Tests
# -----------------------------------------------------------------------------

def test_source_adapter_ssrf_blocking():
    adapter = CompanyWebsiteExtendedAdapter(check_dns=False)
    # Loopback IP
    safe, err = adapter.validate_url_safe("http://127.0.0.1/internal")
    assert safe is False
    assert err is not None

    # Blocked port
    safe_port, err_port = adapter.validate_url_safe("http://127.0.0.1:8000/internal")
    assert safe_port is False

    # AWS metadata
    safe, err = adapter.validate_url_safe("http://169.254.169.254/latest/meta-data")
    assert safe is False


def test_source_adapter_failure_reporting():
    adapter = StateRegistryAdapter(check_dns=False)
    business = {"cid": "cid_fail", "name": "Sample Corp", "country_code": "US", "region": "AK"}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectTimeout("Connection timed out"))

    findings, src_res = asyncio.run(adapter.fetch_and_extract(business, mock_client))
    assert len(findings) == 0
    assert src_res.status == "failed"
    assert "timed out" in src_res.error_message


# -----------------------------------------------------------------------------
# 4. End-to-End Incremental Service & Idempotency Tests
# -----------------------------------------------------------------------------

def test_service_dry_run_does_not_modify_database(temp_db):
    temp_db.upsert_place_stub({
        "cid": "cid_dry_01",
        "name": "Alaska Test Clinic",
        "country_code": "US",
        "region": "AK",
        "website": "https://www.alaskatestclinic.org",
        "website_status": "verified",
        "official_website_url": "https://www.alaskatestclinic.org"
    })

    service = IncrementalEnrichmentService(temp_db, check_dns=False)

    html_content = """
    <html>
      <head><title>Alaska Test Clinic</title></head>
      <body>
        <h3>Dr. Sarah Connor</h3>
        <p class="title">Chief Medical Officer</p>
        <a href="mailto:sconnor@alaskatestclinic.org">Email Dr. Connor</a>
        <p>Call our office: (907) 555-8899</p>
      </body>
    </html>
    """

    mock_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://www.alaskatestclinic.org"),
        text=html_content
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)

    res = asyncio.run(service.run_incremental_enrichment(
        country_code="US",
        region_code="AK",
        dry_run=True,
        http_client=mock_client
    ))

    assert res["dry_run"] is True
    assert res["counts"]["new_findings"] > 0

    # Verify zero database modifications in dry-run
    with temp_db.get_connection() as conn:
        count_intel = conn.execute("SELECT count(*) as cnt FROM additional_intelligence").fetchone()["cnt"]
        count_sources = conn.execute("SELECT count(*) as cnt FROM intelligence_sources").fetchone()["cnt"]
        count_leaders = conn.execute("SELECT count(*) as cnt FROM company_leaders").fetchone()["cnt"]

    assert count_intel == 0
    assert count_sources == 0
    assert count_leaders == 0


def test_service_live_run_persists_and_subsequent_run_is_idempotent(temp_db):
    temp_db.upsert_place_stub({
        "cid": "cid_live_01",
        "name": "Alaska Care Center",
        "country_code": "US",
        "region": "AK",
        "website": "https://www.alaskacarecenter.org",
        "website_status": "verified",
        "official_website_url": "https://www.alaskacarecenter.org"
    })

    service = IncrementalEnrichmentService(temp_db, check_dns=False)

    html_content = """
    <html>
      <head><title>Alaska Care Center</title></head>
      <body>
        <h3>David Miller</h3>
        <p class="title">Chief Executive Officer</p>
        <a href="mailto:david.miller@alaskacarecenter.org">Email David</a>
        <p>Direct Phone: (907) 555-2233</p>
      </body>
    </html>
    """

    mock_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://www.alaskacarecenter.org"),
        text=html_content
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_resp)

    # First run (live write)
    res1 = asyncio.run(service.run_incremental_enrichment(
        country_code="US",
        region_code="AK",
        dry_run=False,
        http_client=mock_client
    ))

    assert res1["counts"]["new_findings"] > 0
    assert res1["counts"]["existing_matches"] == 0

    with temp_db.get_connection() as conn:
        intel_rows1 = conn.execute("SELECT * FROM additional_intelligence").fetchall()
        assert len(intel_rows1) > 0
        src_rows1 = conn.execute("SELECT * FROM intelligence_sources").fetchall()
        assert len(src_rows1) > 0

    # Second run with exact same source data: must detect EXACT_MATCH and avoid duplicate rows
    res2 = asyncio.run(service.run_incremental_enrichment(
        country_code="US",
        region_code="AK",
        force=True,
        dry_run=False,
        http_client=mock_client
    ))

    assert res2["counts"]["new_findings"] == 0
    assert res2["counts"]["existing_matches"] > 0
    assert res2["counts"]["duplicates_prevented"] > 0

    with temp_db.get_connection() as conn:
        intel_rows2 = conn.execute("SELECT * FROM additional_intelligence").fetchall()
        # Row count should be identical (no duplicates added)
        assert len(intel_rows2) == len(intel_rows1)


# -----------------------------------------------------------------------------
# 5. Export Tests
# -----------------------------------------------------------------------------

def test_export_additional_intelligence(temp_db, tmp_path):
    temp_db.upsert_place_stub({
        "cid": "cid_exp_01",
        "name": "Export Test Business",
        "country_code": "US",
        "region": "AK"
    })

    temp_db.save_incremental_finding("cid_exp_01", {
        "company_name": "Export Test Business",
        "entity_type": "leader",
        "person_name": "Elena Rostova",
        "job_title": "Founder & CEO",
        "field_name": "leader",
        "field_value": "Elena Rostova - Founder & CEO",
        "normalized_value": "Elena Rostova - Founder & CEO",
        "match_status": "NEW_INFORMATION",
        "confidence": 0.95,
        "match_reason": "New executive discovered"
    })

    export_res = export_additional_intelligence(
        db=temp_db,
        base_exports_dir=tmp_path / "exports",
        country_code="US",
        region_code="AK",
        region_name="Alaska",
        run_date="2026-10-23"
    )

    csv_file = Path(export_res["csv_path"])
    json_file = Path(export_res["summary_path"])

    assert csv_file.exists()
    assert json_file.exists()
    assert export_res["records_count"] == 1

    content = csv_file.read_text(encoding="utf-8")
    assert "Elena Rostova" in content
    assert "NEW_INFORMATION" in content

    summary = json.loads(json_file.read_text(encoding="utf-8"))
    assert summary["country_code"] == "US"
    assert summary["region_code"] == "AK"
    assert summary["total_records_exported"] == 1
