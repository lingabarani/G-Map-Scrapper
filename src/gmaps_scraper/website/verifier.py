import asyncio
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from .ssrf import is_safe_url, normalize_url

DIRECTORY_AND_SOCIAL_DOMAINS: Set[str] = {
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "yelp.com", "yellowpages.com", "tripadvisor.com",
    "foursquare.com", "mapquest.com", "bbb.org", "tiktok.com",
    "youtube.com", "pinterest.com", "wikipedia.org"
}

CORPORATE_SUFFIXES: Set[str] = {
    "inc", "incorporated", "llc", "corp", "corporation", "ltd", "limited",
    "co", "company", "sa", "s.a.", "de", "cv", "c.v.", "sapi", "s.a.p.i."
}

def clean_tokens(text: Optional[str]) -> List[str]:
    """Extracts normalized alphanumeric word tokens of length >= 2, removing corporate suffixes."""
    if not text:
        return []
    words = re.findall(r'[a-zA-Z0-9]+', text.lower())
    return [w for w in words if len(w) >= 2 and w not in CORPORATE_SUFFIXES]

def extract_domain_stem(netloc: str) -> str:
    """Extracts core domain stem from netloc (e.g. 'www.cafegrumpy.com' -> 'cafegrumpy')."""
    host = netloc.lower().split(":")[0]
    parts = host.split(".")
    if len(parts) >= 2:
        # Avoid 'co.uk', 'com.mx'
        if parts[-2] in ("co", "com", "org", "net", "gob", "edu") and len(parts) >= 3:
            return parts[-3]
        return parts[-2]
    return host

def is_directory_or_social(netloc: str) -> bool:
    host = netloc.lower().split(":")[0]
    for domain in DIRECTORY_AND_SOCIAL_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return False

class WebsiteVerifier:
    def __init__(
        self,
        timeout_seconds: float = 10.0,
        max_redirects: int = 5,
        user_agent: Optional[str] = None,
        check_dns: bool = True
    ):
        self.timeout = timeout_seconds
        self.max_redirects = max_redirects
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        self.check_dns = check_dns

    async def verify(
        self,
        candidate_url: str,
        business: Dict[str, Any],
        http_client: Optional[httpx.AsyncClient] = None
    ) -> Dict[str, Any]:
        """
        Verifies a candidate website URL for a business record using multi-signal evidence.
        Enforces SSRF prevention on initial URL and on all redirect hops.
        """
        norm_url = normalize_url(candidate_url)
        safe, ssrf_err = is_safe_url(norm_url, check_dns=self.check_dns)
        if not safe:
            return {
                "status": "failed",
                "verified": False,
                "candidate": candidate_url,
                "final_url": None,
                "error": f"SSRF Check Failed: {ssrf_err}",
                "evidence": {
                    "reason": f"Destination URL blocked by SSRF safeguard: {ssrf_err}",
                    "is_safe": False
                }
            }

        # Check if directory or social
        parsed_init = urllib.parse.urlsplit(norm_url)
        if is_directory_or_social(parsed_init.netloc):
            return {
                "status": "needs_review",
                "verified": False,
                "candidate": candidate_url,
                "final_url": norm_url,
                "error": None,
                "evidence": {
                    "is_directory_or_social": True,
                    "domain": parsed_init.netloc,
                    "reason": f"URL belongs to third-party directory/social platform ({parsed_init.netloc}), not official company website"
                }
            }

        client_provided = http_client is not None
        client = http_client or httpx.AsyncClient(
            headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"},
            timeout=self.timeout,
            follow_redirects=False,
            verify=True
        )

        try:
            current_url = norm_url
            redirects_followed = 0
            response = None

            while redirects_followed <= self.max_redirects:
                try:
                    resp = await client.get(current_url)
                except httpx.TimeoutException:
                    return {
                        "status": "failed",
                        "verified": False,
                        "candidate": candidate_url,
                        "final_url": current_url,
                        "error": "HTTP Connection Timed Out",
                        "evidence": {"reason": f"Request timed out after {self.timeout}s"}
                    }
                except Exception as e:
                    return {
                        "status": "failed",
                        "verified": False,
                        "candidate": candidate_url,
                        "final_url": current_url,
                        "error": f"Connection error: {e}",
                        "evidence": {"reason": f"Failed to connect to website: {e}"}
                    }

                # Handle redirect
                if resp.is_redirect and "location" in resp.headers:
                    redirects_followed += 1
                    loc = resp.headers["location"]
                    next_url = urllib.parse.urljoin(current_url, loc)
                    safe_next, next_err = is_safe_url(next_url, check_dns=self.check_dns)
                    if not safe_next:
                        return {
                            "status": "failed",
                            "verified": False,
                            "candidate": candidate_url,
                            "final_url": next_url,
                            "error": f"SSRF blocked on redirect: {next_err}",
                            "evidence": {"reason": f"Redirect to unsafe destination blocked: {next_err}"}
                        }
                    current_url = next_url
                    continue
                else:
                    response = resp
                    break

            if response is None:
                return {
                    "status": "failed",
                    "verified": False,
                    "candidate": candidate_url,
                    "final_url": current_url,
                    "error": "Too many redirects",
                    "evidence": {"reason": f"Exceeded maximum redirects ({self.max_redirects})"}
                }

            if response.status_code >= 400:
                return {
                    "status": "failed",
                    "verified": False,
                    "candidate": candidate_url,
                    "final_url": str(response.url),
                    "error": f"HTTP {response.status_code}",
                    "evidence": {
                        "status_code": response.status_code,
                        "reason": f"Website returned HTTP error status {response.status_code}"
                    }
                }

            # Extract signals from HTML body
            html_text = response.text or ""
            parsed_final = urllib.parse.urlsplit(str(response.url))
            final_netloc = parsed_final.netloc.lower()

            # Signal 1: Title & Headings
            title_match = re.search(r'<title[^>]*>(.*?)</title>', html_text, re.IGNORECASE | re.DOTALL)
            title_text = title_match.group(1).strip() if title_match else ""
            h_tags = re.findall(r'<h[1-3][^>]*>(.*?)</h[1-3]>', html_text, re.IGNORECASE | re.DOTALL)
            h_text = " ".join(re.sub(r'<[^>]+>', ' ', h) for h in h_tags)

            # Signal 2: Meta description
            meta_desc_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', html_text, re.IGNORECASE)
            meta_text = meta_desc_match.group(1).strip() if meta_desc_match else ""

            # Name matching
            biz_name = business.get("name") or ""
            biz_tokens = clean_tokens(biz_name)
            domain_stem = extract_domain_stem(final_netloc)

            matched_in_domain = False
            if biz_tokens:
                # Check if business name is contained in domain
                compact_biz = "".join(biz_tokens)
                if compact_biz in domain_stem or domain_stem in compact_biz:
                    matched_in_domain = True
                elif any(t in domain_stem for t in biz_tokens if len(t) >= 4):
                    matched_in_domain = True

            combined_page_text = f"{title_text} {h_text} {meta_text} {html_text[:5000]}".lower()
            matching_tokens = [t for t in biz_tokens if t in combined_page_text]
            token_ratio = len(matching_tokens) / len(biz_tokens) if biz_tokens else 0.0

            # Signal 3: Phone matching
            phone_match = False
            phones_to_check = [business.get("phone"), business.get("phone_e164")]
            for raw_phone in phones_to_check:
                if raw_phone:
                    digits = re.sub(r'\D', '', str(raw_phone))
                    if len(digits) >= 7:
                        # Check last 7 or 10 digits in HTML
                        if digits[-7:] in re.sub(r'\D', '', html_text) or digits[-10:] in re.sub(r'\D', '', html_text):
                            phone_match = True
                            break

            # Signal 4: Location matching
            location_match = False
            city = business.get("city")
            region = business.get("region")
            postal_code = business.get("postal_code")
            if city and city.lower() in combined_page_text:
                location_match = True
            elif region and region.lower() in combined_page_text:
                location_match = True
            elif postal_code and postal_code in combined_page_text:
                location_match = True

            # Signal 5: Category matching
            category_match = False
            cat = business.get("category")
            if cat:
                cat_tokens = clean_tokens(cat)
                if any(ct in combined_page_text for ct in cat_tokens):
                    category_match = True

            # Decision logic
            confidence_score = 0.0
            if matched_in_domain:
                confidence_score += 0.50
            confidence_score += (token_ratio * 0.35)
            if phone_match:
                confidence_score += 0.30
            if location_match:
                confidence_score += 0.15
            if category_match:
                confidence_score += 0.10

            confidence_score = min(1.0, round(confidence_score, 2))

            evidence = {
                "status_code": response.status_code,
                "final_url": str(response.url),
                "domain": final_netloc,
                "domain_stem": domain_stem,
                "title": title_text[:120],
                "matched_in_domain": matched_in_domain,
                "token_match_ratio": round(token_ratio, 2),
                "matching_tokens": matching_tokens,
                "phone_match": phone_match,
                "location_match": location_match,
                "category_match": category_match,
                "confidence_score": confidence_score,
            }

            if matched_in_domain and (token_ratio >= 0.5 or phone_match or location_match or category_match):
                evidence["reason"] = "Verified: Official domain stem matches business name and verified by supporting signals"
                return {
                    "status": "verified",
                    "verified": True,
                    "candidate": candidate_url,
                    "final_url": str(response.url),
                    "evidence": evidence,
                    "error": None
                }
            elif confidence_score >= 0.70:
                evidence["reason"] = "Verified: High confidence multi-signal match across name, location, and contact details"
                return {
                    "status": "verified",
                    "verified": True,
                    "candidate": candidate_url,
                    "final_url": str(response.url),
                    "evidence": evidence,
                    "error": None
                }
            elif confidence_score >= 0.35 or token_ratio >= 0.40:
                evidence["reason"] = "Needs Review: Ambiguous or partial match; requires human verification"
                return {
                    "status": "needs_review",
                    "verified": False,
                    "candidate": candidate_url,
                    "final_url": str(response.url),
                    "evidence": evidence,
                    "error": None
                }
            else:
                evidence["reason"] = "Needs Review: Content and domain mismatch with business entity"
                return {
                    "status": "needs_review",
                    "verified": False,
                    "candidate": candidate_url,
                    "final_url": str(response.url),
                    "evidence": evidence,
                    "error": "Mismatched company content or domain"
                }

        finally:
            if not client_provided:
                await client.aclose()
