import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from typer.testing import CliRunner

from gmaps_scraper.config import ScraperConfig
from gmaps_scraper.db import Database
from gmaps_scraper.cli import app
from gmaps_scraper.website.crawler import SafeWebsiteCrawler, PageResponse
from gmaps_scraper.website.extractor import WebsiteExtractor
from gmaps_scraper.website.enrichment_service import WebsiteEnrichmentService

runner = CliRunner()

@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_enrichment.sqlite"
    db = Database(db_path)
    db.init_schema()
    return db

def test_1_verified_websites_are_selected_correctly(temp_db):
    """Test 1: Only businesses with website_status='verified' are selected for enrichment."""
    temp_db.upsert_place_stub({
        "cid": "cid_ver_01",
        "name": "Verified Business",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.verifiedbiz.com",
        "website_status": "verified",
        "official_website_url": "https://www.verifiedbiz.com"
    })
    temp_db.upsert_place_stub({
        "cid": "cid_unver_02",
        "name": "Pending Business",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.pendingbiz.com",
        "website_status": "needs_review"
    })
    temp_db.upsert_place_stub({
        "cid": "cid_unver_03",
        "name": "Missing Biz",
        "country_code": "US",
        "region": "WY",
        "website": None,
        "website_status": "website_missing"
    })

    targets = temp_db.get_verified_businesses_for_enrichment("US", "WY")
    assert len(targets) == 1
    assert targets[0]["cid"] == "cid_ver_01"

def test_2_unverified_websites_are_excluded_by_default(temp_db):
    """Test 2: Unverified websites are excluded by default from enrichment runs."""
    temp_db.upsert_place_stub({
        "cid": "cid_unver_10",
        "name": "Unverified Shop",
        "country_code": "US",
        "region": "NY",
        "website": "https://www.unverifiedshop.com",
        "website_status": "failed"
    })

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    res = asyncio.run(service.run_enrichment("US", "NY"))
    assert res["counts"]["total_selected"] == 0
    assert len(res["results"]) == 0

def test_3_company_descriptions_and_services_are_extracted_from_fixtures(temp_db):
    """Test 3: Company descriptions and services are accurately parsed from HTML fixtures."""
    temp_db.upsert_place_stub({
        "cid": "cid_desc_01",
        "name": "Artisan Roasters",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.artisanroasters.com",
        "website_status": "verified",
        "official_website_url": "https://www.artisanroasters.com"
    })

    home_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Artisan Roasters</title>
        <meta name="description" content="Artisan Roasters provides specialty ethically sourced single-origin coffee beans and roasting services.">
        <meta name="keywords" content="Specialty Coffee, Coffee Beans, Roastery">
    </head>
    <body>
        <h1>Artisan Roasters</h1>
        <a href="/services">Our Offerings</a>
        <a href="/contact">Get In Touch</a>
    </body>
    </html>
    """
    services_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Services - Artisan Roasters</title></head>
    <body>
        <h2>What We Offer</h2>
        <ul>
            <li>Wholesale Coffee Distribution</li>
            <li>Custom Blend Development</li>
            <li>Barista Staff Training</li>
        </ul>
    </body>
    </html>
    """

    mock_client = MagicMock()
    async def mock_get(url, *args, **kwargs):
        u = str(url)
        if "/services" in u:
            return httpx.Response(200, request=httpx.Request("GET", u), text=services_html)
        return httpx.Response(200, request=httpx.Request("GET", u), text=home_html)
    mock_client.get = AsyncMock(side_effect=mock_get)

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    res = asyncio.run(service.run_enrichment("US", "WY", http_client=mock_client))

    assert res["counts"]["completed"] == 1
    enrichment = temp_db.get_company_enrichment("cid_desc_01")
    profile = enrichment["profile"]
    assert profile is not None
    assert "ethically sourced single-origin coffee" in profile["overview"]
    assert "Specialty Coffee" in profile["industry"]
    assert "Wholesale Coffee Distribution" in profile["services"]
    assert "Custom Blend Development" in profile["services"]

def test_4_leadership_names_and_titles_are_extracted_from_relevant_pages(temp_db):
    """Test 4: Leadership names and titles are extracted from leadership/about pages and JSON-LD."""
    temp_db.upsert_place_stub({
        "cid": "cid_lead_01",
        "name": "Apex Innovations",
        "country_code": "US",
        "region": "NY",
        "website": "https://www.apexinnovations.com",
        "website_status": "verified",
        "official_website_url": "https://www.apexinnovations.com"
    })

    about_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>About Leadership - Apex Innovations</title>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": "Alice Taylor",
            "jobTitle": "President & COO",
            "description": "Alice has 20 years of operations experience."
        }
        </script>
    </head>
    <body>
        <h1>Leadership Team</h1>
        <div class="leader-card">
            <h3>Marcus Vance</h3>
            <p class="role-title">Chief Executive Officer</p>
        </div>
        <div class="leader-card">
            <strong>Elena Rostova</strong>, <em>Co-Founder & CTO</em>
        </div>
    </body>
    </html>
    """

    mock_client = MagicMock()
    async def mock_get(url, *args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", str(url)), text=about_html)
    mock_client.get = AsyncMock(side_effect=mock_get)

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    res = asyncio.run(service.run_enrichment("US", "NY", http_client=mock_client))

    enrichment = temp_db.get_company_enrichment("cid_lead_01")
    leaders = enrichment["leaders"]
    assert len(leaders) == 3

    leader_map = {l["full_name"]: l for l in leaders}
    assert "Marcus Vance" in leader_map
    assert leader_map["Marcus Vance"]["role_type"] == "CEO"
    assert leader_map["Marcus Vance"]["confidence"] in ("high", 0.95)

    assert "Elena Rostova" in leader_map
    assert leader_map["Elena Rostova"]["role_type"] in ("Founder", "CTO")

    assert "Alice Taylor" in leader_map
    assert leader_map["Alice Taylor"]["role_type"] in ("President", "COO")

def test_5_published_emails_and_phone_numbers_are_extracted_without_guessing(temp_db):
    """Test 5: Published emails and phones are extracted safely without fabricating or guessing."""
    temp_db.upsert_place_stub({
        "cid": "cid_contact_01",
        "name": "Global Logistics LLC",
        "country_code": "US",
        "region": "NY",
        "website": "https://www.globallogistics.com",
        "website_status": "verified",
        "official_website_url": "https://www.globallogistics.com"
    })

    contact_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Contact Global Logistics</title></head>
    <body>
        <a href="mailto:info@globallogistics.com">Email Us</a>
        <a href="tel:212-555-0144">Call Office</a>
        <p>Support: support@globallogistics.com</p>
        <!-- False positive filters: should NOT be extracted -->
        <img src="logo@2x.png" />
        <a href="mailto:bugs@sentry.io">Sentry</a>
        <a href="mailto:admin@example.com">Example</a>
    </body>
    </html>
    """

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("GET", "https://www.globallogistics.com"), text=contact_html))

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    res = asyncio.run(service.run_enrichment("US", "NY", http_client=mock_client))

    enrichment = temp_db.get_company_enrichment("cid_contact_01")
    contacts = enrichment["contacts"]
    email_vals = [c["contact_value"] for c in contacts if c["contact_type"] == "email"]
    phone_vals = [c["contact_value"] for c in contacts if c["contact_type"] == "phone"]

    assert "info@globallogistics.com" in email_vals
    assert "support@globallogistics.com" in email_vals
    assert "logo@2x.png" not in email_vals
    assert "bugs@sentry.io" not in email_vals
    assert "admin@example.com" not in email_vals
    assert "(212) 555-0144" in phone_vals

def test_6_source_page_evidence_is_stored(temp_db):
    """Test 6: Source-page evidence is recorded on profile, leaders, and contacts."""
    temp_db.upsert_place_stub({
        "cid": "cid_source_01",
        "name": "Beacon Health",
        "country_code": "US",
        "region": "NY",
        "website": "https://www.beaconhealth.com",
        "website_status": "verified",
        "official_website_url": "https://www.beaconhealth.com"
    })

    html_content = """
    <html><body>
        <h3>Dr. Sarah Jenkins</h3><p class="title">Chief Executive Officer</p>
        <a href="mailto:contact@beaconhealth.com">Email</a>
    </body></html>
    """
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("GET", "https://www.beaconhealth.com/team"), text=html_content))

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    asyncio.run(service.run_enrichment("US", "NY", http_client=mock_client))

    enrichment = temp_db.get_company_enrichment("cid_source_01")
    assert len(enrichment["leaders"]) == 1
    assert enrichment["leaders"][0]["source_url"] == "https://www.beaconhealth.com/team"
    assert len(enrichment["contacts"]) == 1
    assert enrichment["contacts"][0]["source_url"] == "https://www.beaconhealth.com/team"
    assert "https://www.beaconhealth.com/team" in enrichment["profile"]["source_pages"]

def test_7_missing_fields_do_not_overwrite_existing_valid_values(temp_db):
    """Test 7: Missing fields from website do not overwrite existing valid place values."""
    temp_db.upsert_place_stub({
        "cid": "cid_protect_01",
        "name": "Established Diner",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.establisheddiner.com",
        "website_status": "verified",
        "official_website_url": "https://www.establisheddiner.com",
        "phone": "(307) 555-9999",
        "address": "123 Old Trail, Cheyenne, WY 82001"
    })

    # Website with no phone or leadership
    html_content = "<html><body><h1>Established Diner</h1><p>Welcome to our diner.</p></body></html>"
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("GET", "https://www.establisheddiner.com"), text=html_content))

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    asyncio.run(service.run_enrichment("US", "WY", http_client=mock_client))

    with temp_db.get_connection() as conn:
        row = conn.execute("SELECT phone, address FROM places WHERE cid='cid_protect_01'").fetchone()
        assert row["phone"] == "(307) 555-9999"
        assert row["address"] == "123 Old Trail, Cheyenne, WY 82001"

def test_8_duplicate_contacts_and_leadership_records_are_avoided(temp_db):
    """Test 8: Re-running enrichment on the same business avoids duplicate contacts and leaders."""
    temp_db.upsert_place_stub({
        "cid": "cid_dedup_01",
        "name": "Dupe Test Corp",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.dupetestcorp.com",
        "website_status": "verified",
        "official_website_url": "https://www.dupetestcorp.com"
    })

    html_content = """
    <html><body>
        <h3>Robert Stone</h3><p class="title">Chief Technology Officer</p>
        <a href="mailto:info@dupetestcorp.com">Email</a>
        <a href="tel:307-555-1122">Phone</a>
    </body></html>
    """
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("GET", "https://www.dupetestcorp.com"), text=html_content))

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    # First run
    asyncio.run(service.run_enrichment("US", "WY", http_client=mock_client))
    # Second run with force=True
    asyncio.run(service.run_enrichment("US", "WY", force=True, http_client=mock_client))

    enrichment = temp_db.get_company_enrichment("cid_dedup_01")
    assert len(enrichment["leaders"]) == 1
    assert len(enrichment["contacts"]) == 2

def test_9_unsafe_urls_and_redirects_are_blocked(temp_db):
    """Test 9: Unsafe loopback or metadata destinations are blocked by crawler."""
    crawler = SafeWebsiteCrawler(check_dns=False)

    # 1. Direct SSRF blocked
    res = asyncio.run(crawler.crawl("http://169.254.169.254/latest/meta-data"))
    assert res == {}

    # 2. Localhost blocked
    res2 = asyncio.run(crawler.crawl("http://127.0.0.1:8000/private"))
    assert res2 == {}

def test_10_http_failures_and_timeouts_are_handled_safely(temp_db):
    """Test 10: HTTP failures and timeouts are caught gracefully without terminating the run."""
    temp_db.upsert_place_stub({
        "cid": "cid_timeout_01",
        "name": "Timeout Enterprise",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.timeoutenterprise.com",
        "website_status": "verified",
        "official_website_url": "https://www.timeoutenterprise.com"
    })

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timed out"))

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    res = asyncio.run(service.run_enrichment("US", "WY", http_client=mock_client))

    assert res["counts"]["failed"] == 1
    enrichment = temp_db.get_company_enrichment("cid_timeout_01")
    assert enrichment["profile"]["extraction_status"] == "failed"
    assert "Failed to crawl" in enrichment["profile"]["last_error"]

def test_11_resume_and_retry_behavior_works(temp_db):
    """Test 11: Resume skips failed runs unless retry_failed=True is passed."""
    temp_db.upsert_place_stub({
        "cid": "cid_retry_01",
        "name": "Retry Grill",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.retrygrill.com",
        "website_status": "verified",
        "official_website_url": "https://www.retrygrill.com"
    })

    # Fail first
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Network Down"))

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    asyncio.run(service.run_enrichment("US", "WY", http_client=mock_client))

    # Second run without retry_failed -> should not process
    success_html = "<html><head><meta name=\"description\" content=\"Retry Grill serves prime steaks and barbecue.\"></head><body>Success</body></html>"
    mock_client.get = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("GET", "https://www.retrygrill.com"), text=success_html))
    res_no_retry = asyncio.run(service.run_enrichment("US", "WY", retry_failed=False, http_client=mock_client))
    assert res_no_retry["counts"]["total_selected"] == 0

    # Third run with retry_failed=True -> should process and succeed
    res_retry = asyncio.run(service.run_enrichment("US", "WY", retry_failed=True, http_client=mock_client))
    assert res_retry["counts"]["total_selected"] == 1
    assert res_retry["counts"]["completed"] == 1

def test_12_country_and_region_restrictions_remain_enforced(temp_db):
    """Test 12: Country and region boundaries are strictly enforced."""
    temp_db.upsert_place_stub({
        "cid": "cid_us_wy",
        "name": "Wyoming Biz",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.wybiz.com",
        "website_status": "verified",
        "official_website_url": "https://www.wybiz.com"
    })
    temp_db.upsert_place_stub({
        "cid": "cid_mx_jal",
        "name": "Jalisco Biz",
        "country_code": "MX",
        "region": "JAL",
        "website": "https://www.jalbiz.com",
        "website_status": "verified",
        "official_website_url": "https://www.jalbiz.com"
    })

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("GET", "https://www.wybiz.com"), text="<html><body>WY</body></html>"))

    # Only WY
    res_wy = asyncio.run(service.run_enrichment("US", "WY", http_client=mock_client))
    assert res_wy["counts"]["total_selected"] == 1
    assert res_wy["results"][0]["cid"] == "cid_us_wy"

    # Invalid country raises ValueError
    with pytest.raises(ValueError, match="not supported for Pipeline 2"):
        asyncio.run(service.run_enrichment("FR", http_client=mock_client))

def test_13_dry_run_mode_does_not_write_extraction_results(temp_db):
    """Test 13: Dry-run extracts and returns results in memory without writing to database."""
    temp_db.upsert_place_stub({
        "cid": "cid_dry_01",
        "name": "Dry Run Cafe",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.dryruncafe.com",
        "website_status": "verified",
        "official_website_url": "https://www.dryruncafe.com"
    })

    html_content = """
    <html><body>
        <h3>Alice Dry</h3><p class="title">Chief Executive Officer</p>
        <a href="mailto:hello@dryruncafe.com">Email</a>
    </body></html>
    """
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("GET", "https://www.dryruncafe.com"), text=html_content))

    service = WebsiteEnrichmentService(temp_db, check_dns=False)
    res = asyncio.run(service.run_enrichment("US", "WY", dry_run=True, http_client=mock_client))

    # Results returned in memory
    assert len(res["results"]) == 1
    assert res["results"][0]["leaders_count"] == 1
    assert res["results"][0]["contacts_count"] == 1

    # But database remains clean
    enrichment = temp_db.get_company_enrichment("cid_dry_01")
    assert enrichment["profile"] is None
    assert len(enrichment["leaders"]) == 0
    assert len(enrichment["contacts"]) == 0

def test_14_existing_step1_and_step2_functionality_remains_intact(temp_db):
    """Test 14: Step 1 (scope/progress) and Step 2 (discovery) remain fully functional."""
    prog = temp_db.get_region_progress("US", "WY")
    assert "total_tasks" in prog

    # Discovery update
    temp_db.upsert_place_stub({
        "cid": "cid_integ_01",
        "name": "Integ Biz",
        "country_code": "US",
        "region": "WY",
        "website": "https://www.integbiz.com"
    })
    temp_db.update_business_website_verification(
        cid="cid_integ_01",
        status="verified",
        candidate="https://www.integbiz.com",
        discovery_method="existing",
        evidence={"score": 100}
    )

    with temp_db.get_connection() as conn:
        row = conn.execute("SELECT website_status, official_website_url FROM places WHERE cid='cid_integ_01'").fetchone()
        assert row["website_status"] == "verified"
        assert row["official_website_url"] == "https://www.integbiz.com"
