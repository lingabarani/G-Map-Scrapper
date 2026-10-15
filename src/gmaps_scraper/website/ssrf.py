import ipaddress
import socket
import urllib.parse
from typing import Optional, Set, Tuple

# Disallowed internal/metadata hostnames
BLOCKED_HOSTNAMES: Set[str] = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "169.254.169.254",
    "instance-data",
}

# Dangerous / administrative internal ports
BLOCKED_PORTS: Set[int] = {
    21, 22, 23, 25, 53, 110, 111, 135, 137, 138, 139, 143, 445,
    1433, 1521, 2049, 2375, 2376, 3306, 3389, 5432, 5900, 6379,
    8000, 8086, 8500, 9000, 9200, 9300, 11211, 27017, 28017
}

def is_safe_url(url: str, check_dns: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Validates that a URL is safe to fetch, strictly blocking:
    - Non-HTTP(S) schemes (file, gopher, ftp, etc.)
    - Localhost and loopback addresses
    - Private IPv4 & IPv6 ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7)
    - Link-local and cloud metadata addresses (169.254.0.0/16, fe80::/10)
    - Non-standard/administrative internal service ports
    """
    if not url or not isinstance(url, str):
        return False, "URL is empty or invalid"

    cleaned = url.strip()
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except Exception as e:
        return False, f"Malformed URL: {e}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"Disallowed scheme '{scheme}'. Only HTTP and HTTPS are permitted."

    hostname = (parsed.hostname or "").lower().strip("[]")
    if not hostname:
        return False, "Missing hostname in URL"

    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
        return False, f"Hostname '{hostname}' resolves to a local/internal destination"

    # Check port if specified
    if parsed.port and parsed.port in BLOCKED_PORTS:
        return False, f"Port {parsed.port} is blocked for security"

    # Immediate IP literal check
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or not ip.is_global:
            return False, f"IP literal '{hostname}' is not a public, globally routable address"
    except ValueError:
        # Hostname is a domain name, not an IP literal
        pass

    # DNS resolution check
    if check_dns:
        try:
            # Resolve both IPv4 and IPv6
            addr_info = socket.getaddrinfo(hostname, parsed.port or (443 if scheme == "https" else 80), proto=socket.IPPROTO_TCP)
            if not addr_info:
                return False, f"Could not resolve hostname '{hostname}'"

            for item in addr_info:
                sockaddr = item[4]
                ip_str = sockaddr[0]
                ip = ipaddress.ip_address(ip_str)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or not ip.is_global:
                    return False, f"Hostname '{hostname}' resolved to private/unsafe IP {ip_str}"
        except socket.gaierror as e:
            return False, f"DNS resolution failed for '{hostname}': {e}"
        except Exception as e:
            return False, f"Error validating address for '{hostname}': {e}"

    return True, None

def normalize_url(url: str, prefer_https: bool = True) -> str:
    """Normalizes and canonicalizes a URL, stripping tracking parameters."""
    if not url:
        return ""
    u = url.strip()
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "https://" + u

    try:
        parsed = urllib.parse.urlsplit(u)
        scheme = "https" if prefer_https else parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Filter tracking query parameters
        qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered_qs = [
            (k, v) for k, v in qs
            if not k.lower().startswith("utm_")
            and k.lower() not in {"ref", "fbclid", "gclid", "campaign", "source"}
        ]
        new_query = urllib.parse.urlencode(filtered_qs)
        path = parsed.path or "/"

        return urllib.parse.urlunsplit((scheme, netloc, path, new_query, ""))
    except Exception:
        return u
