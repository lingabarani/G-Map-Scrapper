import asyncio
import json
import urllib.parse
from typing import Any, Dict, Optional, Tuple
import httpx

from ..parser_state import strip_google_xssi_prefix
from ..search import build_search_pb, parse_search_rpc_response

class WebsiteDiscoverer:
    """Discovers candidate official website URLs using existing project Google Maps RPC facilities."""

    def __init__(self, user_agent: Optional[str] = None):
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )

    async def discover_candidate(
        self,
        business: Dict[str, Any],
        http_client: Optional[httpx.AsyncClient] = None
    ) -> Tuple[Optional[str], str]:
        """
        Returns (candidate_url, discovery_method).
        Rule 1: If an existing website is present, reuse it.
        Rule 2: If missing, attempt discovery using Google Maps place search RPC with business name and address.
        """
        existing_url = business.get("website")
        if existing_url and str(existing_url).strip():
            return str(existing_url).strip(), "existing"

        # Candidate missing from local place record -> query Google Maps search RPC
        name = business.get("name")
        address = business.get("address") or business.get("city") or ""
        country_code = (business.get("country_code") or "us").lower()
        lat = business.get("latitude") or 0.0
        lng = business.get("longitude") or 0.0

        if not name:
            return None, "missing_name"

        query_str = f"{name} {address}".strip()
        pb = build_search_pb(lat, lng, span_meters=20000, num_results=5)
        url = f"https://www.google.com/search?tbm=map&authuser=0&hl=en&gl={country_code}&pb={pb}&q={urllib.parse.quote(query_str)}"

        client_provided = http_client is not None
        client = http_client or httpx.AsyncClient(
            headers={"User-Agent": self.user_agent, "Accept": "*/*"},
            timeout=10.0,
            follow_redirects=True
        )

        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                clean = strip_google_xssi_prefix(resp.text)
                try:
                    data = json.loads(clean)
                    if isinstance(data, list) and len(data) > 0 and len(data[0]) > 1:
                        items = data[0][1]
                        if isinstance(items, list):
                            for entry in items:
                                p = None
                                if isinstance(entry, list) and len(entry) > 14 and isinstance(entry[14], list):
                                    p = entry[14]
                                elif isinstance(entry, list) and len(entry) > 0 and isinstance(entry[0], list):
                                    p = entry[0]

                                if p and len(p) > 7 and isinstance(p[7], list) and len(p[7]) > 0 and p[7][0]:
                                    found_url = str(p[7][0]).strip()
                                    if found_url.startswith("http://") or found_url.startswith("https://"):
                                        return found_url, "gmaps_search_rpc"
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if not client_provided:
                await client.aclose()

        return None, "gmaps_rpc_empty"
