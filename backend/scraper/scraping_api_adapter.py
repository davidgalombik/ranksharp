"""
ScrapingAPIAdapter — base class for adapters that use Smartproxy's
Universal Scraping API to bypass Akamai / PerimeterX bot protection.

Instead of launching a local Playwright browser (which gets blocked because
the server IP is a known datacenter address), we POST the target URL to
Smartproxy's scraping API. Their infrastructure handles:
  - Akamai Bot Manager challenges
  - PerimeterX / HUMAN challenges
  - JS rendering (headless Chrome on residential IPs)
  - CAPTCHA solving

Usage:
  1. Sign up for Smartproxy Universal Scraping API (separate product from
     residential proxy — found under Scraping → Universal Scraping API
     in the Smartproxy dashboard).
  2. Add to .env:
       SCRAPING_API_USERNAME=smart-xxxxxxxxx
       SCRAPING_API_PASSWORD=yourpassword
  3. Set REQUIRES_SCRAPING_API = True on your adapter subclass.

If credentials are not configured the adapter falls back to a direct
httpx request (useful for testing in dev environments where the target
site isn't blocked).
"""
import base64
from typing import Optional
import httpx
import structlog
from scraper.base_adapter import BaseAdapter
from config import settings

log = structlog.get_logger()

# Smartproxy Universal Scraping API endpoint (v1)
_API_URL = "https://scraper.smartproxy.org/v1/query"


class ScrapingAPIAdapter(BaseAdapter):
    """
    Extends BaseAdapter with a `_fetch_rendered(url)` helper that returns
    fully JS-rendered HTML via Smartproxy's Universal Scraping API.

    Subclasses use this instead of Playwright, dramatically simplifying the
    scraper code — just call `_fetch_rendered()` and parse the HTML with
    BeautifulSoup.
    """

    REQUIRES_SCRAPING_API: bool = True

    @property
    def _api_configured(self) -> bool:
        return bool(settings.scraping_api_username and settings.scraping_api_password)

    def _auth_header(self) -> str:
        creds = f"{settings.scraping_api_username}:{settings.scraping_api_password}"
        return "Basic " + base64.b64encode(creds.encode()).decode()

    async def _fetch_rendered(
        self,
        url: str,
        country: str = "United States",
        wait_for_selector: Optional[str] = None,
    ) -> Optional[str]:
        """
        Fetch fully JS-rendered HTML for `url`.

        Returns the rendered HTML string, or None on failure.
        Falls back to a plain httpx GET if API credentials are not configured.
        """
        if not self._api_configured:
            log.warning(
                "scraping_api_not_configured",
                url=url,
                hint="Set SCRAPING_API_USERNAME and SCRAPING_API_PASSWORD in .env",
            )
            # Fallback: plain HTTP (will likely be blocked on Akamai sites)
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            ) as client:
                try:
                    resp = await client.get(url)
                    return resp.text if resp.status_code == 200 else None
                except Exception as e:
                    log.warning("scraping_api_fallback_failed", url=url, error=str(e))
                    return None

        payload: dict = {
            "geo": "US",
            "locale": "en-US",
            "js_render": True,   # full JS rendering to bypass Akamai/PerimeterX
            "format": ["html"],
            "context": {
                "url": url,
                "source": "uni_scraper",
            },
        }
        if wait_for_selector:
            payload["context"]["wait_for_selector"] = wait_for_selector

        async with httpx.AsyncClient(timeout=120) as client:
            try:
                resp = await client.post(
                    _API_URL,
                    headers={
                        "Authorization": self._auth_header(),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    # v1 response: {"results": [{"content": "<html>...", "status_code": 200}]}
                    results = data.get("results", [])
                    if results:
                        first = results[0]
                        # content may be nested under "content" or directly as html
                        content = (
                            first.get("content")
                            or first.get("html")
                            or (first.get("results", [{}])[0].get("content") if isinstance(first.get("results"), list) else None)
                        )
                        status = first.get("status_code", 0)
                        log.info(
                            "scraping_api_fetched",
                            url=url,
                            status=status,
                            bytes=len(content or ""),
                        )
                        return content if content else None
                    log.warning("scraping_api_empty_result", url=url, response=str(data)[:300])
                    return None

                log.warning(
                    "scraping_api_error",
                    url=url,
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                return None

            except Exception as e:
                log.warning("scraping_api_exception", url=url, error=str(e))
                return None
