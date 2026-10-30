from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class MatchStatus(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    NEW_INFORMATION = "NEW_INFORMATION"
    POSSIBLE_MATCH = "POSSIBLE_MATCH"
    CONFLICT = "CONFLICT"
    UNVERIFIED = "UNVERIFIED"
    SOURCE_FAILED = "SOURCE_FAILED"


class SourceType(str, Enum):
    COMPANY_WEBSITE = "company_website"
    ANNOUNCEMENT_PAGE = "announcement_page"
    PUBLIC_REGISTRY = "public_registry"
    PROFESSIONAL_PROFILE = "professional_profile"
    OPEN_BUSINESS_SOURCE = "open_business_source"


def parse_confidence(val: Any) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val_l = val.strip().lower()
        if val_l == "high":
            return 0.95
        elif val_l == "medium":
            return 0.75
        elif val_l == "low":
            return 0.50
        try:
            return float(val_l)
        except ValueError:
            return 0.8
    return 1.0


class CandidateFinding(BaseModel):
    cid: str
    company_name: Optional[str] = None
    place_id: Optional[str] = None
    entity_type: str  # 'leader', 'contact', 'profile', 'service', 'location', 'registry'
    person_name: Optional[str] = None
    job_title: Optional[str] = None
    field_name: str  # 'email', 'phone', 'title', 'biography', 'profile_url', 'overview', 'service', 'location', etc.
    field_value: str
    normalized_value: Optional[str] = None
    source_url: str
    source_type: str = SourceType.COMPANY_WEBSITE.value
    source_title: Optional[str] = None
    confidence: float = 1.0
    evidence_text: Optional[str] = None
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, v: Any) -> float:
        return parse_confidence(v)


class ComparisonResult(BaseModel):
    finding: CandidateFinding
    match_status: MatchStatus
    existing_value: Optional[str] = None
    match_reason: str = ""
    confidence: float = 1.0
    requires_review: bool = False

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, v: Any) -> float:
        return parse_confidence(v)


class SourceCheckResult(BaseModel):
    source_url: str
    source_type: str
    source_title: Optional[str] = None
    status: str = "success"  # 'success', 'no_data', 'failed', 'blocked'
    error_message: Optional[str] = None
    findings_count: int = 0
    retrieved_at: str
