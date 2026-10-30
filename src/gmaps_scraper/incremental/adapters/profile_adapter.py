import html
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import httpx

from .base import BaseSourceAdapter
from ..models import CandidateFinding, SourceCheckResult, SourceType
from ...website.ssrf import is_safe_url, normalize_url


class ProfessionalProfileAdapter(BaseSourceAdapter):
    """
    Priority 4 Adapter:
    Public professional profiles and permitted open professional sources.
    Strictly adheres to permitted access:
    - Does NOT bypass authentication, anti-bot protections, or login gates.
    - Inspects public OpenGraph metadata, JSON-LD, and verified public profile URLs.
    - Supports manually supplied profile URLs via business metadata.
    """

    def __init__(
        self,
        timeout_seconds: float = 8.0,
        check_dns: bool = True
    ):
        super().__init__(
            source_type=SourceType.PROFESSIONAL_PROFILE.value,
            priority=4,
            name="Permitted Professional Profiles",
            timeout_seconds=timeout_seconds,
            check_dns=check_dns
        )

    def can_handle(self, business: Dict[str, Any]) -> bool:
        # Checks if business has manual professional profile URLs or candidates in metadata
        meta = business.get("raw_metadata") or {}
        has_manual = bool(business.get("manual_profile_url") or meta.get("manual_profile_url"))
        has_linkedin = bool(business.get("linkedin_url") or meta.get("linkedin_url"))
        return has_manual or has_linkedin

    async def fetch_and_extract(
        self,
        business: Dict[str, Any],
        http_client: httpx.AsyncClient,
        **kwargs: Any
    ) -> Tuple[List[CandidateFinding], SourceCheckResult]:
        cid = business["cid"]
        company_name = business.get("name", "")
        meta = business.get("raw_metadata") or {}
        profile_url = business.get("manual_profile_url") or meta.get("manual_profile_url") or \
                      business.get("linkedin_url") or meta.get("linkedin_url")
        timestamp = datetime.now(timezone.utc).isoformat()

        if not profile_url:
            return [], SourceCheckResult(
                source_url="",
                source_type=self.source_type,
                source_title=self.name,
                status="no_data",
                error_message="No permitted professional profile URL supplied",
                findings_count=0,
                retrieved_at=timestamp
            )

        safe, err = self.validate_url_safe(profile_url)
        if not safe:
            return [], SourceCheckResult(
                source_url=profile_url,
                source_type=self.source_type,
                source_title=self.name,
                status="blocked",
                error_message=f"SSRF safety check failed: {err}",
                findings_count=0,
                retrieved_at=timestamp
            )

        try:
            resp = await http_client.get(
                profile_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ProfessionalProfile/1.0"},
                timeout=self.timeout
            )
        except Exception as e:
            return [], SourceCheckResult(
                source_url=profile_url,
                source_type=self.source_type,
                source_title=self.name,
                status="failed",
                error_message=f"Network request to professional profile failed: {str(e)}",
                findings_count=0,
                retrieved_at=timestamp
            )

        # Detect access control / login gate (HTTP 403, 429, 999 or redirect to login)
        if resp.status_code in (403, 429, 999) or "/login" in str(resp.url).lower() or "/authwall" in str(resp.url).lower():
            # Gracefully report access control without attempting unauthorized bypass
            return [], SourceCheckResult(
                source_url=profile_url,
                source_type=self.source_type,
                source_title=self.name,
                status="blocked",
                error_message="Access restricted by platform login wall; automated bypass prohibited by policy",
                findings_count=0,
                retrieved_at=timestamp
            )

        if resp.status_code >= 400:
            return [], SourceCheckResult(
                source_url=profile_url,
                source_type=self.source_type,
                source_title=self.name,
                status="failed",
                error_message=f"Profile URL returned HTTP {resp.status_code}",
                findings_count=0,
                retrieved_at=timestamp
            )

        findings: List[CandidateFinding] = []
        html_text = resp.text

        # Extract OpenGraph title (e.g. "Jane Doe - Chief Executive Officer - Acme Corp | LinkedIn")
        og_title_match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']*)["\']', html_text, re.IGNORECASE)
        if og_title_match:
            og_title = html.unescape(og_title_match.group(1)).strip()
            parts = [p.strip() for p in re.split(r'[-–—|]', og_title) if p.strip()]
            if parts and len(parts) >= 2:
                person_name = parts[0].title()
                job_title = parts[1].title()
                findings.append(CandidateFinding(
                    cid=cid,
                    company_name=company_name,
                    place_id=business.get("place_id"),
                    entity_type="leader",
                    person_name=person_name,
                    job_title=job_title,
                    field_name="leader",
                    field_value=f"{person_name} - {job_title}",
                    source_url=profile_url,
                    source_type=self.source_type,
                    source_title=og_title,
                    confidence=0.92
                ))
                findings.append(CandidateFinding(
                    cid=cid,
                    company_name=company_name,
                    place_id=business.get("place_id"),
                    entity_type="leader",
                    person_name=person_name,
                    job_title=job_title,
                    field_name="profile_url",
                    field_value=profile_url,
                    source_url=profile_url,
                    source_type=self.source_type,
                    source_title=og_title,
                    confidence=0.95
                ))

        status = "success" if findings else "no_data"
        return findings, SourceCheckResult(
            source_url=profile_url,
            source_type=self.source_type,
            source_title=self.name,
            status=status,
            error_message=None if status == "success" else "Profile page retrieved but no structured metadata found",
            findings_count=len(findings),
            retrieved_at=timestamp
        )
