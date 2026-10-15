import json
import re
from typing import Any, Optional

class MissingHtmlFallbackStateError(KeyError):
    """Raised when HTML fallback state path state[5][3][2] is absent."""
    pass

def assert_html_fallback_state(state: Any) -> list:
    """Explicit assertion checking that state[5][3][2] exists in initialization state."""
    if (
        not isinstance(state, list)
        or len(state) <= 5
        or not isinstance(state[5], list)
        or len(state[5]) <= 3
        or not isinstance(state[5][3], list)
        or len(state[5][3]) <= 2
        or not isinstance(state[5][3][2], list)
    ):
        raise MissingHtmlFallbackStateError("Expected HTML fallback state path state[5][3][2] is absent from initialization state.")
    return state[5][3][2]

def extract_app_initialization_state(html: str) -> Optional[Any]:
    """
    Extracts window.APP_INITIALIZATION_STATE from Google Maps HTML page
    using a balanced-bracket scanner accounting for escaped characters and strings.
    """
    pattern = r'window\.APP_INITIALIZATION_STATE\s*=\s*'
    m = re.search(pattern, html)
    if not m:
        m = re.search(r'APP_INITIALIZATION_STATE\s*=\s*', html)
    if not m:
        return None

    start = m.end()
    bracket_count = 0
    in_quote = False
    escape = False
    quote_char = ''

    for i in range(start, len(html)):
        c = html[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if in_quote:
            if c == quote_char:
                in_quote = False
            continue
        if c in ('"', "'"):
            in_quote = True
            quote_char = c
            continue
        if c in ('[', '{'):
            bracket_count += 1
        elif c in (']', '}'):
            bracket_count -= 1
            if bracket_count == 0:
                raw_json = html[start:i+1]
                try:
                    return json.loads(raw_json)
                except Exception:
                    return None
    return None

def strip_google_xssi_prefix(raw_text: str) -> str:
    """Strips the standard )]}'\\n anti-XSSI security prefix."""
    text = raw_text.strip()
    if text.startswith(")]}'"):
        text = text[4:].strip()
    return text
