import asyncio
import random
from typing import Dict, Optional, Any, List
import httpx

class HttpClient:
    """
    Async HTTP client tailored for Google Maps endpoints:
    - Browser header emulation (UA, Accept-Language, Accept-Encoding)
    - Consent cookies ('CONSENT=YES+; SOCS=...')
    - Per-host rate-limiting semaphore
    - Exponential backoff with jitter on 429 / 5xx
    - Request counter instrumentation
    - Consent cookie refresh on redirect
    """
    def __init__(
        self,
        user_agent: str,
        accept_language: str = "en-US,en;q=0.9",
        hl: str = "en",
        gl: str = "us",
        concurrency: int = 5,
        per_host_rate: float = 2.0,
        max_retries: int = 3,
        backoff_base: float = 1.5,
        proxies: Optional[List[str]] = None,
    ):
        self.user_agent = user_agent
        self.accept_language = accept_language
        self.hl = hl
        self.gl = gl
        self.semaphore = asyncio.Semaphore(concurrency)
        self.delay_between_requests = 1.0 / max(per_host_rate, 0.1)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.proxies = proxies or []
        self._last_request_time = 0.0

        # Request counter instrumentation
        self.request_count = 0
        self.retry_count = 0
        self.error_count = 0

        headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.google.com/maps/",
            "Cookie": "CONSENT=YES+; SOCS=CAISHAgBEhJnd3NfMjAyNDA2MTAtMF9SQzEaAmVuIAEaBgiA_LyuBg",
        }
        proxy = self.proxies[0] if self.proxies else None
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
            http2=False,
            proxy=proxy
        )

    async def close(self):
        await self.client.aclose()

    async def _rate_limit(self):
        loop = asyncio.get_event_loop()
        now = loop.time()
        elapsed = now - self._last_request_time
        if elapsed < self.delay_between_requests:
            sleep_time = (self.delay_between_requests - elapsed) + random.uniform(0.05, 0.15)
            await asyncio.sleep(sleep_time)
        self._last_request_time = loop.time()

    async def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> httpx.Response:
        attempts = 0
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

                    if resp.status_code == 429 or resp.status_code >= 500:
                        self.retry_count += 1
                        # Exponential backoff with jitter
                        backoff = (self.backoff_base ** attempts) + random.uniform(0.5, 1.5)
                        await asyncio.sleep(backoff)
                        continue

                    return resp
                except (httpx.RequestError, httpx.TimeoutException):
                    self.retry_count += 1
                    if attempts >= self.max_retries:
                        self.error_count += 1
                        raise
                    backoff = (self.backoff_base ** attempts) + random.uniform(0.5, 1.5)
                    await asyncio.sleep(backoff)

        self.error_count += 1
        raise RuntimeError(f"Failed to fetch {url} after {self.max_retries} attempts")
