import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from gmaps_scraper.geo.nlparse import parse_natural_language_query
from gmaps_scraper.geo.resolve import (
    resolve_location,
    _parse_coordinates_or_bbox,
    _classify_feature,
    get_country_bias,
    estimate_scrape_complexity
)
from gmaps_scraper.db import Database

def test_nlparse_all_cases():
    # 1. "hospitals in Chicago"
    q, loc = parse_natural_language_query("hospitals in Chicago")
    assert q == "hospitals"
    assert loc == "Chicago"

    # 2. "coffee near me"
    q, loc = parse_natural_language_query("coffee near me", default_location="Austin, TX")
    assert q == "coffee"
    assert loc == "Austin, TX"

    # 3. "gyms around Austin, TX"
    q, loc = parse_natural_language_query("gyms around Austin, TX")
    assert q == "gyms"
    assert loc == "Austin, TX"

    # 4. "shops in the Bronx"
    q, loc = parse_natural_language_query("shops in the Bronx")
    assert q == "shops"
    assert "Bronx" in loc

    # 5. "bakeries in downtown Lyon"
    q, loc = parse_natural_language_query("bakeries in downtown Lyon")
    assert q == "bakeries"
    assert loc == "downtown Lyon"

    # 6. Location containing the word "in"
    q, loc = parse_natural_language_query("dine in restaurants in Paris")
    assert q == "dine in restaurants"
    assert loc == "Paris"

    # 7. No separator case
    q, loc = parse_natural_language_query("dentists")
    assert q == "dentists"
    assert loc is None

def test_resolve_coordinates_and_bbox():
    # Explicit bbox
    res_bbox = _parse_coordinates_or_bbox("40.70,-74.02,0.03")
    assert res_bbox is not None
    assert res_bbox["lat"] == 40.70
    assert res_bbox["lng"] == -74.02
    assert res_bbox["span_deg"] == 0.03
    assert res_bbox["resolved_from"] == "explicit_bbox"

    # Coordinates + radius km
    res_rad = _parse_coordinates_or_bbox("40.7128,-74.0060@5km")
    assert res_rad is not None
    assert round(res_rad["lat"], 4) == 40.7128
    assert round(res_rad["lng"], 4) == -74.0060
    assert round(res_rad["span_deg"], 2) == 0.09
    assert res_rad["resolved_from"] == "coordinates"

    # Coordinates without radius
    res_point = _parse_coordinates_or_bbox("51.5074,-0.1278")
    assert res_point is not None
    assert res_point["span_deg"] == 0.03

    # Invalid
    assert _parse_coordinates_or_bbox("not_coords") is None

def test_classify_feature_spans():
    # country
    cls, span = _classify_feature(None, vp_meters=800000)
    assert cls == "country"
    assert span == 2.0

    # state_region
    cls, span = _classify_feature(None, vp_meters=350000)
    assert cls == "state_region"
    assert span == 0.5

    # city
    cls, span = _classify_feature(None, vp_meters=50000)
    assert cls == "city"
    assert span == 0.10

    # district_area
    cls, span = _classify_feature(None, vp_meters=10000)
    assert cls == "district_area"
    assert span == 0.03

    # street_address
    cls, span = _classify_feature(None, vp_meters=1500)
    assert cls == "street_address"
    assert span == 0.005

def test_country_bias():
    gl, hl = get_country_bias("FR")
    assert gl == "fr" and hl == "fr"

    gl, hl = get_country_bias("JP")
    assert gl == "jp" and hl == "ja"

    gl, hl = get_country_bias("US")
    assert gl == "us" and hl == "en"

    gl, hl = get_country_bias(None)
    assert gl == "us" and hl == "en"

@pytest.mark.asyncio
async def test_resolve_caching_and_mock(tmp_path):
    db = Database(tmp_path / "test_geo.sqlite")
    db.init_schema()

    # Pre-seed cache
    db.set_geo_cache({
        "query": "lyon, france",
        "label": "Lyon, France",
        "lat": 45.764,
        "lng": 4.835,
        "span_deg": 0.10,
        "country": "France",
        "country_code": "FR",
        "confidence": 0.95,
        "feature_class": "city",
        "resolved_from": "search_rpc"
    })

    # Call resolve_location: should hit cache
    res = await resolve_location("lyon, france", db=db)
    assert res["resolved_from"] == "cache"
    assert res["lat"] == 45.764
    assert res["lng"] == 4.835
    assert res["span_deg"] == 0.10
    assert res["country_code"] == "FR"

@pytest.mark.asyncio
async def test_resolve_failure_raises(tmp_path):
    db = Database(tmp_path / "test_geo_fail.sqlite")
    db.init_schema()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))

    with pytest.raises(ValueError):
        await resolve_location("NonExistentUnresolvablePlaceXYZ999", db=db, http_client=mock_client)
