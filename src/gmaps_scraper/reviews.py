import json
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
from .models import ReviewModel
from .parser_state import strip_google_xssi_prefix

class MissingReviewsRootPathError(KeyError):
    """Raised when reviews root container path data[1][10] is absent."""
    pass

def assert_reviews_root_path(data: list) -> list:
    """Explicit assertion checking that data[1][10] reviews root path exists."""
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list) or len(data[1]) <= 10 or not isinstance(data[1][10], list):
        raise MissingReviewsRootPathError("Expected reviews root path data[1][10] is absent from reviews response.")
    return data[1][10]

BOQ_ENDPOINT = "https://www.google.com/httpservice/web/PrivateLocalSearchUiDataService/GetLocalBoqProxy"

def build_boq_reviews_url(ftid: str, page_size: int = 10, sort_code: int = 1, pagination_token: Optional[str] = None) -> str:
    """Constructs the GetLocalBoqProxy URL for a place ftid."""
    if not pagination_token:
        reqpld = [
            None,
            [
                None, None, None, None, None, None, None, None, None,
                [None, sort_code, None, None, None, None, None, None, None, page_size, None, [ftid]],
            ],
        ]
    else:
        reqpld = [
            None,
            [
                None, None, None, None, None, None, None, None, None,
                [None, sort_code, None, None, None, None, None, None, None, None, None, [ftid], None, None, None, None, None, None, None, pagination_token],
            ],
        ]
    payload_str = json.dumps(reqpld)
    return f"{BOQ_ENDPOINT}?msc=gwsrpc&reqpld={urllib.parse.quote(payload_str)}"

def parse_reviews_response(raw_text: str, cid: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Parses reviews response according to FIELD_MAP.md.
    Returns (list of review dictionaries, next_pagination_token).
    """
    clean = strip_google_xssi_prefix(raw_text)
    try:
        data = json.loads(clean)
    except Exception:
        return [], None

    if not isinstance(data, list) or len(data) < 2:
        return [], None

    try:
        container = data[1][10]
    except (IndexError, TypeError):
        return [], None

    if not isinstance(container, list) or len(container) < 3:
        return [], None

    reviews_list = container[2] if isinstance(container[2], list) else []
    token = container[6] if len(container) > 6 and isinstance(container[6], str) else None

    parsed_reviews: List[Dict[str, Any]] = []

    for rev in reviews_list:
        if not isinstance(rev, list) or len(rev) < 5:
            continue

        try:
            review_id = str(rev[5]) if len(rev) > 5 and rev[5] else None
            if not review_id:
                continue

            rating = int(rev[1]) if len(rev) > 1 and rev[1] is not None else 5
            rel_date = str(rev[2][0]) if len(rev) > 2 and isinstance(rev[2], list) and len(rev[2]) > 0 and rev[2][0] else None
            timestamp_ms = int(rev[2][2]) if len(rev) > 2 and isinstance(rev[2], list) and len(rev[2]) > 2 and rev[2][2] else None

            author_name = "Anonymous"
            author_avatar = None
            author_profile = None
            if len(rev) > 3 and isinstance(rev[3], list):
                if len(rev[3]) > 0 and rev[3][0]:
                    author_name = str(rev[3][0])
                if len(rev[3]) > 1 and rev[3][1]:
                    author_avatar = str(rev[3][1])
                if len(rev[3]) > 2 and rev[3][2]:
                    author_profile = str(rev[3][2])

            owner_resp_date = None
            owner_resp_text = None
            if len(rev) > 4 and isinstance(rev[4], list):
                if len(rev[4]) > 1 and rev[4][1]:
                    owner_resp_date = str(rev[4][1])
                if len(rev[4]) > 2 and rev[4][2]:
                    owner_resp_text = str(rev[4][2])

            text = None
            if len(rev) > 27 and rev[27]:
                text = str(rev[27])
            elif len(rev) > 28 and rev[28]:
                text = str(rev[28])

            language = str(rev[26]) if len(rev) > 26 and rev[26] else "en"
            reaction_count = int(rev[34]) if len(rev) > 34 and rev[34] is not None else 0

            photos: List[str] = []
            if len(rev) > 14 and isinstance(rev[14], list):
                for p_item in rev[14]:
                    if isinstance(p_item, list) and len(p_item) > 0 and p_item[0]:
                        photos.append(str(p_item[0]))

            parsed_reviews.append({
                "review_id": review_id,
                "cid": cid,
                "author_name": author_name,
                "author_id": author_profile.split("/contrib/")[-1].split("/")[0] if author_profile and "/contrib/" in author_profile else None,
                "author_avatar_url": author_avatar,
                "author_profile_url": author_profile,
                "rating": rating,
                "relative_date": rel_date,
                "timestamp_ms": timestamp_ms,
                "text": text,
                "language": language,
                "owner_response_text": owner_resp_text,
                "owner_response_date": owner_resp_date,
                "reaction_count": reaction_count,
                "photos_json": json.dumps(photos) if photos else "[]"
            })
        except Exception:
            continue

    return parsed_reviews, token
