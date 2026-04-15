"""
Web fetching and search utilities for the mobile homes agent.
All functions return strings or lists — never raise, always degrade gracefully.
"""
import logging
import random
import time

import httpx
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

# Rotate through realistic browser User-Agent strings to avoid basic bot detection
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
    Fetch a URL and return cleaned, truncated page text.

    Uses BeautifulSoup to strip scripts/styles and return readable content.
    Returns an error string (never raises) so Claude can handle failures gracefully.
    """
    # Brief polite delay
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

    # Remove noise elements
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    lines = [line for line in text.splitlines() if line.strip()]
    cleaned = "\n".join(lines)

    # Cap at 12 000 chars to keep Claude token usage reasonable
    if len(cleaned) > 12_000:
        cleaned = cleaned[:12_000] + "\n\n[... page truncated ...]"

    return cleaned


def search_web(query: str, max_results: int = 10) -> list[dict]:
    """
    Search DuckDuckGo and return a list of result dicts with keys:
    title, href, body.

    Returns [] on any failure so callers never need to handle exceptions.
    """
    try:
        with DDGS() as ddgs:
            return list(
                ddgs.text(
                    query,
                    region="us-en",
                    safesearch="off",
                    max_results=max_results,
                )
            )
    except Exception as exc:
        logger.error("DuckDuckGo search failed for %r: %s", query, exc)
        return []
