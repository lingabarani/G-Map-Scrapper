import asyncio
import os
from pathlib import Path
from typing import Optional

class FallbackBrowser:
    """
    Playwright headless browser fallback renderer.
    Used ONLY as a fallback when plain HTTP requests yield 0 results or are challenged.
    Caches browser binaries locally inside the project directory under .cache/ms-playwright/.
    """
    def __init__(self, cache_dir: Optional[Path] = None):
        self.project_root = Path(__file__).resolve().parent.parent.parent
        self.cache_dir = cache_dir or (self.project_root / ".cache" / "ms-playwright")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(self.cache_dir)
        self._browser = None
        self._playwright = None

    async def fetch_page_content(self, url: str) -> Optional[str]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            # Playwright is optional fallback
            return None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
                )
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                    ),
                    locale="en-US"
                )
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                content = await page.content()
                await browser.close()
                return content
        except Exception:
            return None
