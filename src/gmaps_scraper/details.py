import json
from typing import Any, Dict, List, Optional
from .models import PlaceHoursModel, PlaceAttributeModel, PopularTimeModel, PhotoModel
from .parser_state import strip_google_xssi_prefix

class MissingAttributesPathError(KeyError):
    """Raised when attributes path p[100][1] is absent in place details."""
    pass

def assert_attributes_path(p: list) -> list:
    """Explicit assertion checking that p[100][1] path exists and returning attributes groups."""
    if not isinstance(p, list) or len(p) <= 100 or not isinstance(p[100], list) or len(p[100]) <= 1 or not isinstance(p[100][1], list):
        raise MissingAttributesPathError("Expected attributes path p[100][1] is absent from place entity.")
    return p[100][1]

def parse_place_details_response(raw_text: str, cid: str) -> Dict[str, Any]:
    """Parses place details RPC response into structured dictionaries."""
    clean = strip_google_xssi_prefix(raw_text)
    try:
        data = json.loads(clean)
    except Exception:
        return {}

    if not isinstance(data, list) or len(data) == 0:
        return {}

    try:
        p = data[0][1][1][14]
    except (IndexError, TypeError):
        return {}

    if not isinstance(p, list):
        return {}

    details: Dict[str, Any] = {
        "cid": cid,
        "hours": [],
        "attributes": [],
        "popular_times": [],
        "photos": []
    }

    # Timezone
    if len(p) > 30 and p[30]:
        details["timezone"] = str(p[30])

    # Claimed status
    if len(p) > 67 and p[67] is not None:
        details["claimed_status"] = int(p[67])

    # Photo thumbnail
    if len(p) > 157 and p[157]:
        details["photo_thumbnail"] = str(p[157])
        details["photos"].append({
            "cid": cid,
            "photo_url": str(p[157]),
            "attribution": "Thumbnail",
        })

    # Rating & review count updates
    if len(p) > 4 and isinstance(p[4], list):
        if len(p[4]) > 7 and p[4][7] is not None:
            details["rating"] = float(p[4][7])
        if len(p[4]) > 8 and p[4][8] is not None:
            details["review_count"] = int(p[4][8])

    # Hours at p[203][0]
    if len(p) > 203 and isinstance(p[203], list) and len(p[203]) > 0 and isinstance(p[203][0], list):
        for h_entry in p[203][0]:
            if isinstance(h_entry, list) and len(h_entry) > 3:
                day_name = str(h_entry[0])
                day_idx = int(h_entry[1]) if h_entry[1] is not None else 0
                formatted_text = ""
                is_closed = False
                if isinstance(h_entry[3], list) and len(h_entry[3]) > 0 and isinstance(h_entry[3][0], list):
                    formatted_text = str(h_entry[3][0][0]) if h_entry[3][0] else ""
                    if "closed" in formatted_text.lower():
                        is_closed = True

                details["hours"].append({
                    "day": day_idx,
                    "day_name": day_name,
                    "formatted_text": formatted_text,
                    "is_closed": is_closed
                })

    # Attributes at p[100][1]
    if len(p) > 100 and isinstance(p[100], list) and len(p[100]) > 1 and isinstance(p[100][1], list):
        for group in p[100][1]:
            if isinstance(group, list) and len(group) > 2:
                group_id = str(group[0])
                group_name = str(group[1])
                items_list = group[2] if isinstance(group[2], list) else []
                for item in items_list:
                    if isinstance(item, list) and len(item) > 1:
                        attr_id = str(item[0])
                        attr_name = str(item[1])
                        is_enabled = True
                        display_val = None
                        if len(item) > 2 and isinstance(item[2], list) and len(item[2]) > 1:
                            sub = item[2][1]
                            if isinstance(sub, list) and len(sub) > 0 and isinstance(sub[0], list):
                                is_enabled = bool(sub[0][0]) if len(sub[0]) > 0 else True
                                display_val = str(sub[0][1]) if len(sub[0]) > 1 else None

                        details["attributes"].append({
                            "group_id": group_id,
                            "group_name": group_name,
                            "attribute_id": attr_id,
                            "attribute_name": attr_name,
                            "is_enabled": is_enabled,
                            "display_value": display_val or attr_name
                        })

    return details
