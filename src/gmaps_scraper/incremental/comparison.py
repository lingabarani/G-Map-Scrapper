from typing import Any, Dict, List, Optional, Set, Tuple

from .models import CandidateFinding, ComparisonResult, MatchStatus
from .normalizer import (
    canonical_title_key,
    normalize_email,
    normalize_name,
    normalize_name_key,
    normalize_phone,
    normalize_phone_digits,
    normalize_text,
    normalize_title,
    normalize_url,
)

SINGLE_HOLDER_ROLES = {"ceo", "cfo", "coo", "cto", "cio", "president"}


class ComparisonEngine:
    """
    Compares candidate business intelligence findings against existing database records
    to detect EXACT_MATCH, NEW_INFORMATION, POSSIBLE_MATCH, CONFLICT, or UNVERIFIED.
    """

    def __init__(self, existing_data: Dict[str, Any]):
        """
        existing_data dictionary contains:
        - place: dict of places row (name, address, phone, website, etc.)
        - profile: dict of company_profiles row (overview, services, locations, etc.)
        - leaders: list of company_leaders rows
        - contacts: list of company_contacts rows
        - existing_intelligence: list of additional_intelligence rows already saved
        """
        self.place = existing_data.get("place") or {}
        self.profile = existing_data.get("profile") or {}
        self.leaders = existing_data.get("leaders") or []
        self.contacts = existing_data.get("contacts") or []
        self.existing_intelligence = existing_data.get("existing_intelligence") or []

        self._index_existing()

    def _index_existing(self):
        """Builds lookup indexes for fast, accurate comparison."""
        # Emails
        self.existing_emails: Set[str] = set()
        for c in self.contacts:
            if c.get("contact_type") == "email" and c.get("contact_value"):
                norm = normalize_email(c["contact_value"])
                if norm:
                    self.existing_emails.add(norm)

        # Phones
        self.existing_phone_digits: Set[str] = set()
        for c in self.contacts:
            if c.get("contact_type") == "phone" and c.get("contact_value"):
                dig = normalize_phone_digits(c["contact_value"])
                if dig:
                    self.existing_phone_digits.add(dig)

        # Place phone
        if self.place.get("phone"):
            dig = normalize_phone_digits(self.place["phone"])
            if dig:
                self.existing_phone_digits.add(dig)
        if self.place.get("phone_e164"):
            dig = normalize_phone_digits(self.place["phone_e164"])
            if dig:
                self.existing_phone_digits.add(dig)

        # Leaders: (name_key, title_key) -> leader
        self.existing_leaders_by_name: Dict[str, List[Dict[str, Any]]] = {}
        self.existing_leaders_by_role: Dict[str, Dict[str, Any]] = {}
        for l in self.leaders:
            n_key = normalize_name_key(l.get("full_name"))
            t_key = canonical_title_key(l.get("title"))
            if n_key:
                self.existing_leaders_by_name.setdefault(n_key, []).append(l)
            if t_key and t_key in SINGLE_HOLDER_ROLES:
                self.existing_leaders_by_role[t_key] = l

        # Services
        self.existing_services: Set[str] = set()
        raw_services = self.profile.get("services") or []
        for s in raw_services:
            if isinstance(s, str):
                self.existing_services.add(normalize_text(s).lower())

        # Locations
        self.existing_locations: Set[str] = set()
        raw_locs = self.profile.get("locations") or []
        for loc in raw_locs:
            if isinstance(loc, str):
                self.existing_locations.add(normalize_text(loc).lower())
        if self.place.get("address"):
            self.existing_locations.add(normalize_text(self.place["address"]).lower())

        # Existing intelligence entries: (entity_type, field_name, normalized_value)
        self.existing_intel_keys: Set[Tuple[str, str, str]] = set()
        for row in self.existing_intelligence:
            e_type = row.get("entity_type") or ""
            f_name = row.get("field_name") or ""
            n_val = row.get("normalized_value") or ""
            if e_type and f_name and n_val:
                self.existing_intel_keys.add((e_type, f_name, n_val))

    def evaluate(self, candidate: CandidateFinding) -> ComparisonResult:
        """Evaluates a candidate finding against existing data."""
        # 1. Normalize candidate value based on field type
        self._normalize_candidate(candidate)

        # 2. Check if already recorded in additional_intelligence
        intel_key = (candidate.entity_type, candidate.field_name, candidate.normalized_value or candidate.field_value)
        if intel_key in self.existing_intel_keys:
            return ComparisonResult(
                finding=candidate,
                match_status=MatchStatus.EXACT_MATCH,
                existing_value=candidate.field_value,
                match_reason="Finding already recorded in additional intelligence database.",
                confidence=candidate.confidence,
                requires_review=False
            )

        # 3. Entity-specific comparison
        if candidate.entity_type == "contact":
            return self._compare_contact(candidate)
        elif candidate.entity_type == "leader":
            return self._compare_leader(candidate)
        elif candidate.entity_type == "profile":
            return self._compare_profile(candidate)
        elif candidate.entity_type == "service":
            return self._compare_service(candidate)
        elif candidate.entity_type == "location":
            return self._compare_location(candidate)
        elif candidate.entity_type == "registry":
            return self._compare_registry(candidate)
        else:
            return self._compare_generic(candidate)

    def _normalize_candidate(self, candidate: CandidateFinding):
        """Sets candidate.normalized_value."""
        field = candidate.field_name.lower()
        val = candidate.field_value

        if field == "email":
            candidate.normalized_value = normalize_email(val) or val.strip().lower()
        elif field == "phone":
            candidate.normalized_value = normalize_phone(val) or val.strip()
        elif field in ("name", "full_name", "leader"):
            candidate.normalized_value = normalize_name(val) or val.strip()
        elif field in ("title", "job_title"):
            candidate.normalized_value = normalize_title(val) or val.strip()
        elif field in ("url", "website", "profile_url", "linkedin_url"):
            candidate.normalized_value = normalize_url(val) or val.strip()
        else:
            candidate.normalized_value = normalize_text(val)

    def _compare_contact(self, candidate: CandidateFinding) -> ComparisonResult:
        field = candidate.field_name.lower()

        if field == "email":
            norm_email = candidate.normalized_value or candidate.field_value.strip().lower()
            if norm_email in self.existing_emails:
                return ComparisonResult(
                    finding=candidate,
                    match_status=MatchStatus.EXACT_MATCH,
                    existing_value=norm_email,
                    match_reason=f"Email '{norm_email}' already exists in company contacts.",
                    confidence=1.0,
                    requires_review=False
                )
            return ComparisonResult(
                finding=candidate,
                match_status=MatchStatus.NEW_INFORMATION,
                existing_value=None,
                match_reason=f"New business email '{candidate.field_value}' discovered.",
                confidence=candidate.confidence,
                requires_review=False
            )

        elif field == "phone":
            digits = normalize_phone_digits(candidate.field_value)
            if digits and digits in self.existing_phone_digits:
                return ComparisonResult(
                    finding=candidate,
                    match_status=MatchStatus.EXACT_MATCH,
                    existing_value=candidate.normalized_value,
                    match_reason=f"Phone digits '{digits}' match an existing business telephone number.",
                    confidence=1.0,
                    requires_review=False
                )
            return ComparisonResult(
                finding=candidate,
                match_status=MatchStatus.NEW_INFORMATION,
                existing_value=None,
                match_reason=f"New business telephone number '{candidate.normalized_value}' discovered.",
                confidence=candidate.confidence,
                requires_review=False
            )

        return self._compare_generic(candidate)

    def _compare_leader(self, candidate: CandidateFinding) -> ComparisonResult:
        cand_name = candidate.person_name or candidate.field_value
        cand_title = candidate.job_title or candidate.raw_metadata.get("title", "")
        n_key = normalize_name_key(cand_name)
        t_key = canonical_title_key(cand_title)

        # Check for role conflict in single-holder roles (e.g. CEO)
        if t_key and t_key in SINGLE_HOLDER_ROLES:
            existing_holder = self.existing_leaders_by_role.get(t_key)
            if existing_holder:
                ex_name_key = normalize_name_key(existing_holder.get("full_name"))
                if ex_name_key and n_key and ex_name_key != n_key:
                    # Conflicting executives reported for single-holder role
                    return ComparisonResult(
                        finding=candidate,
                        match_status=MatchStatus.CONFLICT,
                        existing_value=f"{existing_holder.get('full_name')} ({existing_holder.get('title')})",
                        match_reason=(
                            f"Role conflict: candidate '{cand_name}' identified as {cand_title}, "
                            f"but database already lists '{existing_holder.get('full_name')}' as {existing_holder.get('title')}."
                        ),
                        confidence=candidate.confidence,
                        requires_review=True
                    )

        # Check existing leaders with same or similar name
        if n_key and n_key in self.existing_leaders_by_name:
            matched_leaders = self.existing_leaders_by_name[n_key]
            for ex in matched_leaders:
                ex_t_key = canonical_title_key(ex.get("title"))
                if t_key and ex_t_key and t_key == ex_t_key:
                    # Check if this finding is a new subfield for this person (e.g. profile URL or bio)
                    if candidate.field_name in ("profile_url", "linkedin_url", "biography", "email", "phone"):
                        return ComparisonResult(
                            finding=candidate,
                            match_status=MatchStatus.NEW_INFORMATION,
                            existing_value=None,
                            match_reason=f"New {candidate.field_name} discovered for existing leader '{ex.get('full_name')}'.",
                            confidence=candidate.confidence,
                            requires_review=False
                        )
                    return ComparisonResult(
                        finding=candidate,
                        match_status=MatchStatus.EXACT_MATCH,
                        existing_value=f"{ex.get('full_name')} - {ex.get('title')}",
                        match_reason=f"Leader '{ex.get('full_name')}' ({ex.get('title')}) already recorded in company leaders.",
                        confidence=1.0,
                        requires_review=False
                    )
                else:
                    # Same name, different title
                    return ComparisonResult(
                        finding=candidate,
                        match_status=MatchStatus.POSSIBLE_MATCH,
                        existing_value=f"{ex.get('full_name')} ({ex.get('title')})",
                        match_reason=(
                            f"Possible title update or role variation for '{cand_name}': "
                            f"existing='{ex.get('title')}', candidate='{cand_title}'."
                        ),
                        confidence=candidate.confidence * 0.85,
                        requires_review=True
                    )

        # Check for partial name overlap to prevent naive merges
        cand_tokens = set(n_key[:4] for n_key in [n_key] if n_key)
        for ex_n_key, ex_list in self.existing_leaders_by_name.items():
            if cand_tokens and any(ex_n_key.startswith(t) for t in cand_tokens) and ex_n_key != n_key:
                ex = ex_list[0]
                # Similar first characters but distinct person
                return ComparisonResult(
                    finding=candidate,
                    match_status=MatchStatus.NEW_INFORMATION,
                    existing_value=f"Distinct from existing '{ex.get('full_name')}'",
                    match_reason=f"New executive '{cand_name}' ({cand_title}) verified distinct from '{ex.get('full_name')}'.",
                    confidence=candidate.confidence,
                    requires_review=False
                )

        # Completely new executive
        return ComparisonResult(
            finding=candidate,
            match_status=MatchStatus.NEW_INFORMATION,
            existing_value=None,
            match_reason=f"New executive '{cand_name}' with title '{cand_title}' discovered.",
            confidence=candidate.confidence,
            requires_review=False
        )

    def _compare_profile(self, candidate: CandidateFinding) -> ComparisonResult:
        field = candidate.field_name.lower()
        if field == "overview":
            ex_overview = self.profile.get("overview")
            if ex_overview:
                norm_ex = normalize_text(ex_overview).lower()
                norm_cand = normalize_text(candidate.field_value).lower()
                if norm_cand in norm_ex or norm_ex in norm_cand:
                    return ComparisonResult(
                        finding=candidate,
                        match_status=MatchStatus.EXACT_MATCH,
                        existing_value=ex_overview[:100] + "...",
                        match_reason="Company overview matches existing description.",
                        confidence=1.0,
                        requires_review=False
                    )
                else:
                    return ComparisonResult(
                        finding=candidate,
                        match_status=MatchStatus.NEW_INFORMATION,
                        existing_value=ex_overview[:100] + "...",
                        match_reason="Additional/updated company overview statement discovered.",
                        confidence=candidate.confidence,
                        requires_review=False
                    )
            else:
                return ComparisonResult(
                    finding=candidate,
                    match_status=MatchStatus.NEW_INFORMATION,
                    existing_value=None,
                    match_reason="New company overview description discovered.",
                    confidence=candidate.confidence,
                    requires_review=False
                )
        return self._compare_generic(candidate)

    def _compare_service(self, candidate: CandidateFinding) -> ComparisonResult:
        norm = normalize_text(candidate.field_value).lower()
        if norm in self.existing_services:
            return ComparisonResult(
                finding=candidate,
                match_status=MatchStatus.EXACT_MATCH,
                existing_value=candidate.field_value,
                match_reason=f"Service '{candidate.field_value}' already listed in company offerings.",
                confidence=1.0,
                requires_review=False
            )
        return ComparisonResult(
            finding=candidate,
            match_status=MatchStatus.NEW_INFORMATION,
            existing_value=None,
            match_reason=f"New business service/offering '{candidate.field_value}' discovered.",
            confidence=candidate.confidence,
            requires_review=False
        )

    def _compare_location(self, candidate: CandidateFinding) -> ComparisonResult:
        norm = normalize_text(candidate.field_value).lower()
        for ex_loc in self.existing_locations:
            if norm in ex_loc or ex_loc in norm:
                return ComparisonResult(
                    finding=candidate,
                    match_status=MatchStatus.EXACT_MATCH,
                    existing_value=ex_loc,
                    match_reason=f"Operating location '{candidate.field_value}' already known.",
                    confidence=1.0,
                    requires_review=False
                )
        return ComparisonResult(
            finding=candidate,
            match_status=MatchStatus.NEW_INFORMATION,
            existing_value=None,
            match_reason=f"New branch or operating location '{candidate.field_value}' discovered.",
            confidence=candidate.confidence,
            requires_review=False
        )

    def _compare_registry(self, candidate: CandidateFinding) -> ComparisonResult:
        # Public registry findings (e.g. entity number, registration status, formation date)
        return ComparisonResult(
            finding=candidate,
            match_status=MatchStatus.NEW_INFORMATION,
            existing_value=None,
            match_reason=f"Public registry information for {candidate.field_name}: '{candidate.field_value}'.",
            confidence=candidate.confidence,
            requires_review=False
        )

    def _compare_generic(self, candidate: CandidateFinding) -> ComparisonResult:
        if candidate.confidence < 0.5:
            return ComparisonResult(
                finding=candidate,
                match_status=MatchStatus.UNVERIFIED,
                existing_value=None,
                match_reason="Confidence score below threshold; insufficient evidence.",
                confidence=candidate.confidence,
                requires_review=True
            )
        return ComparisonResult(
            finding=candidate,
            match_status=MatchStatus.NEW_INFORMATION,
            existing_value=None,
            match_reason=f"New field '{candidate.field_name}' discovered from source.",
            confidence=candidate.confidence,
            requires_review=False
        )
