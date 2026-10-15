import base64
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

from ..config import load_config
from ..http import HttpClient
from ..search import build_search_pb
from ..parser_state import strip_google_xssi_prefix, extract_app_initialization_state
from ..taxonomy import parse_address, load_countries
from ..db import Database

# Country to Google search language (hl) mapping for location bias
COUNTRY_HL_MAP = {
    "FR": "fr", "DE": "de", "IT": "it", "ES": "es", "JP": "ja",
    "CN": "zh-CN", "BR": "pt-BR", "RU": "ru", "KR": "ko",
    "NL": "nl", "PL": "pl", "SE": "sv", "NO": "no", "DK": "da",
    "FI": "fi", "GR": "el", "TR": "tr", "PT": "pt", "MX": "es",
    "AR": "es", "CL": "es", "CO": "es", "PE": "es",
    "SA": "ar", "AE": "ar", "EG": "ar", "IL": "he", "IN": "en",
    "US": "en", "GB": "en", "CA": "en", "AU": "en", "NZ": "en", "IE": "en",
}

_GEO_SPANS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "geo_spans.yaml"
_GEO_SPANS_CACHE: Optional[Dict[str, Any]] = None

def load_geo_spans() -> Dict[str, Any]:
    global _GEO_SPANS_CACHE
    if _GEO_SPANS_CACHE is not None:
        return _GEO_SPANS_CACHE
    if _GEO_SPANS_PATH.exists():
        with open(_GEO_SPANS_PATH, "r", encoding="utf-8") as f:
            _GEO_SPANS_CACHE = yaml.safe_load(f) or {}
    else:
        _GEO_SPANS_CACHE = {
            "country": {"span_deg": 2.0},
            "state_region": {"span_deg": 0.5},
            "city": {"span_deg": 0.10},
            "district_area": {"span_deg": 0.03},
            "street_address": {"span_deg": 0.005},
        }
    return _GEO_SPANS_CACHE

def get_country_bias(country_code: Optional[str]) -> Tuple[str, str]:
    """Returns (gl, hl) query parameters for Google Maps search bias."""
    if not country_code or country_code.lower() == "unknown":
        return "us", "en"
    cc = country_code.upper()
    gl = cc.lower()
    hl = COUNTRY_HL_MAP.get(cc, "en")
    return gl, hl

def estimate_scrape_complexity(
    span_deg: float,
    max_places: int = 200,
    max_depth: int = 3
) -> Dict[str, int]:
    """Estimates tiles, total HTTP requests, and runtime in seconds."""
    if span_deg >= 1.0:
        est_tiles = 64
    elif span_deg >= 0.3:
        est_tiles = 21
    elif span_deg >= 0.08:
        est_tiles = 16
    elif span_deg >= 0.02:
        est_tiles = 5
    else:
        est_tiles = 1

    est_tiles = min(est_tiles, 4 ** max_depth)
    est_requests = est_tiles + min(max_places, est_tiles * 18)
    est_seconds = max(2, int(est_requests * 0.6))
    return {
        "estimated_tiles": est_tiles,
        "estimated_requests": est_requests,
        "estimated_seconds": est_seconds
    }

def _parse_coordinates_or_bbox(location_str: str) -> Optional[Dict[str, Any]]:
    """
    Parses explicit bbox ("lat,lng,span_deg") or coordinates ("lat,lng" / "lat,lng@radius").
    """
    s = location_str.strip()
    # Check explicit bbox: "40.70,-74.02,0.03"
    parts = [p.strip() for p in s.split(",")]
    if len(parts) == 3:
        try:
            lat = float(parts[0])
            lng = float(parts[1])
            span = float(parts[2])
            if -90 <= lat <= 90 and -180 <= lng <= 180 and span > 0:
                est = estimate_scrape_complexity(span)
                return {
                    "label": f"({lat:.4f}, {lng:.4f}, span={span}deg)",
                    "lat": lat,
                    "lng": lng,
                    "span_deg": span,
                    "country": None,
                    "country_code": None,
                    "confidence": 1.0,
                    "resolved_from": "explicit_bbox",
                    "feature_class": "custom_bbox",
                    **est
                }
        except ValueError:
            pass

    # Check coordinate + optional radius: "40.7128,-74.0060@5km" or "40.7128,-74.0060"
    coord_re = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)(?:@(\d+(?:\.\d+)?)\s*(km|m|mi)?)?$", s, re.IGNORECASE)
    if coord_re:
        lat = float(coord_re.group(1))
        lng = float(coord_re.group(2))
        radius_val = coord_re.group(3)
        unit = (coord_re.group(4) or "km").lower()

        if -90 <= lat <= 90 and -180 <= lng <= 180:
            if radius_val:
                r = float(radius_val)
                if unit == "km":
                    span_deg = (r / 111.0) * 2.0
                elif unit == "m":
                    span_deg = ((r / 1000.0) / 111.0) * 2.0
                elif unit == "mi":
                    span_deg = ((r * 1.60934) / 111.0) * 2.0
                else:
                    span_deg = (r / 111.0) * 2.0
            else:
                # Default span for point coordinate: ~0.03 deg (~3.3 km)
                span_deg = 0.03

            span_deg = max(0.001, min(span_deg, 5.0))
            est = estimate_scrape_complexity(span_deg)
            return {
                "label": f"({lat:.4f}, {lng:.4f})",
                "lat": lat,
                "lng": lng,
                "span_deg": round(span_deg, 5),
                "country": None,
                "country_code": None,
                "confidence": 1.0,
                "resolved_from": "coordinates",
                "feature_class": "point_radius",
                **est
            }

    return None

def _classify_feature(
    b64_str: Optional[str],
    vp_meters: Optional[float],
    address_str: Optional[str] = None
) -> Tuple[str, float]:
    """
    Classifies feature type to choose span_deg from config/geo_spans.yaml:
      country        -> 2.0 deg
      state/region   -> 0.5 deg
      city           -> 0.10 deg
      district/area  -> 0.03 deg
      street/address -> 0.005 deg
    """
    spans = load_geo_spans()
    tokens: List[str] = []

    if b64_str and isinstance(b64_str, str):
        try:
            dec = base64.b64decode(b64_str + "==")
            found = re.findall(rb'[a-zA-Z_]{3,}', dec)
            tokens = [w.decode('latin1').lower() for w in found]
        except Exception:
            pass

    # 1. Match by decoded protobuf type tokens
    for t in tokens:
        if t in ("country", "nation"):
            return "country", spans.get("country", {}).get("span_deg", 2.0)
        if any(k in t for k in ("state", "province", "region", "administrative_area_level_1", "us_state")):
            return "state_region", spans.get("state_region", {}).get("span_deg", 0.5)
        if any(k in t for k in ("sublocality", "neighborhood", "ward", "district", "quarter")):
            return "district_area", spans.get("district_area", {}).get("span_deg", 0.03)
        if any(k in t for k in ("premise", "street_address", "route", "compound_building", "establishment", "building")):
            return "street_address", spans.get("street_address", {}).get("span_deg", 0.005)
        if any(k in t for k in ("locality", "city", "town", "municipality")):
            return "city", spans.get("city", {}).get("span_deg", 0.10)

    # 2. Match by viewport meters altitude returned by Google
    if vp_meters is not None:
        if vp_meters >= 600000:
            return "country", spans.get("country", {}).get("span_deg", 2.0)
        elif vp_meters >= 250000:
            return "state_region", spans.get("state_region", {}).get("span_deg", 0.5)
        elif vp_meters >= 20000:
            return "city", spans.get("city", {}).get("span_deg", 0.10)
        elif vp_meters >= 5000:
            return "district_area", spans.get("district_area", {}).get("span_deg", 0.03)
        else:
            return "street_address", spans.get("street_address", {}).get("span_deg", 0.005)

    # 3. Fallback based on address components count
    if address_str:
        parts = [p.strip() for p in address_str.split(",") if p.strip()]
        if len(parts) == 1:
            return "country", spans.get("country", {}).get("span_deg", 2.0)
        elif len(parts) >= 4:
            return "street_address", spans.get("street_address", {}).get("span_deg", 0.005)

    # Default to city
    return "city", spans.get("city", {}).get("span_deg", 0.10)

async def _resolve_via_search_rpc(
    query: str,
    http_client: HttpClient
) -> Optional[Dict[str, Any]]:
    """Method 1: Search RPC."""
    pb = build_search_pb(0.0, 0.0, span_meters=50000, num_results=5)
    url = f"https://www.google.com/search?tbm=map&authuser=0&hl=en&gl=us&pb={pb}&q={urllib.parse.quote(query)}"
    try:
        resp = await http_client.get(url)
    except Exception:
        return None

    clean = strip_google_xssi_prefix(resp.text)
    try:
        data = json.loads(clean)
    except Exception:
        return None

    if not isinstance(data, list) or len(data) == 0:
        return None

    results = data[0][1] if len(data[0]) > 1 and isinstance(data[0][1], list) else []
    if not results:
        return None

    # Inspect top result
    top_entry = results[0]
    # Sometimes top_entry is place listing, sometimes results[1] is place listing
    p = None
    if isinstance(top_entry, list) and len(top_entry) > 14 and isinstance(top_entry[14], list):
        p = top_entry[14]
    elif len(results) > 1 and isinstance(results[1], list) and len(results[1]) > 14 and isinstance(results[1][14], list):
        p = results[1][14]

    if not p:
        return None

    # Extract coordinates from p[9]
    lat, lng = None, None
    if len(p) > 9 and isinstance(p[9], list) and len(p[9]) > 3:
        if p[9][2] is not None and p[9][3] is not None:
            lat = float(p[9][2])
            lng = float(p[9][3])

    if lat is None or lng is None:
        # Check viewport at data[1][0]
        if len(data) > 1 and isinstance(data[1], list) and len(data[1]) > 0 and isinstance(data[1][0], list):
            if len(data[1][0]) > 2:
                lng = float(data[1][0][1])
                lat = float(data[1][0][2])

    if lat is None or lng is None:
        return None

    name = str(p[11]) if len(p) > 11 and p[11] is not None else query
    address = str(p[18]) if len(p) > 18 and p[18] is not None else name

    # Viewport altitude
    vp_meters = None
    if len(data) > 1 and isinstance(data[1], list) and len(data[1]) > 0 and isinstance(data[1][0], list):
        if len(data[1][0]) > 0 and isinstance(data[1][0][0], (int, float)):
            vp_meters = float(data[1][0][0])

    # Feature class & span
    b64_str = p[209] if len(p) > 209 and isinstance(p[209], str) else None
    feature_class, span_deg = _classify_feature(b64_str, vp_meters, address)

    # Country resolution
    iso_country = str(p[243]).strip().upper() if len(p) > 243 and p[243] else None
    _, alias_map = load_countries()

    if iso_country and iso_country.lower() in alias_map:
        country_code = iso_country
        _, country = alias_map[iso_country.lower()]
    else:
        addr_info = parse_address(address)
        country = addr_info.get("country")
        country_code = addr_info.get("country_code")
        if iso_country:
            country_code = iso_country

    est = estimate_scrape_complexity(span_deg)
    return {
        "label": f"{name}, {address}" if name != address and not address.startswith(name) else address,
        "lat": lat,
        "lng": lng,
        "span_deg": span_deg,
        "country": country,
        "country_code": country_code,
        "confidence": 0.95,
        "resolved_from": "search_rpc",
        "feature_class": feature_class,
        **est
    }

async def _resolve_via_place_fallback(
    query: str,
    http_client: HttpClient
) -> Optional[Dict[str, Any]]:
    """Method 2: Fallback to https://www.google.com/maps/place/<name>."""
    encoded = urllib.parse.quote(query)
    url = f"https://www.google.com/maps/place/{encoded}"
    try:
        resp = await http_client.get(url)
    except Exception:
        return None

    # First check URL redirect for coordinates /@lat,lng,...
    url_str = str(resp.url)
    m = re.search(r'/@([+-]?\d+\.\d+),([+-]?\d+\.\d+)', url_str)
    if m:
        lat = float(m.group(1))
        lng = float(m.group(2))
        spans = load_geo_spans()
        span_deg = spans.get("city", {}).get("span_deg", 0.10)
        addr_info = parse_address(query)
        est = estimate_scrape_complexity(span_deg)
        return {
            "label": query,
            "lat": lat,
            "lng": lng,
            "span_deg": span_deg,
            "country": addr_info.get("country"),
            "country_code": addr_info.get("country_code"),
            "confidence": 0.85,
            "resolved_from": "html_fallback_url",
            "feature_class": "city",
            **est
        }

    # Extract state
    state = extract_app_initialization_state(resp.text)
    if not state or not isinstance(state, list):
        return None

    # State elements search
    for item in state:
        if isinstance(item, list) and len(item) > 2 and isinstance(item[2], list):
            # Check for [[span, lng, lat]]
            sub = item[2]
            if len(sub) > 0 and isinstance(sub[0], list) and len(sub[0]) >= 3:
                try:
                    lng = float(sub[0][1])
                    lat = float(sub[0][2])
                    if -90 <= lat <= 90 and -180 <= lng <= 180:
                        spans = load_geo_spans()
                        span_deg = spans.get("city", {}).get("span_deg", 0.10)
                        addr_info = parse_address(query)
                        est = estimate_scrape_complexity(span_deg)
                        return {
                            "label": query,
                            "lat": lat,
                            "lng": lng,
                            "span_deg": span_deg,
                            "country": addr_info.get("country"),
                            "country_code": addr_info.get("country_code"),
                            "confidence": 0.80,
                            "resolved_from": "html_fallback_state",
                            "feature_class": "city",
                            **est
                        }
                except (ValueError, TypeError, IndexError):
                    pass

    return None

async def resolve_location(
    location_input: str,
    db: Optional[Database] = None,
    http_client: Optional[HttpClient] = None,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """
    Main location resolution entrypoint.
    Accepts:
      a) Free text place name ("Chicago", "Lyon, France", "Manhattan", "Shibuya")
      b) Coordinates + radius ("40.7128,-74.0060@5km")
      c) Explicit bbox ("40.70,-74.02,0.03")

    Returns:
      {
        label, lat, lng, span_deg, country, country_code,
        resolved_from, confidence, feature_class,
        estimated_tiles, estimated_requests, estimated_seconds
      }
    Raises ValueError if location cannot be resolved. Never guesses.
    """
    cleaned = location_input.strip()
    if not cleaned:
        raise ValueError("Location string cannot be empty.")

    # 1. Check coordinates or explicit bbox
    parsed_coords = _parse_coordinates_or_bbox(cleaned)
    if parsed_coords:
        if db:
            db.set_geo_cache({**parsed_coords, "query": cleaned})
        return parsed_coords

    # 2. Check local database geo_cache
    if db and not force_refresh:
        cached = db.get_geo_cache(cleaned)
        if cached:
            est = estimate_scrape_complexity(cached["span_deg"])
            return {
                "label": cached["label"],
                "lat": cached["lat"],
                "lng": cached["lng"],
                "span_deg": cached["span_deg"],
                "country": cached.get("country"),
                "country_code": cached.get("country_code"),
                "confidence": cached.get("confidence", 1.0),
                "resolved_from": "cache",
                "feature_class": cached.get("feature_class", "city"),
                **est
            }

    # 3. Network resolution using HttpClient
    owns_client = False
    client = http_client
    if client is None:
        cfg = load_config()
        client = HttpClient(
            user_agent=cfg.user_agent,
            accept_language=cfg.accept_language,
            hl="en",
            gl="us",
            concurrency=2,
            per_host_rate=3.0,
            max_retries=3
        )
        owns_client = True

    try:
        # Fallback 1: Search RPC
        res = await _resolve_via_search_rpc(cleaned, client)
        if not res:
            # Fallback 2: HTML /place/ endpoint
            res = await _resolve_via_place_fallback(cleaned, client)

        if not res:
            raise ValueError(
                f"Could not resolve location '{cleaned}'. Please check spelling or supply explicit coordinates (e.g. 'lat,lng@5km')."
            )

        # Cache resolution in DB
        if db:
            db.set_geo_cache({**res, "query": cleaned})

        return res
    finally:
        if owns_client and client:
            await client.close()
