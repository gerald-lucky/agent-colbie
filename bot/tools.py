"""
Tool definitions passed to Claude and their Python implementations.
"""
import logging
from bot.scraper import fetch_url, search_web

logger = logging.getLogger(__name__)

# Tool schemas sent to the Claude API.
# cache_control on the LAST entry tells Anthropic to cache all tool
# definitions as a reusable prefix, saving tokens on every loop iteration.
TOOL_DEFINITIONS = [
    {
        "name": "web_search",
        "description": (
            "Search the web using DuckDuckGo. Use this to discover listing URLs on "
            "MHVillage, Craigslist Louisiana, 21st Mortgage repos, VMF Homes, Zillow, "
            "or any other mobile-home marketplace. Returns titles, URLs, and snippets. "
            "After finding promising URLs, call web_fetch to get the full page content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query. Example: "
                        "'singlewide mobile home Louisiana for sale under 30000 site:mhvillage.com'"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return (default 10, max 20).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": (
            "Fetch a URL and return its readable text content. Use after web_search to "
            "get listing details (price, location, description, contact). Works with "
            "MHVillage, Craigslist, 21st Mortgage, VMF Homes, and most listing pages. "
            "Returns cleaned text; large pages are truncated at 12 000 characters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to fetch.",
                },
            },
            "required": ["url"],
        },
        # Cache all tool definitions up to and including this entry
        "cache_control": {"type": "ephemeral"},
    },
]


def execute_tool(name: str, tool_input: dict) -> str:
    """
    Dispatch a tool call by name and return the string result for Claude.
    Never raises — errors are returned as strings so Claude can react.
    """
    if name == "web_search":
        query = tool_input["query"]
        max_results = int(tool_input.get("max_results", 10))
        max_results = min(max_results, 20)
        results = search_web(query, max_results=max_results)
        if not results:
            return "No results found."
        parts = []
        for r in results:
            parts.append(
                f"Title: {r.get('title', '')}\n"
                f"URL: {r.get('href', '')}\n"
                f"Snippet: {r.get('body', '')}"
            )
        return "\n\n".join(parts)

    if name == "web_fetch":
        url = tool_input["url"]
        return fetch_url(url)

    return f"[Unknown tool: {name}]"
