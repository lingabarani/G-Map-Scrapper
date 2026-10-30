import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set
import httpx

from .adapters import (
    BaseSourceAdapter,
    CompanyWebsiteExtendedAdapter,
    OpenBusinessSourcesAdapter,
    ProfessionalProfileAdapter,
    StateRegistryAdapter,
)
from .comparison import ComparisonEngine
from .models import CandidateFinding, ComparisonResult, MatchStatus, SourceCheckResult
from ..db import Database
from ..scope import get_region, validate_target_country


class IncrementalEnrichmentService:
    """
    Incremental Business Intelligence Enrichment Service:
    - Queries public sources via modular adapters in priority order.
    - Normalizes and compares candidate findings against existing database records.
    - Accurately classifies findings: EXACT_MATCH, NEW_INFORMATION, POSSIBLE_MATCH, CONFLICT, UNVERIFIED.
    - Preserves existing values on conflicts without silent overwriting.
    - Idempotently prevents duplicate leaders and contacts.
    - Retains full source provenance and access history.
    - Supports strict dry-run mode and resumption.
    """

    def __init__(
        self,
        db: Database,
        max_pages: int = 5,
        timeout_seconds: float = 10.0,
        user_agent: Optional[str] = None,
        check_dns: bool = True,
        source_types: Optional[List[str]] = None
    ):
        self.db = db
        self.max_pages = max_pages
        self.timeout = timeout_seconds
        self.user_agent = user_agent
        self.check_dns = check_dns
        self.source_types = set(source_types or ["all"])

        # Initialize adapters in priority order
        self.adapters: List[BaseSourceAdapter] = [
            CompanyWebsiteExtendedAdapter(
                max_pages=self.max_pages,
                timeout_seconds=self.timeout,
                user_agent=self.user_agent,
                check_dns=self.check_dns
            ),
            StateRegistryAdapter(
                timeout_seconds=self.timeout,
                check_dns=self.check_dns
            ),
            ProfessionalProfileAdapter(
                timeout_seconds=self.timeout,
                check_dns=self.check_dns
            ),
            OpenBusinessSourcesAdapter(
                timeout_seconds=self.timeout,
                check_dns=self.check_dns
            ),
        ]

    def _is_adapter_enabled(self, adapter: BaseSourceAdapter) -> bool:
        if "all" in self.source_types:
            return True
        st = adapter.source_type.lower()
        name_lower = adapter.name.lower()
        for t in self.source_types:
            t_low = t.lower()
            if t_low in st or t_low in name_lower:
                return True
        return False

    async def run_incremental_enrichment(
        self,
        country_code: str,
        region_code: Optional[str] = None,
        limit: int = 20,
        max_pages: Optional[int] = None,
        retry_failed: bool = False,
        force: bool = False,
        dry_run: bool = False,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        http_client: Optional[httpx.AsyncClient] = None
    ) -> Dict[str, Any]:
        """
        Executes incremental intelligence enrichment workflow for targeted scope.
        """
        cc = validate_target_country(country_code)
        if region_code:
            get_region(cc, region_code)

        effective_max_pages = max_pages or self.max_pages
        # Update crawler max_pages if provided
        for adp in self.adapters:
            if isinstance(adp, CompanyWebsiteExtendedAdapter):
                adp.max_pages = effective_max_pages

        # 1. Fetch businesses for enrichment
        businesses = self.db.get_verified_businesses_for_enrichment(
            country_code=cc,
            region_code=region_code,
            limit=limit,
            retry_failed=retry_failed,
            force=force
        )

        counts = {
            "businesses_inspected": len(businesses),
            "sources_attempted": 0,
            "sources_succeeded": 0,
            "sources_no_data": 0,
            "sources_failed": 0,
            "new_findings": 0,
            "existing_matches": 0,
            "duplicates_prevented": 0,
            "conflicts": 0,
            "possible_matches": 0,
            "unverified": 0,
            "new_leaders": 0,
            "new_contacts": 0,
        }

        all_comparison_results: List[ComparisonResult] = []
        all_source_results: List[Dict[str, Any]] = []

        client_provided = http_client is not None
        client = http_client or httpx.AsyncClient(
            headers={"User-Agent": self.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IncrementalScraper/1.0"},
            timeout=self.timeout,
            follow_redirects=False,
            verify=True
        )

        try:
            for biz in businesses:
                cid = biz["cid"]
                company_name = biz.get("name", "Unknown Business")

                # Fetch existing data for comparison
                existing_intel, _ = self.db.get_additional_intelligence(cid=cid, limit=500)
                company_enrichment = self.db.get_company_enrichment(cid)
                existing_leaders = self.db.get_company_leaders(cid)
                existing_contacts = self.db.get_company_contacts(cid)

                existing_data = {
                    "place": biz,
                    "profile": company_enrichment.get("profile") or {},
                    "leaders": existing_leaders,
                    "contacts": existing_contacts,
                    "existing_intelligence": existing_intel
                }

                comparison_engine = ComparisonEngine(existing_data)
                biz_findings: List[ComparisonResult] = []
                biz_sources: List[SourceCheckResult] = []

                # Run through enabled adapters in priority order
                for adapter in self.adapters:
                    if not self._is_adapter_enabled(adapter):
                        continue
                    if not adapter.can_handle(biz):
                        continue

                    counts["sources_attempted"] += 1
                    try:
                        candidates, src_result = await adapter.fetch_and_extract(
                            biz, http_client=client
                        )
                    except Exception as ex:
                        src_result = SourceCheckResult(
                            source_url=biz.get("official_website_url") or "",
                            source_type=adapter.source_type,
                            source_title=adapter.name,
                            status="failed",
                            error_message=f"Adapter execution error: {str(ex)}",
                            findings_count=0,
                            retrieved_at=datetime.now(timezone.utc).isoformat()
                        )
                        candidates = []

                    biz_sources.append(src_result)
                    all_source_results.append({
                        "cid": cid,
                        "company_name": company_name,
                        **src_result.model_dump()
                    })

                    if src_result.status == "success":
                        counts["sources_succeeded"] += 1
                    elif src_result.status == "no_data":
                        counts["sources_no_data"] += 1
                    else:
                        counts["sources_failed"] += 1

                    # Compare each candidate finding
                    for cand in candidates:
                        comp_res = comparison_engine.evaluate(cand)
                        biz_findings.append(comp_res)
                        all_comparison_results.append(comp_res)

                        status_val = comp_res.match_status
                        if status_val == MatchStatus.NEW_INFORMATION:
                            counts["new_findings"] += 1
                            if cand.entity_type == "leader":
                                counts["new_leaders"] += 1
                            elif cand.entity_type == "contact":
                                counts["new_contacts"] += 1
                        elif status_val == MatchStatus.EXACT_MATCH:
                            counts["existing_matches"] += 1
                            counts["duplicates_prevented"] += 1
                        elif status_val == MatchStatus.CONFLICT:
                            counts["conflicts"] += 1
                        elif status_val == MatchStatus.POSSIBLE_MATCH:
                            counts["possible_matches"] += 1
                        elif status_val == MatchStatus.UNVERIFIED:
                            counts["unverified"] += 1

                        # Persistence (if not dry run)
                        if not dry_run:
                            intel_row = {
                                "place_id": cand.place_id,
                                "company_name": cand.company_name,
                                "entity_type": cand.entity_type,
                                "person_name": cand.person_name,
                                "job_title": cand.job_title,
                                "field_name": cand.field_name,
                                "field_value": cand.field_value,
                                "normalized_value": cand.normalized_value or cand.field_value,
                                "match_status": comp_res.match_status.value,
                                "verification_status": "verified" if comp_res.match_status in (MatchStatus.EXACT_MATCH, MatchStatus.NEW_INFORMATION) else "unverified",
                                "confidence": comp_res.confidence,
                                "conflict_existing_value": comp_res.existing_value if comp_res.match_status == MatchStatus.CONFLICT else None,
                                "match_reason": comp_res.match_reason,
                            }
                            intel_id, is_new = self.db.save_incremental_finding(cid, intel_row)

                            # Record source evidence linked to finding
                            self.db.record_intelligence_source(
                                cid=cid,
                                source_data={
                                    "source_url": cand.source_url,
                                    "source_type": cand.source_type,
                                    "source_title": cand.source_title,
                                    "evidence_summary": comp_res.match_reason,
                                    "status": "success",
                                    "error_message": None
                                },
                                intelligence_id=intel_id
                            )

                            # Idempotently update core tables for genuine new findings
                            if comp_res.match_status == MatchStatus.NEW_INFORMATION:
                                if cand.entity_type == "leader" and cand.person_name and cand.job_title:
                                    self.db.save_company_leaders(cid, [{
                                        "full_name": cand.person_name,
                                        "title": cand.job_title,
                                        "role_type": cand.raw_metadata.get("role_type"),
                                        "biography": cand.raw_metadata.get("biography"),
                                        "source_url": cand.source_url,
                                        "confidence": comp_res.confidence
                                    }])
                                elif cand.entity_type == "contact" and cand.field_name in ("email", "phone"):
                                    self.db.save_company_contacts(cid, [{
                                        "contact_type": cand.field_name,
                                        "contact_value": cand.field_value,
                                        "label": cand.raw_metadata.get("label", "general"),
                                        "source_url": cand.source_url
                                    }])

                # If no findings produced from source, still record source inspection
                if not dry_run:
                    for src in biz_sources:
                        if src.findings_count == 0:
                            self.db.record_intelligence_source(
                                cid=cid,
                                source_data={
                                    "source_url": src.source_url,
                                    "source_type": src.source_type,
                                    "source_title": src.source_title,
                                    "evidence_summary": None,
                                    "status": src.status,
                                    "error_message": src.error_message
                                },
                                intelligence_id=None
                            )

                if on_progress:
                    on_progress({
                        "cid": cid,
                        "company_name": company_name,
                        "findings_count": len(biz_findings),
                        "new_findings": sum(1 for f in biz_findings if f.match_status == MatchStatus.NEW_INFORMATION),
                        "exact_matches": sum(1 for f in biz_findings if f.match_status == MatchStatus.EXACT_MATCH),
                        "conflicts": sum(1 for f in biz_findings if f.match_status == MatchStatus.CONFLICT),
                        "sources_checked": len(biz_sources),
                        "dry_run": dry_run
                    })

        finally:
            if not client_provided:
                await client.aclose()

        return {
            "country_code": cc,
            "region_code": region_code,
            "dry_run": dry_run,
            "counts": counts,
            "findings": [
                {
                    "cid": r.finding.cid,
                    "company_name": r.finding.company_name,
                    "place_id": r.finding.place_id,
                    "entity_type": r.finding.entity_type,
                    "person_name": r.finding.person_name,
                    "job_title": r.finding.job_title,
                    "field_name": r.finding.field_name,
                    "field_value": r.finding.field_value,
                    "normalized_value": r.finding.normalized_value,
                    "match_status": r.match_status.value,
                    "existing_value": r.existing_value,
                    "match_reason": r.match_reason,
                    "confidence": r.confidence,
                    "requires_review": r.requires_review,
                    "source_url": r.finding.source_url,
                    "source_type": r.finding.source_type,
                    "source_title": r.finding.source_title,
                }
                for r in all_comparison_results
            ],
            "sources": all_source_results
        }
