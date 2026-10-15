import json
from typing import Any, Dict, List, Optional, Tuple
from .models import PlaceStub
from .parser_state import strip_google_xssi_prefix, extract_app_initialization_state

def build_search_pb(lat: float, lng: float, span_meters: int = 1000, num_results: int = 20) -> str:
    """Constructs the Google Maps search Protocol Buffer parameter for a tile query."""
    return f"!4m8!1m3!1d{span_meters}!2d{lng}!3d{lat}!3m2!1i1024!2i768!4f13.1!7i{num_results}!10b1"

def parse_search_rpc_response(raw_text: str) -> List[PlaceStub]:
    """Parses place stubs from the search RPC response according to FIELD_MAP.md."""
    clean = strip_google_xssi_prefix(raw_text)
    try:
        data = json.loads(clean)
    except Exception:
        return []

    return extract_stubs_from_data(data)

def extract_stubs_from_data(data: Any) -> List[PlaceStub]:
    stubs: List[PlaceStub] = []
    if not isinstance(data, list) or len(data) == 0:
        return stubs

    try:
        results_container = data[0][1]
    except (IndexError, TypeError):
        return stubs

    if not isinstance(results_container, list):
        return stubs

    # Item 0 is metadata/query, items 1..N are place listings
    for entry in results_container[1:]:
        if not isinstance(entry, list) or len(entry) <= 14:
            continue
        p = entry[14]
        if not isinstance(p, list) or len(p) <= 11:
            continue

        try:
            name = str(p[11]) if p[11] is not None else None
            if not name:
                continue

            ftid = str(p[10]) if len(p) > 10 and p[10] is not None else ""
            if not ftid or ":" not in ftid:
                continue

            hex_cid = ftid.split(":")[1]
            cid_int = int(hex_cid, 16)
            cid = str(cid_int)

            place_id = str(p[78]) if len(p) > 78 and p[78] is not None else ""
            raw_address = str(p[18]) if len(p) > 18 and p[18] is not None else None
            street_address = str(p[39]) if len(p) > 39 and p[39] is not None else None

            # Phase 0 correction: Use p[39] for clean street address, never name-polluted
            if street_address:
                address = street_address
            elif raw_address and name and raw_address.startswith(name + ", "):
                address = raw_address[len(name) + 2:].strip()
            else:
                address = raw_address

            lat, lng = None, None
            if len(p) > 9 and isinstance(p[9], list) and len(p[9]) > 3:
                lat = float(p[9][2]) if p[9][2] is not None else None
                lng = float(p[9][3]) if p[9][3] is not None else None

            website = str(p[7][0]) if len(p) > 7 and isinstance(p[7], list) and len(p[7]) > 0 and p[7][0] is not None else None

            phone = None
            phone_e164 = None
            if len(p) > 178 and isinstance(p[178], list) and len(p[178]) > 0 and isinstance(p[178][0], list):
                phone = str(p[178][0][0]) if len(p[178][0]) > 0 and p[178][0][0] is not None else None
                phone_e164 = str(p[178][0][3]) if len(p[178][0]) > 3 and p[178][0][3] is not None else None

            category = str(p[13][0]) if len(p) > 13 and isinstance(p[13], list) and len(p[13]) > 0 and p[13][0] is not None else None

            rating = None
            review_count = 0
            if len(p) > 4 and isinstance(p[4], list):
                if len(p[4]) > 7 and p[4][7] is not None:
                    rating = float(p[4][7])
                if len(p[4]) > 8 and p[4][8] is not None:
                    review_count = int(p[4][8])

            stub = PlaceStub(
                cid=cid,
                ftid=ftid,
                place_id=place_id,
                name=name,
                address=address,
                street_address=street_address,
                latitude=lat,
                longitude=lng,
                website=website,
                phone=phone,
                phone_e164=phone_e164,
                category=category,
                rating=rating,
                review_count=review_count,
                status="pending"
            )
            stubs.append(stub)
        except Exception:
            continue

    return stubs

def parse_html_fallback(html: str) -> List[PlaceStub]:
    """Fallback extractor using window.APP_INITIALIZATION_STATE."""
    state = extract_app_initialization_state(html)
    if not state:
        return []
    # If state[3][1] contains the JSON blob
    if len(state) > 3 and isinstance(state[3], list) and len(state[3]) > 1 and isinstance(state[3][1], str):
        raw = state[3][1]
        clean = strip_google_xssi_prefix(raw)
        try:
            data = json.loads(clean)
            return extract_stubs_from_data(data)
        except Exception:
            pass
    return []
