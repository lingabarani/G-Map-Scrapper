import asyncio
from typing import Any, Callable, Dict, List, Optional
import httpx

from ..db import Database
from ..scope import validate_target_country, get_region
from .discovery import WebsiteDiscoverer
from .verifier import WebsiteVerifier

class WebsiteDiscoveryService:
    def __init__(
        self,
        db: Database,
        timeout_seconds: float = 10.0,
        user_agent: Optional[str] = None,
        check_dns: bool = True
    ):
        self.db = db
        self.discoverer = WebsiteDiscoverer(user_agent=user_agent)
        self.verifier = WebsiteVerifier(timeout_seconds=timeout_seconds, user_agent=user_agent, check_dns=check_dns)

    async def run_discovery(
        self,
        country_code: str,
        region_code: Optional[str] = None,
        limit: int = 50,
        retry_failed: bool = False,
        force: bool = False,
        dry_run: bool = False,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        http_client: Optional[httpx.AsyncClient] = None
    ) -> Dict[str, Any]:
        """
        Executes Step 2 Official Website Discovery & Verification workflow:
        1. Select eligible USA/Mexico businesses.
        2. Identify existing website or discover candidate if missing.
        3. Verify candidate with SSRF safety and multi-signal matching.
        4. Save final verified website, candidate, status, and evidence to database.
        5. Support dry-run mode and safe resume.
        """
        cc = validate_target_country(country_code)
        if region_code:
            get_region(cc, region_code)

        businesses = self.db.get_businesses_for_website_discovery(
            country_code=cc,
            region_code=region_code,
            limit=limit,
            retry_failed=retry_failed,
            force=force
        )

        results: List[Dict[str, Any]] = []
        counts = {
            "verified": 0,
            "needs_review": 0,
            "website_missing": 0,
            "failed": 0
        }

        for biz in businesses:
            cid = biz["cid"]
            name = biz.get("name", "Unknown Business")

            # 1. Discover candidate
            candidate_url, discovery_method = await self.discoverer.discover_candidate(biz, http_client=http_client)

            if not candidate_url:
                outcome = {
                    "cid": cid,
                    "name": name,
                    "status": "website_missing",
                    "candidate": None,
                    "final_url": None,
                    "discovery_method": discovery_method,
                    "evidence": {"reason": "No candidate website found in local record or discovery queries"},
                    "error": None
                }
                counts["website_missing"] += 1
                results.append(outcome)

                if not dry_run:
                    self.db.update_business_website_verification(
                        cid=cid,
                        status="website_missing",
                        candidate=None,
                        discovery_method=discovery_method,
                        evidence=outcome["evidence"]
                    )
                if on_progress:
                    on_progress(outcome)
                continue

            # 2. Verify candidate
            ver_res = await self.verifier.verify(candidate_url, biz, http_client=http_client)
            status = ver_res["status"]
            final_url = ver_res.get("final_url") if ver_res.get("verified") else None
            evidence = ver_res.get("evidence", {})
            error = ver_res.get("error")

            counts[status] = counts.get(status, 0) + 1

            outcome = {
                "cid": cid,
                "name": name,
                "status": status,
                "candidate": candidate_url,
                "final_url": final_url or (biz.get("website") if status != "verified" else None),
                "discovery_method": discovery_method,
                "evidence": evidence,
                "error": error
            }
            results.append(outcome)

            if not dry_run:
                self.db.update_business_website_verification(
                    cid=cid,
                    status=status,
                    website=final_url,
                    candidate=candidate_url,
                    discovery_method=discovery_method,
                    evidence=evidence,
                    error=error
                )

            if on_progress:
                on_progress(outcome)

        return {
            "country_code": cc,
            "region_code": region_code.upper() if region_code else None,
            "total_inspected": len(businesses),
            "counts": counts,
            "dry_run": dry_run,
            "results": results
        }
