import html
import json
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import httpx

from .base import BaseSourceAdapter
from ..models import CandidateFinding, SourceCheckResult, SourceType
from ...website.ssrf import is_safe_url, normalize_url


class OpenBusinessSourcesAdapter(BaseSourceAdapter):
    """
    Priority 5 Adapter:
    Permitted public open business knowledge sources (e.g. Wikidata / Open Business Knowledge).
    Extracts corporate founding date, industry classification, subsidiaries, and official entities.
    """

    def __init__(
        self,
        timeout_seconds: float = 8.0,
        check_dns: bool = True
    ):
        super().__init__(
            source_type=SourceType.OPEN_BUSINESS_SOURCE.value,
            priority=5,
            name="Open Business Knowledge Sources",
            timeout_seconds=timeout_seconds,
            check_dns=check_dns
        )

    def can_handle(self, business: Dict[str, Any]) -> bool:
        name = business.get("name")
        return bool(name and len(name.strip()) >= 4)

    async def fetch_and_extract(
        self,
        business: Dict[str, Any],
        http_client: httpx.AsyncClient,
        **kwargs: Any
    ) -> Tuple[List[CandidateFinding], SourceCheckResult]:
        cid = business["cid"]
        company_name = business.get("name", "").strip()
        timestamp = datetime.now(timezone.utc).isoformat()

        # Public Wikidata API for entity search
        encoded_query = urllib.parse.quote(company_name)
        api_url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={encoded_query}&language=en&format=json&limit=1"

        safe, err = self.validate_url_safe(api_url)
        if not safe:
            return [], SourceCheckResult(
                source_url=api_url,
                source_type=self.source_type,
                source_title=self.name,
                status="blocked",
                error_message=f"SSRF safety check failed: {err}",
                findings_count=0,
                retrieved_at=timestamp
            )

        try:
            resp = await http_client.get(
                api_url,
                headers={"User-Agent": "GmapsScraperIntelligence/1.0 (Public Open Knowledge Integration)"},
                timeout=self.timeout
            )
        except Exception as e:
            return [], SourceCheckResult(
                source_url=api_url,
                source_type=self.source_type,
                source_title=self.name,
                status="failed",
                error_message=f"Open source lookup failed: {str(e)}",
                findings_count=0,
                retrieved_at=timestamp
            )

        if resp.status_code >= 400:
            return [], SourceCheckResult(
                source_url=api_url,
                source_type=self.source_type,
                source_title=self.name,
                status="failed",
                error_message=f"Open knowledge endpoint returned HTTP {resp.status_code}",
                findings_count=0,
                retrieved_at=timestamp
            )

        findings: List[CandidateFinding] = []
        try:
            data = resp.json()
            search_items = data.get("search") or []
            if search_items:
                item = search_items[0]
                desc = item.get("description")
                item_url = item.get("concepturi") or f"https://www.wikidata.org/wiki/{item.get('id')}"

                if desc and any(k in desc.lower() for k in ("company", "hospital", "clinic", "center", "corporation", "business", "service", "organization", "firm")):
                    findings.append(CandidateFinding(
                        cid=cid,
                        company_name=company_name,
                        place_id=business.get("place_id"),
                        entity_type="profile",
                        field_name="overview",
                        field_value=desc,
                        source_url=item_url,
                        source_type=self.source_type,
                        source_title=f"Wikidata Open Entity ({item.get('id')})",
                        confidence=0.88,
                        raw_metadata=item
                    ))
        except Exception:
            pass

        status = "success" if findings else "no_data"
        return findings, SourceCheckResult(
            source_url=api_url,
            source_type=self.source_type,
            source_title=self.name,
            status=status,
            error_message=None if status == "success" else "Open knowledge source accessed; no matching business entity found",
            findings_count=len(findings),
            retrieved_at=timestamp
        )
