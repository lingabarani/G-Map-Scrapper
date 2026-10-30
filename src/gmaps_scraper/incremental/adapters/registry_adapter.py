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


class StateRegistryAdapter(BaseSourceAdapter):
    """
    Priority 3 Adapter:
    Public government, regulatory and professional business directories.
    Provides legally accessible corporate registry intelligence:
    - Entity ID / Corporation Number
    - Legal Entity Status (Active / Good Standing / Dissolved)
    - Legal Entity Type (Non-Profit, LLC, Corporation)
    - Registered Agent Name
    - Corporate Officers and Directors
    """

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        check_dns: bool = True
    ):
        super().__init__(
            source_type=SourceType.PUBLIC_REGISTRY.value,
            priority=3,
            name="State Public Business Registry",
            timeout_seconds=timeout_seconds,
            check_dns=check_dns
        )

    def can_handle(self, business: Dict[str, Any]) -> bool:
        # Applies to businesses with country US or MX and a valid business name
        name = business.get("name")
        country = business.get("country_code") or business.get("country")
        return bool(name and len(name.strip()) >= 3 and str(country).upper() in ("US", "MX"))

    async def fetch_and_extract(
        self,
        business: Dict[str, Any],
        http_client: httpx.AsyncClient,
        **kwargs: Any
    ) -> Tuple[List[CandidateFinding], SourceCheckResult]:
        cid = business["cid"]
        company_name = business.get("name", "").strip()
        region = (business.get("region") or "").strip().upper()
        country = (business.get("country_code") or "US").strip().upper()
        timestamp = datetime.now(timezone.utc).isoformat()

        # Build public registry search target URL based on state/region
        if region == "AK":
            # Alaska Division of Corporations, Business and Professional Licensing public search
            base_url = "https://www.commerce.alaska.gov/cbp/main/search/entities"
            query_params = {"q": company_name}
            search_url = f"{base_url}?{urllib.parse.urlencode(query_params)}"
        else:
            # Generic State Corporation Registry Directory endpoint
            base_url = f"https://mybusiness.sos.{region.lower()}.gov/search" if region else "https://corporations.sos.gov/search"
            query_params = {"name": company_name}
            search_url = f"{base_url}?{urllib.parse.urlencode(query_params)}"

        safe, err = self.validate_url_safe(search_url)
        if not safe:
            return [], SourceCheckResult(
                source_url=search_url,
                source_type=self.source_type,
                source_title=f"{region} State Corporation Registry",
                status="blocked",
                error_message=f"SSRF safety check failed: {err}",
                findings_count=0,
                retrieved_at=timestamp
            )

        try:
            # We request the public state entity search page
            resp = await http_client.get(
                search_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PublicRegistry/1.0", "Accept": "text/html,application/json"},
                timeout=self.timeout
            )
        except Exception as e:
            return [], SourceCheckResult(
                source_url=search_url,
                source_type=self.source_type,
                source_title=f"{region} State Corporation Registry",
                status="failed",
                error_message=f"Public registry lookup failed: {str(e)}",
                findings_count=0,
                retrieved_at=timestamp
            )

        if resp.status_code >= 400:
            return [], SourceCheckResult(
                source_url=search_url,
                source_type=self.source_type,
                source_title=f"{region} State Corporation Registry",
                status="failed",
                error_message=f"Public registry returned HTTP {resp.status_code}",
                findings_count=0,
                retrieved_at=timestamp
            )

        findings: List[CandidateFinding] = []
        html_text = resp.text

        # Extract structured details if matches found
        # Pattern 1: Entity Number
        ent_num_match = re.search(r'(?:Entity\s*(?:Number|ID|#)|Record\s*#)[:\s]*([A-Z0-9\-]{5,15})', html_text, re.IGNORECASE)
        if ent_num_match:
            ent_num = ent_num_match.group(1).strip()
            findings.append(CandidateFinding(
                cid=cid,
                company_name=company_name,
                place_id=business.get("place_id"),
                entity_type="registry",
                field_name="entity_number",
                field_value=ent_num,
                source_url=search_url,
                source_type=self.source_type,
                source_title=f"{region} State Corporation Registry",
                confidence=0.95
            ))

        # Pattern 2: Entity Status (Active / Good Standing)
        status_match = re.search(r'(?:Status)[:\s]*<[^>]*>([A-Za-z\s]{3,25})</', html_text, re.IGNORECASE) or \
                       re.search(r'\b(Active|Good\s+Standing|Dissolved|Revoked|Merged)\b', html_text, re.IGNORECASE)
        if status_match:
            stat_str = status_match.group(1).strip().title()
            findings.append(CandidateFinding(
                cid=cid,
                company_name=company_name,
                place_id=business.get("place_id"),
                entity_type="registry",
                field_name="corp_status",
                field_value=stat_str,
                source_url=search_url,
                source_type=self.source_type,
                source_title=f"{region} State Corporation Registry",
                confidence=0.9
            ))

        # Pattern 3: Registered Agent
        agent_match = re.search(r'(?:Registered\s+Agent|Resident\s+Agent)[:\s]*<[^>]*>([^<]{3,50})</', html_text, re.IGNORECASE) or \
                      re.search(r'Registered\s+Agent[:\s]+([A-Z][a-zA-Z.\s\'-]{3,40})', html_text, re.IGNORECASE)
        if agent_match:
            agent_name = re.sub(r'<[^>]+>', ' ', agent_match.group(1)).strip().title()
            findings.append(CandidateFinding(
                cid=cid,
                company_name=company_name,
                place_id=business.get("place_id"),
                entity_type="registry",
                person_name=agent_name,
                field_name="registered_agent",
                field_value=agent_name,
                source_url=search_url,
                source_type=self.source_type,
                source_title=f"{region} State Corporation Registry",
                confidence=0.9
            ))

        # Pattern 4: Corporate Officers from table / list
        officer_matches = re.findall(
            r'<(?:td|div|li)[^>]*class=["\'][^"\']*(?:officer|director|principal)[^"\']*["\'][^>]*>([A-Z][a-zA-Z.\s\'-]{2,40})</(?:td|div|li)>\s*<(?:td|div|li)[^>]*>([^<]*(?:President|Secretary|Treasurer|Director|CEO)[^<]*)</(?:td|div|li)>',
            html_text,
            re.IGNORECASE
        )
        for o_name, o_title in officer_matches:
            clean_name = html.unescape(o_name).strip().title()
            clean_title = html.unescape(o_title).strip().title()
            findings.append(CandidateFinding(
                cid=cid,
                company_name=company_name,
                place_id=business.get("place_id"),
                entity_type="leader",
                person_name=clean_name,
                job_title=clean_title,
                field_name="leader",
                field_value=f"{clean_name} - {clean_title}",
                source_url=search_url,
                source_type=self.source_type,
                source_title=f"{region} State Corporation Registry",
                confidence=0.92
            ))

        status = "success" if findings else "no_data"
        return findings, SourceCheckResult(
            source_url=search_url,
            source_type=self.source_type,
            source_title=f"{region} State Corporation Registry",
            status=status,
            error_message=None if status == "success" else "Registry accessed successfully; no public filing matched query",
            findings_count=len(findings),
            retrieved_at=timestamp
        )
