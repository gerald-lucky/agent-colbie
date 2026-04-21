"""
Web fetching and search utilities for the mobile homes agent.
All functions return strings or lists — never raise, always degrade gracefully.
"""
import logging
import random
import time
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
        "Gecko/20100101 Firefox/123.0"
    ),
]


def _headers() -> dict:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def fetch_url(url: str, timeout: int = 15) -> str:
    """
    Fetch a URL and return page text PLUS all hyperlinks found on the page.

    Links are appended as a structured list so Claude can see individual
    listing URLs (e.g. Craigslist post hrefs) that would otherwise be lost
    when HTML is stripped to plain text.
    """
    time.sleep(random.uniform(0.3, 1.2))

    try:
        with httpx.Client(headers=_headers(), timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning("HTTP %s for %s", exc.response.status_code, url)
        return f"[Error: HTTP {exc.response.status_code} from {url}]"
    except httpx.RequestError as exc:
        logger.warning("Request error for %s: %s", url, exc)
        return f"[Error fetching {url}: {exc}]"

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type:
        return response.text[:5000]

    soup = BeautifulSoup(response.text, "lxml")

    # Extract links BEFORE removing tags — this is what gives Claude listing URLs
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    links: list[str] = []
    seen_hrefs: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Resolve relative URLs
        if href.startswith("/"):
            href = urljoin(base, href)
        anchor = a.get_text(strip=True)
        # Keep only real http links with meaningful anchor text, deduplicated
        if (
            href.startswith("http")
            and anchor
            and len(anchor) > 4
            and href not in seen_hrefs
        ):
            seen_hrefs.add(href)
            links.append(f"  [{anchor}] → {href}")

    # Remove noise elements from text
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    lines = [line for line in text.splitlines() if line.strip()]
    cleaned = "\n".join(lines)

    if len(cleaned) > 8_000:
        cleaned = cleaned[:8_000] + "\n\n[... text truncated ...]"

    # Append extracted links (up to 60) so Claude can see all URLs on the page
    if links:
        links_section = "\n\n--- LINKS FOUND ON THIS PAGE ---\n" + "\n".join(links[:60])
        cleaned += links_section

    return cleaned


# Track last search time to enforce a minimum gap between DDG calls
_last_search_time: float = 0.0
_MIN_SEARCH_GAP = 3.0  # seconds between DuckDuckGo requests


def search_web(query: str, max_results: int = 10) -> list[dict]:
    """
    Search DuckDuckGo with rate-limit protection.
    Enforces a minimum gap between calls and retries once on failure.
    Returns [] on failure so callers never need to handle exceptions.
    """
    global _last_search_time

    # Enforce minimum gap to avoid DDG rate limits
    elapsed = time.time() - _last_search_time
    if elapsed < _MIN_SEARCH_GAP:
        time.sleep(_MIN_SEARCH_GAP - elapsed)

    for attempt in range(2):
        try:
            with DDGS() as ddgs:
                results = list(
                    ddgs.text(
                        query,
                        region="us-en",
                        safesearch="off",
                        max_results=max_results,
                    )
                )
            _last_search_time = time.time()
            return results
        except Exception as exc:
            logger.error("DuckDuckGo search failed for %r (attempt %d): %s", query, attempt + 1, exc)
            if attempt == 0:
                time.sleep(5)  # Wait before retry

    _last_search_time = time.time()
    return []
