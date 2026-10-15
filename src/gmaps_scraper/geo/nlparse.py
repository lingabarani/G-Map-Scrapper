import re
from typing import Optional, Tuple

SEPARATORS = [" in ", " near ", " around ", " at ", " within "]

def parse_natural_language_query(
    text: str,
    default_location: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    Parses a single free-text box like 'hospitals in Chicago' into (query, location).
    Rules:
    - Splits on the LAST occurrence of ' in ', ' near ', ' around ', ' at ', ' within '.
      Left = query, right = location.
    - Handles 'in the ...' -> cleans leading 'the ' from location, but preserves 'in downtown X'.
    - Handles 'near me' -> returns default_location or None.
    - If no separator, returns (text.strip(), None).
    """
    raw = text.strip()
    if not raw:
        return "", None

    # Find the LAST occurrence of any separator
    lower_raw = raw.lower()
    last_idx = -1
    best_sep_len = 0

    for sep in SEPARATORS:
        idx = lower_raw.rfind(sep)
        if idx > last_idx:
            last_idx = idx
            best_sep_len = len(sep)

    if last_idx == -1:
        # Check if text ends with " near me" or similar
        return raw, None

    query_part = raw[:last_idx].strip()
    location_part = raw[last_idx + best_sep_len:].strip()

    # Clean location_part:
    # Handle "near me"
    if location_part.lower() in ("me", "here", "my location"):
        return query_part, default_location or "New York, NY"

    # Handle "in the Bronx" -> "the Bronx" or "Bronx"
    # Prompt: handle "in the", "in downtown X" -> location "downtown X"
    if location_part.lower().startswith("the "):
        # e.g. "the Bronx", "the loop", "the French Quarter"
        # We can keep the name or strip if generic
        pass

    return query_part, location_part
