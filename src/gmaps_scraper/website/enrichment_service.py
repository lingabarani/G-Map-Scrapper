import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
import httpx

from ..db import Database
from ..scope import validate_target_country, get_region
from .crawler import SafeWebsiteCrawler
from .extractor import WebsiteExtractor

class WebsiteEnrichmentService:
    """
    Step 3 Company Information, Leadership, and Business Contact Extraction Service:
    - Selects USA/Mexico records with verified official websites.
    - Crawls permitted public website pages within the verified domain.
    - Extracts company overview, industry, services, locations, leadership, and contacts.
    - Saves extracted values and provenance to SQLite (with idempotent deduplication).
    - Supports dry-run mode and safe resume/retry.
    """

    def __init__(
        self,
        db: Database,
        max_pages: int = 5,
        timeout_seconds: float = 10.0,
        user_agent: Optional[str] = None,
        check_dns: bool = True
    ):
        self.db = db
        self.max_pages = max_pages
        self.timeout = timeout_seconds
        self.user_agent = user_agent
        self.check_dns = check_dns

    async def run_enrichment(
        self,
        country_code: str,
        region_code: Optional[str] = None,
        limit: int = 50,
        max_pages: Optional[int] = None,
        retry_failed: bool = False,
        force: bool = False,
        dry_run: bool = False,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        http_client: Optional[httpx.AsyncClient] = None
    ) -> Dict[str, Any]:
        """
        Executes Step 3 website information extraction workflow.
        """
        cc = validate_target_country(country_code)
        if region_code:
            get_region(cc, region_code)

        effective_max_pages = max_pages or self.max_pages
        crawler = SafeWebsiteCrawler(
            max_pages=effective_max_pages,
            timeout_seconds=self.timeout,
            user_agent=self.user_agent,
            check_dns=self.check_dns
        )

        businesses = self.db.get_verified_businesses_for_enrichment(
            country_code=cc,
            region_code=region_code,
            limit=limit,
            retry_failed=retry_failed,
            force=force
        )

        results: List[Dict[str, Any]] = []
        counts = {
            "total_selected": len(businesses),
            "completed": 0,
            "partial": 0,
            "failed": 0,
            "total_leaders_extracted": 0,
            "total_contacts_extracted": 0
        }

        for biz in businesses:
            cid = biz["cid"]
            name = biz.get("name", "Unknown Business")
            website_url = biz.get("official_website_url") or biz.get("website")

            if not website_url:
                outcome = {
                    "cid": cid,
                    "name": name,
                    "status": "failed",
                    "url": None,
                    "error": "No verified official website URL found",
                    "leaders_count": 0,
                    "contacts_count": 0,
                    "profile": None,
                    "leaders": [],
                    "contacts": []
                }
                counts["failed"] += 1
                results.append(outcome)
                if not dry_run:
                    with self.db.get_connection() as conn:
                        conn.execute(
                            "UPDATE places SET enrichment_status = 'failed', enrichment_last_checked = ? WHERE cid = ?",
                            (datetime.now(timezone.utc).isoformat(), cid)
                        )
                        conn.commit()
                if on_progress:
                    on_progress(outcome)
                continue

            # Crawl website pages safely
            crawled_pages = await crawler.crawl(website_url, http_client=http_client)

            if not crawled_pages:
                outcome = {
                    "cid": cid,
                    "name": name,
                    "status": "failed",
                    "url": website_url,
                    "error": "Failed to crawl any public HTML pages safely",
                    "leaders_count": 0,
                    "contacts_count": 0,
                    "profile": None,
                    "leaders": [],
                    "contacts": []
                }
                counts["failed"] += 1
                results.append(outcome)
                if not dry_run:
                    self.db.save_company_profile(
                        cid=cid,
                        overview=None,
                        industry=biz.get("primary_type"),
                        services=[],
                        locations=[],
                        contact_page_url=None,
                        source_pages=[],
                        extraction_status="failed",
                        last_error="Failed to crawl any public HTML pages safely"
                    )
                if on_progress:
                    on_progress(outcome)
                continue

            # Extract structured company info, leadership, and contacts
            extracted = WebsiteExtractor.extract(biz, crawled_pages)
            profile = extracted["profile"]
            leaders = extracted["leaders"]
            contacts = extracted["contacts"]
            source_pages = extracted["source_pages"]
            status = profile.get("extraction_status", "completed")

            outcome = {
                "cid": cid,
                "name": name,
                "status": status,
                "url": website_url,
                "pages_crawled": len(crawled_pages),
                "leaders_count": len(leaders),
                "contacts_count": len(contacts),
                "profile": profile,
                "leaders": leaders,
                "contacts": contacts,
                "source_pages": source_pages,
                "error": None
            }

            if status == "completed":
                counts["completed"] += 1
            else:
                counts["partial"] += 1

            counts["total_leaders_extracted"] += len(leaders)
            counts["total_contacts_extracted"] += len(contacts)
            results.append(outcome)

            if not dry_run:
                self.db.save_company_profile(
                    cid=cid,
                    overview=profile.get("overview"),
                    industry=profile.get("industry"),
                    services=profile.get("services"),
                    locations=profile.get("locations"),
                    contact_page_url=profile.get("contact_page_url"),
                    source_pages=source_pages,
                    extraction_status=status,
                    last_error=None
                )
                self.db.save_company_leaders(cid=cid, leaders=leaders)
                self.db.save_company_contacts(cid=cid, contacts=contacts)

            if on_progress:
                on_progress(outcome)

        return {
            "country_code": cc,
            "region_code": region_code,
            "dry_run": dry_run,
            "counts": counts,
            "results": results
        }
