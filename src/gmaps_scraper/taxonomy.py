import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_VERTICALS_CACHE: Optional[Dict[str, Any]] = None
_COUNTRIES_CACHE: Optional[Dict[str, Any]] = None
_ALIAS_TO_COUNTRY: Optional[Dict[str, Tuple[str, str]]] = None

def get_config_dir() -> Path:
    return _CONFIG_DIR

def load_verticals(config_path: Optional[Path] = None) -> Dict[str, Any]:
    global _VERTICALS_CACHE
    if _VERTICALS_CACHE is not None and config_path is None:
        return _VERTICALS_CACHE
    
    path = config_path or (_CONFIG_DIR / "verticals.yaml")
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if config_path is None:
        _VERTICALS_CACHE = data
    return data

def load_countries(config_path: Optional[Path] = None) -> Tuple[Dict[str, Any], Dict[str, Tuple[str, str]]]:
    global _COUNTRIES_CACHE, _ALIAS_TO_COUNTRY
    if _COUNTRIES_CACHE is not None and _ALIAS_TO_COUNTRY is not None and config_path is None:
        return _COUNTRIES_CACHE, _ALIAS_TO_COUNTRY

    path = config_path or (_CONFIG_DIR / "countries.yaml")
    if not path.exists():
        return {}, {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    alias_map: Dict[str, Tuple[str, str]] = {}
    for raw_code, info in data.items():
        if raw_code is False:
            code = "NO"
        elif raw_code is True:
            code = "YES"
        else:
            code = str(raw_code)
        name = info.get("name", code)
        code_upper = code.upper()
        # Direct code match
        alias_map[code.lower()] = (code_upper, name)
        alias_map[name.lower()] = (code_upper, name)
        for alias in info.get("aliases", []):
            alias_map[str(alias).lower().strip()] = (code_upper, name)

    if config_path is None:
        _COUNTRIES_CACHE = data
        _ALIAS_TO_COUNTRY = alias_map
    return data, alias_map

def classify_vertical(category_raw: Optional[str], verticals_cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """
    Classifies a raw category string into a normalized vertical.
    First match in the configured order wins.
    Returns (vertical_key, vertical_raw).
    """
    raw = str(category_raw).strip() if category_raw is not None else ""
    if not raw:
        return "other", ""

    cfg = verticals_cfg or load_verticals()
    raw_lower = raw.lower()

    for v_key, v_info in cfg.items():
        if v_key == "other":
            continue
        keywords = v_info.get("keywords", []) if isinstance(v_info, dict) else v_info
        for kw in keywords:
            pattern = r'\b' + re.escape(str(kw).lower()) + r'\b'
            if re.search(pattern, raw_lower):
                return v_key, raw

    return "other", raw

def parse_address(address: Optional[str]) -> Dict[str, Optional[str]]:
    """
    Parses country, country_code, region, city, postal_code from an address string.
    Rule: take the last comma-separated component, strip postal codes,
    resolve via countries.yaml. If unresolvable -> country = "unknown", country_code = "unknown".
    Never guess.
    """
    result: Dict[str, Optional[str]] = {
        "country": "unknown",
        "country_code": "unknown",
        "region": None,
        "city": None,
        "postal_code": None
    }

    if not address or not address.strip():
        return result

    raw_addr = address.strip()
    components = [c.strip() for c in raw_addr.split(",") if c.strip()]
    if not components:
        return result

    _, alias_map = load_countries()

    # Regex patterns for common postal codes
    re_us_ca_uk_zip = re.compile(
        r'\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}|\d{5}(?:-\d{4})?|[A-Z]\d[A-Z]\s*\d[A-Z]\d|\d{4,6})\b',
        re.IGNORECASE
    )

    found_postal = None
    # Check components in reverse for postal code
    for comp in reversed(components):
        m = re_us_ca_uk_zip.search(comp)
        if m:
            found_postal = m.group(1).strip()
            break

    result["postal_code"] = found_postal

    # Rule: Take the last comma-separated component, strip postal codes
    last_comp = components[-1]
    last_cleaned = re_us_ca_uk_zip.sub('', last_comp).strip()
    last_cleaned = re.sub(r'^[^\w]+|[^\w]+$', '', last_cleaned).strip()

    US_STATE_CODES = {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"
    }

    country_match = None
    # Special check: If last_cleaned is a 2-letter US state code and postal code is a 5-digit US zip,
    # it is United States (not Canada for 'CA' or India for 'IN')
    if found_postal and re.match(r'^\d{5}(?:-\d{4})?$', found_postal) and last_cleaned.upper() in US_STATE_CODES:
        country_match = ("US", "United States")
    elif last_cleaned.lower() in alias_map:
        country_match = alias_map[last_cleaned.lower()]
    elif last_comp.lower() in alias_map:
        country_match = alias_map[last_comp.lower()]
    
    country_comp_idx = len(components) - 1

    # If last component was only a postal code (e.g. "10004"), check second to last
    if not country_match and len(components) >= 2 and not last_cleaned:
        penultimate = components[-2]
        pen_cleaned = re_us_ca_uk_zip.sub('', penultimate).strip()
        pen_cleaned = re.sub(r'^[^\w]+|[^\w]+$', '', pen_cleaned).strip()
        if found_postal and re.match(r'^\d{5}(?:-\d{4})?$', found_postal) and pen_cleaned.upper() in US_STATE_CODES:
            country_match = ("US", "United States")
            country_comp_idx = len(components) - 2
        elif pen_cleaned.lower() in alias_map:
            country_match = alias_map[pen_cleaned.lower()]
            country_comp_idx = len(components) - 2

    if country_match:
        result["country_code"], result["country"] = country_match
    else:
        result["country"] = "unknown"
        result["country_code"] = "unknown"

    # If the component that matched country was a state/region alias (e.g. "NY" or "NY 10004")
    state_as_country_comp = False
    if country_match and len(last_cleaned) <= 3 and last_cleaned.isalpha():
        result["region"] = last_cleaned.upper()
        state_as_country_comp = True

    # Now extract city and region from the components preceding the country
    preceding = components[:country_comp_idx]
    if preceding:
        if state_as_country_comp:
            # Country and region were in the last component, so preceding[-1] is city
            result["city"] = preceding[-1]
        else:
            cand = preceding[-1]
            cand_no_zip = re_us_ca_uk_zip.sub('', cand).strip()
            cand_parts = [p.strip() for p in cand_no_zip.split() if p.strip()]

            if len(cand_parts) == 1 and len(cand_parts[0]) <= 3 and cand_parts[0].isalpha():
                # e.g. "NY"
                result["region"] = cand_parts[0].upper()
                if len(preceding) >= 2:
                    result["city"] = re_us_ca_uk_zip.sub('', preceding[-2]).strip()
                else:
                    result["city"] = cand_no_zip
            elif len(cand_parts) > 1 and len(cand_parts[-1]) <= 3 and cand_parts[-1].isalpha():
                # e.g. "New York NY"
                result["region"] = cand_parts[-1].upper()
                result["city"] = " ".join(cand_parts[:-1])
            else:
                result["city"] = cand_no_zip
    elif len(components) == 1 and not country_match:
        # Single unresolvable component
        pass

    return result

def enrich_place_dimensions(place: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enriches a place dict with vertical, vertical_raw, country, country_code,
    region, city, and postal_code.
    """
    category = place.get("category")
    vertical, vertical_raw = classify_vertical(category)
    place["vertical"] = vertical
    place["vertical_raw"] = vertical_raw

    address_info = parse_address(place.get("address"))
    if place.get("country_code") == "US" and address_info.get("country_code") == "CA" and address_info.get("region") in ("CA", "California"):
        address_info["country_code"] = "US"
        address_info["country"] = "United States"
        address_info["region"] = "CA"

    if address_info["country_code"] != "unknown" or not place.get("country_code"):
        place["country"] = address_info["country"]
        place["country_code"] = address_info["country_code"]
    if address_info["region"] is not None or not place.get("region"):
        place["region"] = address_info["region"]
    if address_info["city"] is not None or not place.get("city"):
        place["city"] = address_info["city"]
    if address_info["postal_code"] is not None or not place.get("postal_code"):
        place["postal_code"] = address_info["postal_code"]

    return place
