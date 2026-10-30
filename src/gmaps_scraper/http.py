import asyncio
import email.utils
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger("gmaps_scraper.http")

# ---------------------------------------------------------------------------
# Rotating User-Agent pool — realistic Chrome/Firefox/Edge browser strings
# ---------------------------------------------------------------------------
_UA_POOL: List[str] = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.86 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.80",
]

# ---------------------------------------------------------------------------
# Rotating accept-language pool
# ---------------------------------------------------------------------------
_LANG_POOL: List[str] = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.8",
    "en-CA,en;q=0.9,fr;q=0.7",
]

# ---------------------------------------------------------------------------
# Rotating SOCS consent cookie pool (all consent to analytics/ads)
# ---------------------------------------------------------------------------
_SOCS_POOL: List[str] = [
    "CAISHAgBEhJnd3NfMjAyNDA2MTAtMF9SQzEaAmVuIAEaBgiA_LyuBg",
    "CAISHAgBEhJnd3NfMjAyNDA1MDEtMF9SQzEaAmVuIAEaBgiAiaqvBg",
    "CAISHAgBEhJnd3NfMjAyNDA3MDEtMF9SQzEaAmVuIAEaBgiA_NmuBg",
    "CAISHAgBEhJnd3NfMjAyNDA4MDEtMF9SQzEaAmVuIAEaBgiA_OauBg",
]

# ---------------------------------------------------------------------------
# Block-detection heuristics
# ---------------------------------------------------------------------------
_BLOCK_SIGNALS = [
    "sorry/index",          # Google CAPTCHA redirect
    "recaptcha",
    "unusual traffic",
    "captcha",
    "detected unusual",
    "automated queries",
    "sorry.google.com",
    "/sorry?",
]

def _is_blocked_response(resp: httpx.Response) -> bool:
    """Returns True if Google is serving a CAPTCHA / block page."""
    if resp.status_code in (429, 503):
        return True
    # Check URL redirect to CAPTCHA
    url_str = str(resp.url).lower()
    if any(sig in url_str for sig in _BLOCK_SIGNALS):
        return True
    # Check body (only first 2 KB to keep it fast)
    try:
        body_preview = resp.text[:2048].lower()
        if any(sig in body_preview for sig in _BLOCK_SIGNALS):
            return True
    except Exception:
        pass
    return False


class HttpClient:
    """
    Async HTTP client tailored for Google Maps endpoints & reliable state collection:
    - Granular connection, read, and total request deadlines
    - Rotating User-Agent / Accept-Language / SOCS-cookie pool (auto-rotates on block)
    - Proxy pool with round-robin selection and automatic failover
    - Per-host rate-limiting semaphore with configurable concurrency
    - Exponential backoff with jitter on 429 / 5xx / timeouts / CAPTCHA detection
    - Respects HTTP 'Retry-After' headers
    - Fast-fails permanent 4xx client errors (400, 401, 403, 404, 422) without retries
    - Request counter and retry/block instrumentation
    - Consent cookie refresh on redirect
    """
    def __init__(
        self,
        user_agent: str,
        accept_language: str = "en-US,en;q=0.9",
        hl: str = "en",
        gl: str = "us",
        concurrency: int = 10,
        per_host_rate: float = 5.0,
        max_retries: int = 5,
        backoff_base: float = 1.5,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        total_timeout: float = 45.0,
        proxies: Optional[List[str]] = None,
    ):
        self.user_agent = user_agent
        self.accept_language = accept_language
        self.hl = hl
        self.gl = gl
        self.concurrency = concurrency
        self.semaphore = asyncio.Semaphore(concurrency)
        self.per_host_rate = per_host_rate
        self.delay_between_requests = 1.0 / max(per_host_rate, 0.1)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.total_timeout = total_timeout
        self.proxies = proxies or []
        self._proxy_index = 0          # round-robin pointer
        self._proxy_lock = asyncio.Lock()
        self._last_request_time = 0.0
        self._rate_limit_lock = asyncio.Lock()

        # Request counter instrumentation
        self.request_count = 0
        self.retry_count = 0
        self.error_count = 0
        self.block_count = 0           # CAPTCHA / 429 detections

        # Start with the provided UA; subsequent rotations pick from pool
        self._ua_pool = list(_UA_POOL)
        if user_agent not in self._ua_pool:
            self._ua_pool.insert(0, user_agent)

        self._clients: List[httpx.AsyncClient] = []
        self._active_client_idx = 0
        self._client_lock = asyncio.Lock()

        # Build first client
        self._clients.append(self._build_client(
            ua=user_agent,
            lang=accept_language,
            socs=random.choice(_SOCS_POOL),
            proxy=self._pick_proxy(),
        ))

    # ------------------------------------------------------------------
    def _pick_proxy(self) -> Optional[str]:
        if not self.proxies:
            return None
        idx = self._proxy_index % len(self.proxies)
        return self.proxies[idx]

    def _build_client(
        self,
        ua: str,
        lang: str,
        socs: str,
        proxy: Optional[str],
    ) -> httpx.AsyncClient:
        headers = {
            "User-Agent": ua,
            "Accept-Language": lang,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.google.com/maps/",
            "Cookie": f"CONSENT=YES+; SOCS={socs}",
        }
        timeout = httpx.Timeout(
            timeout=self.total_timeout,
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=10.0,
        )
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            http2=False,
            proxy=proxy,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        """Returns the currently active httpx client."""
        return self._clients[self._active_client_idx % len(self._clients)]

    async def _rotate_identity(self):
        """
        Rotates to a fresh httpx client with a different User-Agent, lang, cookie,
        and (if available) next proxy.  Called automatically when a block is detected.
        """
        async with self._client_lock:
            self.block_count += 1
            new_ua = random.choice(self._ua_pool)
            new_lang = random.choice(_LANG_POOL)
            new_socs = random.choice(_SOCS_POOL)

            # Advance proxy pointer
            if self.proxies:
                self._proxy_index = (self._proxy_index + 1) % len(self.proxies)
            new_proxy = self._pick_proxy()

            new_client = self._build_client(new_ua, new_lang, new_socs, new_proxy)
            self._clients.append(new_client)
            self._active_client_idx = len(self._clients) - 1

            proxy_info = f" proxy={new_proxy}" if new_proxy else " (no proxy)"
            logger.warning(
                f"[BLOCK DETECTED] Rotated identity -> UA={new_ua[:40]}...{proxy_info} "
                f"(block #{self.block_count})"
            )

    async def close(self):
        for c in self._clients:
            try:
                await c.aclose()
            except Exception:
                pass

    async def _rate_limit(self):
        async with self._rate_limit_lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            elapsed = now - self._last_request_time
            if elapsed < self.delay_between_requests:
                sleep_time = (self.delay_between_requests - elapsed) + random.uniform(0.02, 0.08)
                await asyncio.sleep(sleep_time)
            self._last_request_time = loop.time()

    def _parse_retry_after(self, header_value: Optional[str]) -> Optional[float]:
        """Parses Retry-After header (seconds or RFC 2822 date) into seconds float."""
        if not header_value:
            return None
        val = header_value.strip()
        # 1. Integer seconds
        try:
            sec = float(val)
            return max(1.0, min(sec, 120.0))
        except ValueError:
            pass
        # 2. HTTP date
        try:
            dt = email.utils.parsedate_to_datetime(val)
            now = datetime.now(timezone.utc)
            diff = (dt - now).total_seconds()
            return max(1.0, min(diff, 120.0))
        except Exception:
            return None

    async def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        attempts = 0
        last_exception: Optional[Exception] = None

        while attempts < self.max_retries:
            attempts += 1
            async with self.semaphore:
                await self._rate_limit()
                self.request_count += 1
                try:
                    resp = await self.client.get(url, params=params, headers=headers)

                    # Re-establish consent cookies if provided in Set-Cookie
                    if "set-cookie" in resp.headers:
                        for cookie_name, cookie_val in resp.cookies.items():
                            if cookie_name in ["CONSENT", "SOCS"]:
                                self.client.headers["Cookie"] = f"{cookie_name}={cookie_val}"

                    # -------------------------------------------------------
                    # Block / CAPTCHA detection — rotate identity and retry
                    # -------------------------------------------------------
                    if _is_blocked_response(resp):
                        self.retry_count += 1
                        if attempts >= self.max_retries:
                            self.error_count += 1
                            logger.error(
                                f"Blocked by Google after {attempts} attempts. Giving up on this tile."
                            )
                            return resp

                        retry_after = self._parse_retry_after(resp.headers.get("retry-after"))
                        backoff = retry_after if retry_after else (self.backoff_base ** attempts) + random.uniform(1.0, 3.0)
                        await self._rotate_identity()
                        logger.warning(f"Retrying after block in {backoff:.1f}s (attempt {attempts}/{self.max_retries})")
                        await asyncio.sleep(backoff)
                        continue

                    # Fast-fail non-retryable 4xx client errors (except 429 Rate Limit)
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        return resp

                    # Transient 5xx errors
                    if resp.status_code >= 500:
                        self.retry_count += 1
                        if attempts >= self.max_retries:
                            self.error_count += 1
                            return resp
                        backoff = (self.backoff_base ** attempts) + random.uniform(0.5, 2.0)
                        logger.warning(f"HTTP {resp.status_code}. Retrying in {backoff:.2f}s...")
                        await asyncio.sleep(backoff)
                        continue

                    return resp

                except (httpx.TimeoutException, httpx.RequestError) as ex:
                    last_exception = ex
                    self.retry_count += 1
                    if attempts >= self.max_retries:
                        self.error_count += 1
                        logger.error(f"Failed to fetch {url} after {self.max_retries} attempts: {ex}")
                        raise

                    backoff = (self.backoff_base ** attempts) + random.uniform(0.5, 2.0)
                    logger.warning(
                        f"Network timeout/error ({type(ex).__name__}) on attempt {attempts}/{self.max_retries}. "
                        f"Retrying in {backoff:.2f}s..."
                    )
                    await asyncio.sleep(backoff)

        self.error_count += 1
        if last_exception:
            raise last_exception
        raise RuntimeError(f"Failed to fetch {url} after {self.max_retries} attempts")
