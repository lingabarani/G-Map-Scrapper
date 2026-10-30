import json
import logging
import re
import html
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Role mapping patterns
ROLE_PATTERNS = [
    (re.compile(r'\b(?:chief\s+executive\s+officer|ceo)\b', re.IGNORECASE), "CEO", "high"),
    (re.compile(r'\b(?:chief\s+technology\s+officer|cto)\b', re.IGNORECASE), "CTO", "high"),
    (re.compile(r'\b(?:chief\s+financial\s+officer|cfo)\b', re.IGNORECASE), "CFO", "high"),
    (re.compile(r'\b(?:chief\s+operating\s+officer|coo)\b', re.IGNORECASE), "COO", "high"),
    (re.compile(r'\b(?:president)\b', re.IGNORECASE), "President", "high"),
    (re.compile(r'\b(?:co[- ]?founder|founder)\b', re.IGNORECASE), "Founder", "high"),
    (re.compile(r'\b(?:owner|co[- ]?owner)\b', re.IGNORECASE), "Owner", "high"),
    (re.compile(r'\b(?:managing\s+director|executive\s+director)\b', re.IGNORECASE), "Director", "high"),
    (re.compile(r'\b(?:vice\s+president|vp)\b', re.IGNORECASE), "VP", "medium"),
    (re.compile(r'\b(?:director)\b', re.IGNORECASE), "Director", "medium"),
]

INVALID_NAMES: Set[str] = {
    "about us", "contact us", "our team", "leadership team", "executive team",
    "meet the team", "management team", "our mission", "our story", "our vision",
    "learn more", "read more", "read bio", "view profile", "view more",
    "follow us", "get in touch", "privacy policy", "terms of service", "home"
}

EMAIL_FALSE_POSITIVES: Set[str] = {
    "example.com", "domain.com", "sentry.io", "wixpress.com", "schema.org",
    "test.com", "sample.com", "yourcompany.com", "email.com"
}

class WebsiteExtractor:
    """
    Extracts structured company information, executive leadership, and business contacts
    from crawled website pages while strictly preserving source-page provenance.
    """

    @classmethod
    def extract(cls, business: Dict[str, Any], crawled_pages: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main extraction entrypoint given a business record and crawled pages.
        crawled_pages maps: {url: PageResponse}
        """
        source_urls = list(crawled_pages.keys())

        # 1. Company Profile Info
        profile = cls._extract_company_profile(business, crawled_pages)

        # 2. Public Leadership
        leaders = cls._extract_leadership(business, crawled_pages)

        # 3. Business Contacts
        contacts = cls._extract_contacts(business, crawled_pages, leaders)

        return {
            "profile": profile,
            "leaders": leaders,
            "contacts": contacts,
            "source_pages": source_urls
        }

    # -------------------------------------------------------------------------
    # 1. COMPANY PROFILE EXTRACTION
    # -------------------------------------------------------------------------
    @classmethod
    def _extract_company_profile(cls, business: Dict[str, Any], pages: Dict[str, Any]) -> Dict[str, Any]:
        overview: Optional[str] = None
        industry: Optional[str] = business.get("primary_type") or business.get("category")
        services: List[str] = []
        locations: List[str] = []
        contact_page_url: Optional[str] = None

        # Check existing place address for locations
        existing_address = business.get("formatted_address") or business.get("address")
        for norm_addr in cls._normalize_address(existing_address):
            if norm_addr and norm_addr not in locations:
                locations.append(norm_addr)

        for url, page in pages.items():
            html_content = page.html
            url_lower = url.lower()

            # Identify contact page
            if not contact_page_url and any(k in url_lower for k in ("contact", "get-in-touch", "reach-us")):
                contact_page_url = url

            # JSON-LD structured data extraction
            json_ld_blocks = cls._extract_json_ld(html_content)
            for block in json_ld_blocks:
                b_type = block.get("@type", "")
                if isinstance(b_type, list):
                    b_type = " ".join(str(t) for t in b_type if t)

                # Standalone PostalAddress block
                if "postaladdress" in str(b_type).lower():
                    for norm_addr in cls._normalize_address(block):
                        if norm_addr and norm_addr not in locations:
                            locations.append(norm_addr)

                if any(t in str(b_type).lower() for t in ("organization", "localbusiness", "corporation", "store", "restaurant", "bakery", "cafe", "establishment", "service")):
                    if not overview and block.get("description"):
                        overview = cls._clean_text(block["description"])
                    if not industry and block.get("knowsAbout"):
                        knows = block["knowsAbout"]
                        industry = ", ".join(str(k) for k in knows if k) if isinstance(knows, list) else str(knows)

                    # PostalAddress or nested address
                    addr = block.get("address")
                    for norm_addr in cls._normalize_address(addr):
                        if norm_addr and norm_addr not in locations:
                            locations.append(norm_addr)

                    # Also check location field if present
                    loc_val = block.get("location")
                    if loc_val:
                        for norm_addr in cls._normalize_address(loc_val):
                            if norm_addr and norm_addr not in locations:
                                locations.append(norm_addr)

            # Overview from meta tags if not already found
            if not overview:
                meta_desc = cls._extract_meta_tag(html_content, "description") or cls._extract_og_tag(html_content, "og:description")
                if meta_desc and len(meta_desc) > 30:
                    overview = meta_desc

            # Overview from about page prominent paragraph
            if not overview and ("about" in url_lower or "company" in url_lower):
                paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html_content, re.IGNORECASE | re.DOTALL)
                for p in paragraphs:
                    clean_p = cls._clean_text(p)
                    if 60 <= len(clean_p) <= 600 and not clean_p.lower().startswith(("cookie", "copyright", "all rights")):
                        overview = clean_p
                        break

            # Industry from meta keywords if still not found
            if not industry:
                keywords = cls._extract_meta_tag(html_content, "keywords")
                if keywords:
                    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
                    if kw_list:
                        industry = ", ".join(kw_list[:3])

            # Extract Services/Products
            if any(k in url_lower for k in ("service", "offering", "menu", "product", "what-we-do")):
                extracted_services = cls._extract_list_items(html_content)
                for s in extracted_services:
                    if s not in services and len(services) < 15:
                        services.append(s)

            # Locations from <address> tag
            addr_matches = re.findall(r'<address[^>]*>(.*?)</address>', html_content, re.IGNORECASE | re.DOTALL)
            for m in addr_matches:
                clean_addr = cls._clean_text(m)
                if len(clean_addr) > 10 and clean_addr not in locations:
                    locations.append(clean_addr)

        return {
            "overview": overview,
            "industry": industry,
            "services": services,
            "locations": locations,
            "contact_page_url": contact_page_url,
            "extraction_status": "completed" if (overview or services or locations) else "partial"
        }

    # -------------------------------------------------------------------------
    # 2. PUBLIC LEADERSHIP EXTRACTION
    # -------------------------------------------------------------------------
    @classmethod
    def _extract_leadership(cls, business: Dict[str, Any], pages: Dict[str, Any]) -> List[Dict[str, Any]]:
        leaders: List[Dict[str, Any]] = []
        seen_keys: Set[Tuple[str, str]] = set()

        # Prioritize pages: about, team, leadership, management, executives
        def sort_priority(item: Tuple[str, Any]) -> int:
            u = item[0].lower()
            if any(k in u for k in ("team", "leadership", "management", "executive", "people")):
                return 0
            if "about" in u:
                return 1
            return 2

        sorted_pages = sorted(pages.items(), key=sort_priority)

        for url, page in sorted_pages:
            html_content = page.html

            # A. JSON-LD Person structures
            json_ld_blocks = cls._extract_json_ld(html_content)
            for block in json_ld_blocks:
                cls._extract_leaders_from_json(block, url, leaders, seen_keys)

            # B. HTML Structural Blocks (e.g. <h3>Name</h3><p>Title</p>)
            # Pattern 1: Heading with name followed closely by title/role
            patterns = [
                # <h3>John Doe</h3> <p class="...title...">Chief Executive Officer</p>
                r'<h[2-5][^>]*>([A-Z][a-zA-Z.\s\'-]{2,40})</h[2-5]>\s*<(?:p|span|div)[^>]*class=["\'][^"\']*(?:title|role|position|job)[^"\']*["\'][^>]*>([^<]+)</(?:p|span|div)>',
                # <strong>John Doe</strong>, <em>Founder & CEO</em>
                r'<(?:strong|b)[^>]*>([A-Z][a-zA-Z.\s\'-]{2,40})</(?:strong|b)>\s*(?:,\s*|<br\s*/?>\s*)<(?:em|i|span|p)[^>]*>([^<]+)</(?:em|i|span|p)>',
                # Plain Text format: "John Doe - Founder & CEO" or "Jane Smith | President"
                r'<(?:p|div|li|h[2-5])[^>]*>([A-Z][a-zA-Z.\s\'-]{2,40})\s*(?:[-–—|]|,\s*(?=[A-Z]))\s*([^<]*(?:CEO|Chief|President|Founder|Director|VP|Executive|Owner)[^<]*)</(?:p|div|li|h[2-5])>'
            ]

            for pat in patterns:
                matches = re.findall(pat, html_content, re.IGNORECASE)
                for name_raw, title_raw in matches:
                    clean_n = cls._clean_text(name_raw)
                    clean_t = cls._clean_text(title_raw)

                    if not cls._is_valid_name(clean_n):
                        continue

                    role_type, confidence = cls._classify_role(clean_t)
                    if not role_type:
                        continue

                    leader_key = (clean_n.lower(), clean_t.lower())
                    if leader_key not in seen_keys:
                        seen_keys.add(leader_key)
                        leaders.append({
                            "full_name": clean_n,
                            "title": clean_t,
                            "role_type": role_type,
                            "biography": None,
                            "source_url": url,
                            "confidence": confidence
                        })

        return leaders

    @classmethod
    def _extract_leaders_from_json(cls, block: Any, url: str, leaders: List[Dict[str, Any]], seen_keys: Set[Tuple[str, str]]) -> None:
        if isinstance(block, list):
            for item in block:
                cls._extract_leaders_from_json(item, url, leaders, seen_keys)
            return

        if not isinstance(block, dict):
            return

        # Check @graph
        if "@graph" in block and isinstance(block["@graph"], list):
            for item in block["@graph"]:
                cls._extract_leaders_from_json(item, url, leaders, seen_keys)

        b_type = str(block.get("@type", "")).lower()
        if "person" in b_type:
            name = block.get("name")
            title = block.get("jobTitle") or block.get("hasOccupation", {}).get("name")
            bio = block.get("description")
            if name and title and isinstance(name, str) and isinstance(title, str):
                clean_n = cls._clean_text(name)
                clean_t = cls._clean_text(title)
                if cls._is_valid_name(clean_n):
                    role_type, confidence = cls._classify_role(clean_t)
                    if role_type:
                        key = (clean_n.lower(), clean_t.lower())
                        if key not in seen_keys:
                            seen_keys.add(key)
                            leaders.append({
                                "full_name": clean_n,
                                "title": clean_t,
                                "role_type": role_type,
                                "biography": cls._clean_text(bio) if bio else None,
                                "source_url": url,
                                "confidence": confidence
                            })

        # Check nested founders or employees
        for rel in ("founder", "founders", "employee", "alumni", "member"):
            if rel in block:
                cls._extract_leaders_from_json(block[rel], url, leaders, seen_keys)

    # -------------------------------------------------------------------------
    # 3. BUSINESS CONTACTS EXTRACTION
    # -------------------------------------------------------------------------
    @classmethod
    def _extract_contacts(
        cls,
        business: Dict[str, Any],
        pages: Dict[str, Any],
        leaders: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        contacts: List[Dict[str, Any]] = []
        seen_contacts: Set[Tuple[str, str]] = set()

        leader_first_names = {l["full_name"].split()[0].lower() for l in leaders if l.get("full_name")}

        # Prioritize contact, touch, about pages for contacts
        for url, page in pages.items():
            html_content = page.html

            # Strip non-text technical blocks before searching visible text
            clean_text = re.sub(r'<script[^>]*>[\s\S]*?</script>', ' ', html_content, flags=re.IGNORECASE)
            clean_text = re.sub(r'<style[^>]*>[\s\S]*?</style>', ' ', clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'<svg[^>]*>[\s\S]*?</svg>', ' ', clean_text, flags=re.IGNORECASE)
            visible_text = re.sub(r'<[^>]+>', ' ', clean_text)

            # A. Mailto links
            mailto_matches = re.findall(r'href=["\']mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})["\']', html_content, re.IGNORECASE)
            # B. Body text emails
            body_email_matches = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', visible_text)

            all_emails = set(m.lower() for m in mailto_matches + body_email_matches)
            for email in all_emails:
                if not cls._is_valid_email(email):
                    continue

                local_part = email.split("@")[0].lower()
                # Check if executive or general
                is_exec = any(fn in local_part for fn in leader_first_names)
                is_general = any(local_part.startswith(p) for p in (
                    "info", "contact", "support", "hello", "sales", "inquiries",
                    "office", "team", "hi", "help", "admin", "service", "billing"
                ))

                label = "executive" if is_exec else ("general" if is_general else "general")
                key = ("email", email)
                if key not in seen_contacts:
                    seen_contacts.add(key)
                    contacts.append({
                        "contact_type": "email",
                        "contact_value": email,
                        "label": label,
                        "source_url": url
                    })

            # C. Tel links
            tel_matches = re.findall(r'href=["\']tel:([+0-9\s().-]+)["\']', html_content, re.IGNORECASE)
            for tel in tel_matches:
                norm_phone = cls._normalize_phone(tel)
                if norm_phone:
                    key = ("phone", norm_phone)
                    if key not in seen_contacts:
                        seen_contacts.add(key)
                        contacts.append({
                            "contact_type": "phone",
                            "contact_value": norm_phone,
                            "label": "office",
                            "source_url": url
                        })

            # D. Text phone matches in visible body text
            # North American / Mexican phone patterns (NANP: area code [2-9]xx, exchange [2-9]xx)
            phone_matches = re.findall(
                r'(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?[2-9]\d{2}[-.\s]?\d{4}|\+?52[-.\s]?\(?\d{2,3}\)?[-.\s]?\d{4}[-.\s]?\d{4}',
                visible_text
            )
            for phone_str in phone_matches:
                norm_phone = cls._normalize_phone(phone_str)
                if norm_phone:
                    key = ("phone", norm_phone)
                    if key not in seen_contacts:
                        seen_contacts.add(key)
                        contacts.append({
                            "contact_type": "phone",
                            "contact_value": norm_phone,
                            "label": "office",
                            "source_url": url
                        })

        return contacts

    # -------------------------------------------------------------------------
    # HELPER UTILITIES
    # -------------------------------------------------------------------------
    @classmethod
    def _is_valid_name(cls, name: str) -> bool:
        """Validates that extracted string is a realistic person name."""
        if not name or len(name) < 3 or len(name) > 40:
            return False
        if name.lower() in INVALID_NAMES:
            return False
        # Must contain at least two words
        parts = name.split()
        if len(parts) < 2 or len(parts) > 5:
            return False
        # Avoid names containing numbers or html tokens
        if re.search(r'[0-9<>{}@/\\=_\*\+]', name):
            return False
        # Each part should be capitalized or title case
        if not all(p[0].isupper() for p in parts if p):
            return False
        return True

    @classmethod
    def _classify_role(cls, title: str) -> Tuple[Optional[str], str]:
        """Classifies a title into a role_type and confidence level."""
        for pattern, role_type, confidence in ROLE_PATTERNS:
            if pattern.search(title):
                return role_type, confidence
        return None, "low"

    @classmethod
    def _is_valid_email(cls, email: str) -> bool:
        """Filters out non-working or false-positive email strings."""
        if not email or "@" not in email:
            return False
        domain = email.split("@")[1].lower()
        if domain in EMAIL_FALSE_POSITIVES:
            return False
        # Avoid image/asset false positives like logo@2x.png
        if any(email.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            return False
        return True

    @classmethod
    def _normalize_phone(cls, phone_str: str) -> Optional[str]:
        """Normalizes and verifies telephone numbers."""
        clean = re.sub(r'[^\d+]', '', phone_str)
        if clean.startswith("+1") and len(clean) == 12:
            digits = clean[2:]
        elif clean.startswith("1") and len(clean) == 11:
            digits = clean[1:]
        elif len(clean) == 10:
            digits = clean
        elif clean.startswith("+52") and len(clean) >= 12:
            return clean
        else:
            return None

        # Filter repeated digits (e.g. 0000000000, 1111111111)
        if len(set(digits)) <= 2:
            return None
        # Format as standard (XXX) XXX-XXXX
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"

    @classmethod
    def _clean_text(cls, text: Optional[str]) -> str:
        """Strips HTML tags, unescapes entities, and collapses whitespace."""
        if not text:
            return ""
        no_html = re.sub(r'<[^>]+>', ' ', text)
        unescaped = html.unescape(no_html)
        return re.sub(r'\s+', ' ', unescaped).strip()

    @classmethod
    def _extract_meta_tag(cls, html_content: str, name: str) -> Optional[str]:
        pattern = rf'<meta[^>]+(?:name|property)=["\']{name}["\'][^>]+content=["\']([^"\']*)["\']'
        match = re.search(pattern, html_content, re.IGNORECASE)
        if match:
            return cls._clean_text(match.group(1))
        # Reverse attribute order: content first, then name
        rev_pattern = rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\']{name}["\']'
        rev_match = re.search(rev_pattern, html_content, re.IGNORECASE)
        return cls._clean_text(rev_match.group(1)) if rev_match else None

    @classmethod
    def _extract_og_tag(cls, html_content: str, property_name: str) -> Optional[str]:
        return cls._extract_meta_tag(html_content, property_name)

    @classmethod
    def _extract_json_ld(cls, html_content: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        matches = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_content, re.IGNORECASE | re.DOTALL)
        for m in matches:
            try:
                data = json.loads(m.strip())
                if isinstance(data, dict):
                    results.append(data)
                elif isinstance(data, list):
                    results.extend([d for d in data if isinstance(d, dict)])
            except Exception:
                continue
        return results

    @classmethod
    def _extract_list_items(cls, html_content: str) -> List[str]:
        items: List[str] = []
        raw_items = re.findall(r'<li[^>]*>(.*?)</li>', html_content, re.IGNORECASE | re.DOTALL)
        for it in raw_items:
            clean = cls._clean_text(it)
            if 3 <= len(clean) <= 60 and clean not in items:
                items.append(clean)
        return items

    @classmethod
    def _extract_text_field(cls, val: Any) -> Optional[str]:
        """
        Safely extracts a clean string from a string, number, dict, or list.
        Prevents returning raw dictionary string representations like {'name': 'US'}.
        """
        if val is None:
            return None
        if isinstance(val, str):
            cleaned = cls._clean_text(val)
            return cleaned if cleaned else None
        if isinstance(val, (int, float)):
            return str(val).strip() or None
        if isinstance(val, (list, tuple, set)):
            sub_strs = [s for s in (cls._extract_text_field(item) for item in val) if s]
            return ", ".join(sub_strs) if sub_strs else None
        if isinstance(val, dict):
            # Check common keys for Schema.org or general nested objects (e.g. Country, State)
            for key in ("name", "text", "@value", "value", "addressCountry", "addressRegion", "addressLocality", "identifier", "code"):
                if key in val and val[key] is not None:
                    candidate = cls._extract_text_field(val[key])
                    if candidate:
                        return candidate
            # If it's a dict that might contain street/city/state directly
            nested_parts: List[str] = []
            for sub_key in ("streetAddress", "street", "addressLocality", "city", "addressRegion", "state", "postalCode", "zip", "addressCountry", "country"):
                if sub_key in val and val[sub_key] is not None:
                    sub_val = cls._extract_text_field(val[sub_key])
                    if sub_val:
                        nested_parts.append(sub_val)
            if nested_parts:
                return ", ".join(nested_parts)

            logger.debug("Unexpected dictionary format in address field with keys: %s", list(val.keys()))
            return None

        logger.debug("Unexpected type for address field: %s", type(val).__name__)
        return None

    @classmethod
    def _normalize_address(cls, addr: Any) -> List[str]:
        """
        Safely normalizes various address formats (str, dict, list, None)
        into a list of clean, human-readable address strings.
        Handles Schema.org PostalAddress, nested Country/State objects,
        and missing or partial fields without crashing or producing raw dict strings.
        """
        if not addr:
            return []

        if isinstance(addr, str):
            cleaned = cls._clean_text(addr)
            return [cleaned] if cleaned else []

        if isinstance(addr, (list, tuple, set)):
            res: List[str] = []
            for item in addr:
                for norm in cls._normalize_address(item):
                    if norm and norm not in res:
                        res.append(norm)
            return res

        if isinstance(addr, dict):
            # If the dict contains a nested address object (e.g. {"address": {...}})
            if "address" in addr and isinstance(addr["address"], (dict, list, str)):
                nested_res = cls._normalize_address(addr["address"])
                if nested_res:
                    return nested_res

            # 1. Check for PostalAddress structured components
            street = cls._extract_text_field(
                addr.get("streetAddress") or addr.get("street") or addr.get("addressLine1") or addr.get("street_address")
            )
            extended = cls._extract_text_field(
                addr.get("addressLine2") or addr.get("extendedAddress") or addr.get("suite")
            )
            if street and extended:
                street = f"{street}, {extended}"
            elif extended:
                street = extended

            locality = cls._extract_text_field(
                addr.get("addressLocality") or addr.get("locality") or addr.get("city") or addr.get("town")
            )
            region = cls._extract_text_field(
                addr.get("addressRegion") or addr.get("region") or addr.get("state") or addr.get("province")
            )
            postal_code = cls._extract_text_field(
                addr.get("postalCode") or addr.get("postal_code") or addr.get("postcode") or addr.get("zip") or addr.get("zipcode")
            )
            country = cls._extract_text_field(
                addr.get("addressCountry") or addr.get("country") or addr.get("country_code")
            )

            parts = [street, locality, region, postal_code, country]
            valid_parts = [p.strip().rstrip(",") for p in parts if p and p.strip()]

            if valid_parts:
                # Deduplicate adjacent identical components if any
                deduped: List[str] = []
                for p in valid_parts:
                    if not deduped or p.lower() != deduped[-1].lower():
                        deduped.append(p)
                return [", ".join(deduped)]

            # 2. Check for fallback full-address keys if structured fields were missing
            for fallback_key in ("formatted_address", "full_address", "name", "text"):
                if fallback_key in addr:
                    val = cls._extract_text_field(addr[fallback_key])
                    if val:
                        return [val]

            logger.debug("Could not extract postal address from dictionary with keys: %s", list(addr.keys()))
            return []

        logger.debug("Unexpected address format type: %s", type(addr).__name__)
        return []
