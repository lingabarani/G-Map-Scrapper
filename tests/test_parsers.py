import json
from pathlib import Path
import pytest

from gmaps_scraper.parser_state import (
    strip_google_xssi_prefix,
    extract_app_initialization_state,
    assert_html_fallback_state,
    MissingHtmlFallbackStateError,
)
from gmaps_scraper.search import parse_search_rpc_response
from gmaps_scraper.details import (
    parse_place_details_response,
    assert_attributes_path,
    MissingAttributesPathError,
)
from gmaps_scraper.reviews import (
    parse_reviews_response,
    assert_reviews_root_path,
    MissingReviewsRootPathError,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

def test_strip_google_xssi_prefix():
    raw = ")]}'\n{\"key\": \"value\"}"
    clean = strip_google_xssi_prefix(raw)
    assert clean == '{"key": "value"}'
    data = json.loads(clean)
    assert data["key"] == "value"

def test_extract_app_initialization_state():
    html_file = FIXTURES_DIR / "place_page.html"
    assert html_file.exists()
    html_content = html_file.read_text(encoding="utf-8", errors="replace")
    state = extract_app_initialization_state(html_content)
    assert state is not None
    assert isinstance(state, list)
    assert len(state) > 0

    # Path assertion for state[5][3][2]
    target = assert_html_fallback_state(state)
    assert target[0] == "0x89c25bb26cc76c9f:0xa38e11c309a5d41e"
    assert target[1] == "787 Coffee"

def test_parse_search_rpc_and_address_pollution_correction():
    search_file = FIXTURES_DIR / "search_rpc.json"
    assert search_file.exists()
    raw = search_file.read_text(encoding="utf-8")
    stubs = parse_search_rpc_response(raw)
    assert len(stubs) > 0
    s0 = stubs[0]
    assert s0.cid != ""
    assert s0.name == "787 Coffee"
    assert s0.latitude is not None
    assert s0.longitude is not None
    # Correction 0.4(a): Address must NOT be name-polluted
    assert s0.address == "66 Pearl St, New York, NY 10004"
    assert not s0.address.startswith("787 Coffee,")

def test_parse_place_details():
    details_file = FIXTURES_DIR / "place_details_rpc.json"
    assert details_file.exists()
    raw = details_file.read_text(encoding="utf-8")
    cid = "11785501865243169822"
    details = parse_place_details_response(raw, cid)
    assert details["cid"] == cid
    assert len(details["hours"]) > 0
    assert len(details["attributes"]) > 0
    assert "timezone" in details
    assert "claimed_status" in details

    # Path assertion for attributes
    clean = strip_google_xssi_prefix(raw)
    data = json.loads(clean)
    attrs = assert_attributes_path(data[0][1][1][14])
    assert len(attrs) > 0

def test_parse_reviews_rpc():
    reviews_file = FIXTURES_DIR / "reviews_rpc.json"
    assert reviews_file.exists()
    raw = reviews_file.read_text(encoding="utf-8")
    cid = "11785501865243169822"
    reviews, token = parse_reviews_response(raw, cid)
    assert len(reviews) > 0
    r0 = reviews[0]
    assert r0["cid"] == cid
    assert r0["author_name"] != ""
    assert r0["rating"] in [1, 2, 3, 4, 5]
    assert r0["timestamp_ms"] is not None
    assert token is not None

    clean = strip_google_xssi_prefix(raw)
    data = json.loads(clean)
    container = assert_reviews_root_path(data)
    assert len(container) > 2

def test_parse_additional_fixture_no_website_no_phone():
    fixture_path = FIXTURES_DIR / "place_no_website_no_phone.json"
    assert fixture_path.exists()
    raw = fixture_path.read_text(encoding="utf-8")
    stubs = parse_search_rpc_response(raw)
    assert len(stubs) > 0
    s = stubs[0]
    assert s.name != ""
    assert s.website is None
    assert s.phone is None

def test_parse_additional_fixture_permanently_closed():
    fixture_path = FIXTURES_DIR / "place_permanently_closed.json"
    assert fixture_path.exists()
    raw = fixture_path.read_text(encoding="utf-8")
    clean = strip_google_xssi_prefix(raw)
    data = json.loads(clean)
    items = data[0][1]
    # Check that Uncle Boons has CLOSED status in p[88]
    closed_found = False
    for entry in items[1:]:
        if len(entry) > 14 and isinstance(entry[14], list):
            p = entry[14]
            if len(p) > 88 and isinstance(p[88], list) and len(p[88]) > 0 and p[88][0] == "CLOSED":
                closed_found = True
                break
    assert closed_found is True

def test_parse_second_reviews_fixture():
    fixture_path = FIXTURES_DIR / "reviews_second_place.json"
    assert fixture_path.exists()
    raw = fixture_path.read_text(encoding="utf-8")
    cid = "17937989352932994479"  # Thai Diner CID
    reviews, token = parse_reviews_response(raw, cid)
    assert len(reviews) == 10
    assert reviews[0]["author_name"] != ""
    assert token is not None

def test_explicit_path_assertion_errors():
    with pytest.raises(MissingAttributesPathError):
        assert_attributes_path([])

    with pytest.raises(MissingReviewsRootPathError):
        assert_reviews_root_path([])

    with pytest.raises(MissingHtmlFallbackStateError):
        assert_html_fallback_state([])

def test_parser_edge_cases_and_graceful_degradation():
    """C2, C3, C4 requirements: No crash on malformed JSON, empty results, unicode, emoji, 10k strings."""
    # Empty string or malformed truncated JSON
    assert parse_search_rpc_response("") == []
    assert parse_search_rpc_response(")]}'\n[truncated") == []
    assert parse_place_details_response("", "123") == {}
    assert parse_place_details_response("{corrupt", "123") == {}
    revs, tok = parse_reviews_response("not json", "123")
    assert revs == [] and tok is None

    # Response without )]}' prefix
    p_entry = [None] * 80
    p_entry[10] = "0x1:0xa38e"
    p_entry[11] = "Clean Place"
    p_entry[39] = "100 Main St"
    p_entry[78] = "ChIJ123"
    entry = [None] * 15
    entry[14] = p_entry
    valid_no_prefix = json.dumps([[None, ["header", entry]]])
    stubs = parse_search_rpc_response(valid_no_prefix)
    assert len(stubs) == 1
    assert stubs[0].name == "Clean Place"

    # Emoji and 10k-char string in name
    long_str = "Cafe " + "☕" * 100 + "A" * 10000
    p_long = [None] * 80
    p_long[10] = "0x1:0xa38e"
    p_long[11] = long_str
    p_long[39] = "456 Side St"
    p_long[78] = "ChIJ123"
    entry_long = [None] * 15
    entry_long[14] = p_long
    emoji_payload = json.dumps([[None, ["header", entry_long]]])
    stubs_long = parse_search_rpc_response(emoji_payload)
    assert len(stubs_long) == 1
    assert len(stubs_long[0].name) > 10000
