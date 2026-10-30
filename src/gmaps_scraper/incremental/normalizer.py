import html
import re
import urllib.parse
from typing import Optional, Set

HONORIFIC_PREFIXES: Set[str] = {
    "dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.",
    "prof", "prof.", "rev", "rev.", "hon", "hon."
}

NAME_SUFFIXES: Set[str] = {
    "jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "md", "phd", "esq", "esq.", "do", "dds", "dvm", "cpa", "faac"
}

TITLE_CANONICAL_MAP = {
    "chief executive officer": "ceo",
    "chief operating officer": "coo",
    "chief financial officer": "cfo",
    "chief technology officer": "cto",
    "chief information officer": "cio",
    "chief medical officer": "cmo",
    "chief marketing officer": "cmo",
    "vice president": "vp",
    "executive vice president": "evp",
    "senior vice president": "svp",
    "co-founder": "founder",
    "cofounder": "founder",
    "managing director": "director",
    "executive director": "director",
}

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "source", "mc_cid", "mc_eid"
}


def normalize_email(email: Optional[str]) -> Optional[str]:
    """Trims whitespace and lowercases email address."""
    if not email:
        return None
    cleaned = email.strip().lower()
    if "@" not in cleaned or "." not in cleaned:
        return None
    # Filter common asset names ending in image extensions
    if any(cleaned.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")):
        return None
    return cleaned


def normalize_phone(phone_str: Optional[str], country_code: str = "US") -> Optional[str]:
    """
    Normalizes telephone numbers.
    For US: formats as standard '(XXX) XXX-XXXX' and filters invalid sequences.
    """
    if not phone_str:
        return None
    clean = re.sub(r'[^\d+]', '', phone_str.strip())
    digits = re.sub(r'\D', '', clean)

    if clean.startswith("+1") and len(digits) == 11:
        digits = digits[1:]
    elif clean.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    elif len(digits) == 10:
        pass
    elif clean.startswith("+52") and len(digits) >= 12:
        return f"+{digits}"
    else:
        # If not standard 10-digit, return sanitized digits if at least 7 digits
        if 7 <= len(digits) <= 15:
            return clean
        return None

    if len(digits) != 10:
        return None

    # Filter invalid/dummy repetitions (e.g., 0000000000, 1111111111)
    if len(set(digits)) <= 2:
        return None

    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"


def normalize_phone_digits(phone_str: Optional[str]) -> Optional[str]:
    """Extracts raw comparable digits only."""
    norm = normalize_phone(phone_str)
    if not norm:
        return None
    return re.sub(r'\D', '', norm)


def normalize_name(name: Optional[str]) -> Optional[str]:
    """
    Normalizes person name: strips honorifics and suffixes, collapses whitespace,
    and returns title-cased clean name.
    """
    if not name:
        return None
    text = html.unescape(name).strip()
    text = re.sub(r'\s+', ' ', text)

    # Remove quotes
    text = text.replace('"', '').replace("'", "")

    tokens = text.split()
    if not tokens:
        return None

    # Remove prefix
    if tokens[0].rstrip(',').lower() in HONORIFIC_PREFIXES and len(tokens) > 1:
        tokens = tokens[1:]

    # Remove suffix
    if tokens and tokens[-1].rstrip(',').lower() in NAME_SUFFIXES and len(tokens) > 1:
        tokens = tokens[:-1]

    # Clean any trailing comma left from suffix removal (e.g., "Smith,")
    clean = " ".join(t.rstrip(',') for t in tokens).strip()
    clean = " ".join(clean.split()).title()
    return clean if len(clean) >= 2 else None


def normalize_name_key(name: Optional[str]) -> Optional[str]:
    """Produces canonical lowercase key for name comparison."""
    norm = normalize_name(name)
    if not norm:
        return None
    return re.sub(r'[^a-z0-9]', '', norm.lower())


def normalize_title(title: Optional[str]) -> Optional[str]:
    """Normalizes job title formatting while preserving role distinctions."""
    if not title:
        return None
    text = html.unescape(title).strip()
    text = re.sub(r'\s+', ' ', text)
    # Strip leading/trailing punctuation (e.g. "- CEO", ", Founder")
    text = re.sub(r'^[\s\-,–—|]+|[\s\-,–—|]+$', '', text)
    return text.title() if text else None


def canonical_title_key(title: Optional[str]) -> Optional[str]:
    """Maps common executive roles to a canonical short key for comparison."""
    norm = normalize_title(title)
    if not norm:
        return None
    t_lower = norm.lower()
    for long_form, short_form in TITLE_CANONICAL_MAP.items():
        if t_lower == long_form or t_lower.startswith(long_form + " ") or t_lower.endswith(" " + long_form):
            t_lower = short_form
            break
    # Remove non-alphanumeric
    return re.sub(r'[^a-z0-9]', '', t_lower)


def normalize_url(url: Optional[str]) -> Optional[str]:
    """
    Normalizes URL: scheme, lowercase host, strip trailing slash,
    remove tracking query params.
    """
    if not url:
        return None
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u

    try:
        parsed = urllib.parse.urlsplit(u)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        # Strip default ports
        if netloc.endswith(":80"):
            netloc = netloc[:-3]
        elif netloc.endswith(":443"):
            netloc = netloc[:-4]

        # Strip tracking query params
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        filtered_queries = [(k, v) for k, v in query_pairs if k.lower() not in TRACKING_PARAMS]
        new_query = urllib.parse.urlencode(filtered_queries)

        path = parsed.path
        if path != "/" and path.endswith("/"):
            path = path[:-1]
        if not path:
            path = ""

        clean_url = urllib.parse.urlunsplit((scheme, netloc, path, new_query, ""))
        return clean_url
    except Exception:
        return u.rstrip("/")


def normalize_text(text: Optional[str]) -> str:
    """Strips HTML, unescapes entities, and collapses whitespace."""
    if not text:
        return ""
    no_html = re.sub(r'<[^>]+>', ' ', text)
    unescaped = html.unescape(no_html)
    return re.sub(r'\s+', ' ', unescaped).strip()
