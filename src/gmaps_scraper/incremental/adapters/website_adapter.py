import html
import json
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from .base import BaseSourceAdapter
from ..models import CandidateFinding, SourceCheckResult, SourceType
from ...website.crawler import SafeWebsiteCrawler
from ...website.extractor import WebsiteExtractor
from ...website.ssrf import is_safe_url, normalize_url

EXTENDED_PRIORITY_KEYWORDS = [
    "board", "investor", "governance", "press", "news",
    "announcement", "provider", "physician", "doctor",
    "attorney", "lawyer", "staff", "management", "partner"
]

LINKEDIN_PROFILE_PATTERN = re.compile(
    r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([a-zA-Z0-9_\-]+)',
    re.IGNORECASE
)


class CompanyWebsiteExtendedAdapter(BaseSourceAdapter):
    """
    Priority 1 & 2 Adapter:
    Safely crawls official company website pages with an extended focus on:
    - Leadership, Board of Directors, Medical/Legal Providers, Executive Staff.
    - Corporate announcements, Press releases, and Governance pages.
    - Official LinkedIn professional profile URLs linked from company pages.
    - Business communication channels and additional operating branches.
    """

    def __init__(
        self,
        max_pages: int = 6,
        timeout_seconds: float = 10.0,
        user_agent: Optional[str] = None,
        check_dns: bool = True
    ):
        super().__init__(
            source_type=SourceType.COMPANY_WEBSITE.value,
            priority=1,
            name="Official Company Website Extended",
            timeout_seconds=timeout_seconds,
            check_dns=check_dns
        )
        self.max_pages = max_pages
        self.user_agent = user_agent

    def can_handle(self, business: Dict[str, Any]) -> bool:
        url = business.get("official_website_url") or business.get("website")
        return bool(url and url.strip())

    async def fetch_and_extract(
        self,
        business: Dict[str, Any],
        http_client: httpx.AsyncClient,
        **kwargs: Any
    ) -> Tuple[List[CandidateFinding], SourceCheckResult]:
        cid = business["cid"]
        company_name = business.get("name", "")
        root_url = business.get("official_website_url") or business.get("website")
        timestamp = datetime.now(timezone.utc).isoformat()

        if not root_url:
            return [], SourceCheckResult(
                source_url="",
                source_type=self.source_type,
                source_title=self.name,
                status="failed",
                error_message="No official website URL provided",
                findings_count=0,
                retrieved_at=timestamp
            )

        safe, err = self.validate_url_safe(root_url)
        if not safe:
            return [], SourceCheckResult(
                source_url=root_url,
                source_type=self.source_type,
                source_title=self.name,
                status="blocked",
                error_message=f"SSRF safety check failed: {err}",
                findings_count=0,
                retrieved_at=timestamp
            )

        crawler = SafeWebsiteCrawler(
            max_pages=self.max_pages,
            timeout_seconds=self.timeout,
            user_agent=self.user_agent,
            check_dns=self.check_dns
        )

        try:
            crawled_pages = await crawler.crawl(root_url, http_client=http_client)
        except Exception as e:
            return [], SourceCheckResult(
                source_url=root_url,
                source_type=self.source_type,
                source_title=self.name,
                status="failed",
                error_message=f"Crawling exception: {str(e)}",
                findings_count=0,
                retrieved_at=timestamp
            )

        if not crawled_pages:
            return [], SourceCheckResult(
                source_url=root_url,
                source_type=self.source_type,
                source_title=self.name,
                status="failed",
                error_message="Could not access or retrieve HTML pages from website",
                findings_count=0,
                retrieved_at=timestamp
            )

        findings: List[CandidateFinding] = []

        # 1. Standard Extraction via WebsiteExtractor
        extracted = WebsiteExtractor.extract(business, crawled_pages)
        profile = extracted.get("profile") or {}
        leaders = extracted.get("leaders") or []
        contacts = extracted.get("contacts") or []

        # Add Leaders
        for l in leaders:
            findings.append(CandidateFinding(
                cid=cid,
                company_name=company_name,
                place_id=business.get("place_id"),
                entity_type="leader",
                person_name=l["full_name"],
                job_title=l.get("title"),
                field_name="leader",
                field_value=f"{l['full_name']} - {l.get('title')}",
                source_url=l.get("source_url") or root_url,
                source_type=SourceType.COMPANY_WEBSITE.value,
                source_title=f"{company_name} Official Website",
                confidence=l.get("confidence", 0.9),
                raw_metadata=l
            ))
            if l.get("biography"):
                findings.append(CandidateFinding(
                    cid=cid,
                    company_name=company_name,
                    place_id=business.get("place_id"),
                    entity_type="leader",
                    person_name=l["full_name"],
                    job_title=l.get("title"),
                    field_name="biography",
                    field_value=l["biography"],
                    source_url=l.get("source_url") or root_url,
                    source_type=SourceType.COMPANY_WEBSITE.value,
                    source_title=f"{company_name} Official Website",
                    confidence=0.85,
                    raw_metadata=l
                ))

        # Add Contacts
        for c in contacts:
            findings.append(CandidateFinding(
                cid=cid,
                company_name=company_name,
                place_id=business.get("place_id"),
                entity_type="contact",
                field_name=c["contact_type"],
                field_value=c["contact_value"],
                source_url=c.get("source_url") or root_url,
                source_type=SourceType.COMPANY_WEBSITE.value,
                source_title=f"{company_name} Official Website",
                confidence=0.95,
                raw_metadata=c
            ))

        # Add Profile overview, services, locations
        if profile.get("overview"):
            findings.append(CandidateFinding(
                cid=cid,
                company_name=company_name,
                place_id=business.get("place_id"),
                entity_type="profile",
                field_name="overview",
                field_value=profile["overview"],
                source_url=root_url,
                source_type=SourceType.COMPANY_WEBSITE.value,
                source_title=f"{company_name} Official Website",
                confidence=0.9
            ))

        for s in profile.get("services") or []:
            if isinstance(s, str) and s.strip():
                findings.append(CandidateFinding(
                    cid=cid,
                    company_name=company_name,
                    place_id=business.get("place_id"),
                    entity_type="service",
                    field_name="service",
                    field_value=s.strip(),
                    source_url=root_url,
                    source_type=SourceType.COMPANY_WEBSITE.value,
                    source_title=f"{company_name} Official Website",
                    confidence=0.9
                ))

        for loc in profile.get("locations") or []:
            if isinstance(loc, str) and loc.strip():
                findings.append(CandidateFinding(
                    cid=cid,
                    company_name=company_name,
                    place_id=business.get("place_id"),
                    entity_type="location",
                    field_name="location",
                    field_value=loc.strip(),
                    source_url=root_url,
                    source_type=SourceType.COMPANY_WEBSITE.value,
                    source_title=f"{company_name} Official Website",
                    confidence=0.95
                ))

        # 2. Extended Extraction: Professional Profiles linked on the company website
        seen_profile_urls: Set[str] = set()
        for url, page in crawled_pages.items():
            html_content = page.html
            matches = LINKEDIN_PROFILE_PATTERN.findall(html_content)
            for handle in matches:
                full_linkedin_url = f"https://www.linkedin.com/in/{handle.strip()}"
                if full_linkedin_url in seen_profile_urls:
                    continue
                seen_profile_urls.add(full_linkedin_url)

                # Try to associate with person name from surrounding text/anchor
                person_name = None
                anchor_match = re.search(
                    r'<a[^>]+href=["\'][^"\']*linkedin\.com/in/' + re.escape(handle) + r'[^"\']*["\'][^>]*>(.*?)</a>',
                    html_content,
                    re.IGNORECASE | re.DOTALL
                )
                if anchor_match:
                    anchor_text = re.sub(r'<[^>]+>', ' ', anchor_match.group(1)).strip()
                    if anchor_text and len(anchor_text.split()) in (2, 3):
                        person_name = anchor_text.title()

                findings.append(CandidateFinding(
                    cid=cid,
                    company_name=company_name,
                    place_id=business.get("place_id"),
                    entity_type="leader" if person_name else "profile",
                    person_name=person_name,
                    field_name="profile_url",
                    field_value=full_linkedin_url,
                    source_url=url,
                    source_type=SourceType.PROFESSIONAL_PROFILE.value,
                    source_title="Company Website LinkedIn Link",
                    confidence=0.88,
                    raw_metadata={"handle": handle, "discovered_on": url}
                ))

        status = "success" if findings else "no_data"
        return findings, SourceCheckResult(
            source_url=root_url,
            source_type=self.source_type,
            source_title=self.name,
            status=status,
            error_message=None if status == "success" else "Checked website successfully but found no candidate records",
            findings_count=len(findings),
            retrieved_at=timestamp
        )
