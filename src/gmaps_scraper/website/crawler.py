import asyncio
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from .ssrf import is_safe_url, normalize_url

# File extensions to ignore during crawling
IGNORED_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".doc", ".docx", ".zip", ".tar", ".gz", ".mp3", ".mp4",
    ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot"
}

# High-priority keywords in path or anchor text for company info, leadership, and contacts
PRIORITY_KEYWORDS: List[str] = [
    "about", "team", "leadership", "executive", "management", "founder",
    "services", "products", "offerings", "menu", "locations", "stores",
    "contact", "touch", "find-us"
]

class PageResponse:
    def __init__(self, url: str, status_code: int, html: str):
        self.url = url
        self.status_code = status_code
        self.html = html or ""

class SafeWebsiteCrawler:
    """
    Crawls verified official website pages safely within the same domain:
    - Enforces strict SSRF protection on every URL and redirect hop.
    - Limits crawling strictly to the verified company domain.
    - Limits total pages crawled per business (default: 4-6 pages).
    - Prioritizes high-value pages: About, Leadership, Team, Services, Locations, Contact.
    - Avoids infinite loops, query loops, and non-HTML assets.
    """

    def __init__(
        self,
        max_pages: int = 5,
        timeout_seconds: float = 10.0,
        user_agent: Optional[str] = None,
        check_dns: bool = True
    ):
        self.max_pages = max_pages
        self.timeout = timeout_seconds
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        self.check_dns = check_dns

    async def crawl(
        self,
        root_url: str,
        http_client: Optional[httpx.AsyncClient] = None
    ) -> Dict[str, PageResponse]:
        """
        Crawls the homepage and up to max_pages priority internal pages.
        Returns a dictionary mapping {normalized_url: PageResponse}.
        """
        norm_root = normalize_url(root_url)
        safe, err = is_safe_url(norm_root, check_dns=self.check_dns)
        if not safe:
            return {}

        parsed_root = urllib.parse.urlsplit(norm_root)
        root_domain = parsed_root.netloc.lower()
        if not root_domain:
            return {}

        pages_visited: Dict[str, PageResponse] = {}
        urls_to_visit: List[str] = [norm_root]
        seen_urls: Set[str] = {norm_root}

        client_provided = http_client is not None
        client = http_client or httpx.AsyncClient(
            headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"},
            timeout=self.timeout,
            follow_redirects=False,
            verify=True
        )

        try:
            while urls_to_visit and len(pages_visited) < self.max_pages:
                current_url = urls_to_visit.pop(0)

                # Fetch current URL
                try:
                    resp = await client.get(current_url)
                except Exception:
                    continue

                # Handle redirect
                if resp.is_redirect and "location" in resp.headers:
                    redirect_target = urllib.parse.urljoin(current_url, resp.headers["location"])
                    safe_red, _ = is_safe_url(redirect_target, check_dns=self.check_dns)
                    if safe_red:
                        # Must remain on same domain
                        parsed_red = urllib.parse.urlsplit(redirect_target)
                        if self._is_same_domain(root_domain, parsed_red.netloc.lower()):
                            norm_red = normalize_url(redirect_target)
                            if norm_red not in seen_urls:
                                seen_urls.add(norm_red)
                                urls_to_visit.insert(0, norm_red)
                    continue

                if resp.status_code >= 400:
                    continue

                content_type = resp.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml" not in content_type and not resp.text.strip().startswith("<"):
                    continue

                html = resp.text
                final_url = str(resp.url)
                pages_visited[final_url] = PageResponse(final_url, resp.status_code, html)

                # Extract and queue links for high-value targets
                extracted_links = self._extract_internal_links(html, final_url, root_domain)
                for link in extracted_links:
                    if link not in seen_urls and len(seen_urls) < (self.max_pages * 4):
                        seen_urls.add(link)
                        # Insert priority links at front of queue
                        if any(k in link.lower() for k in PRIORITY_KEYWORDS):
                            urls_to_visit.append(link)
                        elif len(urls_to_visit) < self.max_pages:
                            urls_to_visit.append(link)

        finally:
            if not client_provided:
                await client.aclose()

        return pages_visited

    def _is_same_domain(self, base_domain: str, candidate_domain: str) -> bool:
        """Checks if candidate domain matches or is a subdomain of base domain."""
        b = base_domain.split(":")[0].lower()
        c = candidate_domain.split(":")[0].lower()
        if b.startswith("www."):
            b = b[4:]
        if c.startswith("www."):
            c = c[4:]
        return c == b or c.endswith("." + b)

    def _extract_internal_links(self, html: str, base_url: str, root_domain: str) -> List[str]:
        """Extracts and filters same-domain HTML links, prioritizing priority sections."""
        links: List[str] = []
        raw_hrefs = re.findall(r'<a[^>]+href=["\']([^"\'#\s>]+)["\']', html, re.IGNORECASE)

        priority_links: List[str] = []
        other_links: List[str] = []

        for href in raw_hrefs:
            href_clean = href.strip()
            if not href_clean or href_clean.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
                continue

            # Absolute URL
            abs_url = urllib.parse.urljoin(base_url, href_clean)
            parsed = urllib.parse.urlsplit(abs_url)

            if parsed.scheme.lower() not in ("http", "https"):
                continue

            if not self._is_same_domain(root_domain, parsed.netloc):
                continue

            # Check ignored file extensions
            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in IGNORED_EXTENSIONS):
                continue

            norm = normalize_url(abs_url)

            # Prioritize links with keywords
            if any(k in norm.lower() for k in PRIORITY_KEYWORDS):
                priority_links.append(norm)
            else:
                other_links.append(norm)

        # De-duplicate preserving priority order
        seen = set()
        result = []
        for l in priority_links + other_links:
            if l not in seen:
                seen.add(l)
                result.append(l)

        return result
