import abc
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import httpx

from ..models import CandidateFinding, SourceCheckResult, SourceType
from ...website.ssrf import is_safe_url, normalize_url


class BaseSourceAdapter(abc.ABC):
    """
    Abstract Base Class for Incremental Public-Source Intelligence Adapters.
    Enforces SSRF protection, redirect validation, bounded timeouts,
    and accurate source-level tracking.
    """

    def __init__(
        self,
        source_type: str,
        priority: int,
        name: str,
        timeout_seconds: float = 10.0,
        check_dns: bool = True
    ):
        self.source_type = source_type
        self.priority = priority
        self.name = name
        self.timeout = timeout_seconds
        self.check_dns = check_dns

    @abc.abstractmethod
    def can_handle(self, business: Dict[str, Any]) -> bool:
        """Determines if this adapter is applicable for the business."""
        pass

    @abc.abstractmethod
    async def fetch_and_extract(
        self,
        business: Dict[str, Any],
        http_client: httpx.AsyncClient,
        **kwargs: Any
    ) -> Tuple[List[CandidateFinding], SourceCheckResult]:
        """
        Executes safe fetch and returns candidate findings and source check status.
        Must not raise uncaught network exceptions.
        """
        pass

    def validate_url_safe(self, url: str) -> Tuple[bool, Optional[str]]:
        """Validates that candidate URL is SSRF safe."""
        norm = normalize_url(url)
        return is_safe_url(norm, check_dns=self.check_dns)
