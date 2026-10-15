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
from gmaps_scraper.website.ssrf import is_safe_url, normalize_url
from gmaps_scraper.website.verifier import WebsiteVerifier
from gmaps_scraper.website.discovery import WebsiteDiscoverer
from gmaps_scraper.website.service import WebsiteDiscoveryService

runner = CliRunner()

@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_website.sqlite"
    db = Database(db_path)
    db.init_schema()
    return db

def test_1_existing_valid_website_is_preserved(temp_db):
    """Test 1: Existing valid website URL is preserved and verified."""
    temp_db.upsert_place_stub({
        "cid": "cid_valid_01",
        "name": "Acme Coffee Co",
        "country_code": "US",
        "region": "WY",
        "city": "Cheyenne",
        "website": "https://www.acmecoffeeco.com",
        "phone": "307-555-0199"
    })

    # Mock HTTP response confirming Acme Coffee Co
    mock_client = MagicMock()
    mock_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://www.acmecoffeeco.com"),
        text="<html><head><title>Acme Coffee Co - Cheyenne WY</title></head><body><h1>Welcome to Acme Coffee Co</h1><p>Call us at 307-555-0199</p></body></html>"
    )
    mock_client.get = AsyncMock(return_value=mock_resp)

    service = WebsiteDiscoveryService(temp_db, check_dns=False)
    res = asyncio.run(service.run_discovery(
        country_code="US",
        region_code="WY",
        http_client=mock_client
    ))

    assert res["counts"]["verified"] == 1
    with temp_db.get_connection() as conn:
        row = conn.execute("SELECT website, website_status, website_discovery_method, website_evidence FROM places WHERE cid='cid_valid_01'").fetchone()
        assert row["website"] == "https://www.acmecoffeeco.com"
        assert row["website_status"] == "verified"
        assert row["website_discovery_method"] == "existing"
        ev = json.loads(row["website_evidence"])
        assert ev["phone_match"] is True
        assert ev["matched_in_domain"] is True

def test_2_missing_website_is_identified_correctly(temp_db):
    """Test 2: Missing website is detected and marked website_missing when discovery yields no URL."""
    temp_db.upsert_place_stub({
        "cid": "cid_missing_01",
        "name": "No Web Diner",
        "country_code": "US",
        "region": "WY",
        "city": "Casper",
        "website": None
    })

    mock_client = MagicMock()
    # Mock Google Maps search RPC returning empty items
    mock_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://www.google.com/search"),
        text=")]}'\n[[null, []]]"
    )
    mock_client.get = AsyncMock(return_value=mock_resp)

    service = WebsiteDiscoveryService(temp_db, check_dns=False)
    res = asyncio.run(service.run_discovery(
        country_code="US",
        region_code="WY",
        http_client=mock_client
    ))

    assert res["counts"]["website_missing"] == 1
    with temp_db.get_connection() as conn:
        row = conn.execute("SELECT website, website_status, website_discovery_method FROM places WHERE cid='cid_missing_01'").fetchone()
        assert row["website"] is None
        assert row["website_status"] == "website_missing"
        assert row["website_discovery_method"] == "gmaps_rpc_empty"

def test_3_candidate_url_can_be_stored_without_being_marked_verified(temp_db):
    """Test 3: An ambiguous candidate can be stored as candidate / needs_review without claiming it is verified."""
    temp_db.upsert_place_stub({
        "cid": "cid_ambiguous_01",
        "name": "General Store",
        "country_code": "MX",
        "region": "JAL",
        "city": "Guadalajara",
        "website": "https://www.randomshopdirectory.com/general-store"
    })

    mock_client = MagicMock()
    mock_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://www.randomshopdirectory.com/general-store"),
        text="<html><head><title>Online Directory - General Stores Listing</title></head><body>Directory of items</body></html>"
    )
    mock_client.get = AsyncMock(return_value=mock_resp)

    service = WebsiteDiscoveryService(temp_db, check_dns=False)
    res = asyncio.run(service.run_discovery(
        country_code="MX",
        region_code="JAL",
        http_client=mock_client
    ))

    assert res["counts"]["needs_review"] == 1
    with temp_db.get_connection() as conn:
        row = conn.execute("SELECT website, website_status, website_candidate FROM places WHERE cid='cid_ambiguous_01'").fetchone()
        assert row["website_status"] == "needs_review"
        assert row["website_candidate"] == "https://www.randomshopdirectory.com/general-store"

def test_4_verification_succeeds_with_multi_signals():
    """Test 4: Verification succeeds when sufficient matching evidence exists across name, location, and phone."""
    verifier = WebsiteVerifier(check_dns=False)
    biz = {
        "name": "Blue Mountain Cafe",
        "city": "Austin",
        "region": "TX",
        "phone": "512-555-1234",
        "category": "Cafe"
    }

    mock_client = MagicMock()
    mock_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://bluemountaincafe.com"),
        text="<html><head><title>Blue Mountain Cafe - Best Coffee in Austin TX</title></head><body><h1>Welcome to Blue Mountain Cafe</h1><p>Contact: (512) 555-1234</p></body></html>"
    )
    mock_client.get = AsyncMock(return_value=mock_resp)

    res = asyncio.run(verifier.verify("https://bluemountaincafe.com", biz, http_client=mock_client))
    assert res["status"] == "verified"
    assert res["verified"] is True
    assert res["evidence"]["matched_in_domain"] is True
    assert res["evidence"]["phone_match"] is True
    assert res["evidence"]["location_match"] is True

def test_5_mismatched_company_website_is_not_verified():
    """Test 5: A mismatched company website is rejected from verified status."""
    verifier = WebsiteVerifier(check_dns=False)
    biz = {
        "name": "Precision Auto Repair",
        "city": "Denver",
        "region": "CO",
        "phone": "303-555-8888"
    }

    mock_client = MagicMock()
    mock_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://unrelatedbakery.com"),
        text="<html><head><title>Sweet Cupcakes Bakery</title></head><body>Baking fresh cookies and cakes daily in Seattle</body></html>"
    )
    mock_client.get = AsyncMock(return_value=mock_resp)

    res = asyncio.run(verifier.verify("https://unrelatedbakery.com", biz, http_client=mock_client))
    assert res["status"] != "verified"
    assert res["verified"] is False
    assert res["status"] in ("needs_review", "failed")

def test_6_ssrf_protection_blocks_private_and_local_destinations():
    """Test 6: Invalid URLs and unsafe private-network / localhost / metadata destinations are rejected."""
    # Localhost
    safe, err = is_safe_url("http://localhost:8080/admin", check_dns=False)
    assert not safe
    assert "local/internal" in err

    safe_loop, _ = is_safe_url("http://127.0.0.1/status", check_dns=False)
    assert not safe_loop

    # Cloud metadata IP
    safe_meta, _ = is_safe_url("http://169.254.169.254/latest/meta-data/", check_dns=False)
    assert not safe_meta

    # Private IP 10.x.x.x
    safe_priv1, _ = is_safe_url("http://10.0.0.1/", check_dns=False)
    assert not safe_priv1

    # Private IP 192.168.x.x
    safe_priv2, _ = is_safe_url("https://192.168.1.100/config", check_dns=False)
    assert not safe_priv2

    # Disallowed non-HTTP scheme
    safe_file, err_file = is_safe_url("file:///etc/passwd", check_dns=False)
    assert not safe_file
    assert "Disallowed scheme" in err_file

    # Blocked port
    safe_port, err_port = is_safe_url("http://example.com:22/", check_dns=False)
    assert not safe_port
    assert "Port 22 is blocked" in err_port

def test_7_timeouts_and_http_errors_handled_gracefully():
    """Test 7: HTTP timeouts and error codes degrade cleanly to failed status."""
    verifier = WebsiteVerifier(timeout_seconds=2.0, check_dns=False)
    biz = {"name": "Timeout Cafe"}

    # Mock timeout
    mock_client_timeout = MagicMock()
    mock_client_timeout.get = AsyncMock(side_effect=httpx.TimeoutException("Timed out"))

    res_timeout = asyncio.run(verifier.verify("https://slowserver.com", biz, http_client=mock_client_timeout))
    assert res_timeout["status"] == "failed"
    assert "Timed Out" in res_timeout["error"]

    # Mock HTTP 500
    mock_client_500 = MagicMock()
    mock_client_500.get = AsyncMock(return_value=httpx.Response(500, request=httpx.Request("GET", "https://error.com")))

    res_500 = asyncio.run(verifier.verify("https://error.com", biz, http_client=mock_client_500))
    assert res_500["status"] == "failed"
    assert "HTTP 500" in res_500["error"]

def test_8_rerunning_discovery_does_not_create_duplicates(temp_db):
    """Test 8: Re-running discovery is idempotent and does not create duplicate business rows."""
    temp_db.upsert_place_stub({
        "cid": "cid_idem_01",
        "name": "Sol Coffee",
        "country_code": "US",
        "region": "WY",
        "website": "https://solcoffee.com"
    })

    mock_client = MagicMock()
    mock_resp = httpx.Response(
        200,
        request=httpx.Request("GET", "https://solcoffee.com"),
        text="<html><title>Sol Coffee</title><body>Sol Coffee Cafe</body></html>"
    )
    mock_client.get = AsyncMock(return_value=mock_resp)

    service = WebsiteDiscoveryService(temp_db, check_dns=False)

    # First run
    asyncio.run(service.run_discovery(country_code="US", region_code="WY", http_client=mock_client))
    # Second run with force=True
    asyncio.run(service.run_discovery(country_code="US", region_code="WY", force=True, http_client=mock_client))

    with temp_db.get_connection() as conn:
        count = conn.execute("SELECT count(*) FROM places WHERE cid='cid_idem_01'").fetchone()[0]
        assert count == 1, "Duplicate business row created!"

def test_9_retry_failed_and_resume(temp_db):
    """Test 9: Failed website discovery work can be retried or resumed."""
    temp_db.upsert_place_stub({
        "cid": "cid_retry_01",
        "name": "Retry Grill",
        "country_code": "US",
        "region": "WY",
        "website": "https://retrygrill.com"
    })

    # Simulate first run failing
    mock_fail_client = MagicMock()
    mock_fail_client.get = AsyncMock(side_effect=Exception("Temporary network glitch"))

    service = WebsiteDiscoveryService(temp_db, check_dns=False)
    asyncio.run(service.run_discovery(country_code="US", region_code="WY", http_client=mock_fail_client))

    with temp_db.get_connection() as conn:
        status_1 = conn.execute("SELECT website_status FROM places WHERE cid='cid_retry_01'").fetchone()[0]
        assert status_1 == "failed"

    # Second run with retry_failed=True succeeds
    mock_ok_client = MagicMock()
    mock_ok_client.get = AsyncMock(return_value=httpx.Response(
        200,
        request=httpx.Request("GET", "https://retrygrill.com"),
        text="<html><title>Retry Grill</title><body>Steakhouse Grill</body></html>"
    ))

    asyncio.run(service.run_discovery(country_code="US", region_code="WY", retry_failed=True, http_client=mock_ok_client))

    with temp_db.get_connection() as conn:
        status_2 = conn.execute("SELECT website_status FROM places WHERE cid='cid_retry_01'").fetchone()[0]
        assert status_2 == "verified"

def test_10_country_and_region_scope_validation():
    """Test 10: Scope validation rejects countries outside US and MX."""
    service = WebsiteDiscoveryService(MagicMock())
    with pytest.raises(ValueError) as exc:
        asyncio.run(service.run_discovery(country_code="CA"))
    assert "not supported for Pipeline 2" in str(exc.value)

    with pytest.raises(ValueError):
        asyncio.run(service.run_discovery(country_code="US", region_code="INVALID_STATE"))

def test_11_existing_database_records_unaffected(temp_db):
    """Test 11: Unrelated existing columns (reviews, hours, lat/lng) remain intact."""
    temp_db.upsert_place_stub({
        "cid": "cid_preserve_01",
        "name": "Legacy Place",
        "country_code": "US",
        "region": "WY",
        "latitude": 41.1400,
        "longitude": -104.8200,
        "rating": 4.8,
        "review_count": 120,
        "website": "https://legacyplace.com"
    })

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=httpx.Response(
        200,
        request=httpx.Request("GET", "https://legacyplace.com"),
        text="<html><title>Legacy Place</title><body>Place</body></html>"
    ))

    service = WebsiteDiscoveryService(temp_db, check_dns=False)
    asyncio.run(service.run_discovery(country_code="US", region_code="WY", http_client=mock_client))

    with temp_db.get_connection() as conn:
        row = conn.execute("SELECT latitude, longitude, rating, review_count FROM places WHERE cid='cid_preserve_01'").fetchone()
        assert row["latitude"] == 41.1400
        assert row["longitude"] == -104.8200
        assert row["rating"] == 4.8
        assert row["review_count"] == 120

def test_12_cli_arguments_and_dry_run_mode(temp_db, tmp_path):
    """Test 12: CLI runs cleanly and dry-run mode does not modify the database."""
    temp_db.upsert_place_stub({
        "cid": "cid_cli_01",
        "name": "Dry Run Cafe",
        "country_code": "US",
        "region": "WY",
        "website": "https://dryrun.com"
    })

    cfg_file = tmp_path / "user_test.yaml"
    cfg_file.write_text(f"db_path: \"{str(temp_db.db_path).replace(chr(92), '/')}\"\n", encoding="utf-8")

    # Execute CLI with dry-run
    result = runner.invoke(app, [
        "website-discover",
        "--country", "US",
        "--region", "WY",
        "--limit", "1",
        "--dry-run",
        "--config", str(cfg_file)
    ])
    assert result.exit_code == 0
    assert "DRY-RUN" in result.output

    # Check database: website_status should STILL be None because of dry-run
    with temp_db.get_connection() as conn:
        status = conn.execute("SELECT website_status FROM places WHERE cid='cid_cli_01'").fetchone()[0]
        assert status is None
