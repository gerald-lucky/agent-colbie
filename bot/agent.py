"""
Claude agentic loop for the mobile homes Slack bot.

The agent receives a user message, calls Claude with tool-use enabled,
executes any tools Claude requests, and loops until Claude signals it is
done (stop_reason == "end_turn").  Prompt caching is applied to the
static system prompt and tool definitions to cut token costs.
"""
import logging
import os

import anthropic

from bot.tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


# Static system prompt — cached on first use, refreshed every 5 minutes.
_SYSTEM_PROMPT = """You are Colbie, a friendly real-estate research assistant specialising in \
affordable mobile homes in Louisiana. The user's preferred sources are VMF Homes and \
21st Mortgage repo homes — always check these first.

SEARCH STRATEGY:

STEP 1 — VMF Homes (priority source):
Fetch https://www.vmfhomes.com and look in the LINKS section for a link to repo/for-sale homes. \
Follow that link to find Louisiana listings. Individual listing URLs will appear in the LINKS section.

STEP 2 — 21st Mortgage repo homes (priority source):
Fetch https://www.21stmortgage.com and look in the LINKS section for a repo or \
"homes for sale" link. Follow it to find Louisiana listings.

STEP 3 — Craigslist (reliable fallback, fetch directly — no search needed):
  https://batonrouge.craigslist.org/search/rea?query=singlewide+mobile+home&max_price=30000
  https://shreveport.craigslist.org/search/rea?query=singlewide+mobile+home&max_price=30000
  https://lafayette.craigslist.org/search/rea?query=singlewide+mobile+home&max_price=30000
  https://lakecharles.craigslist.org/search/rea?query=singlewide+mobile+home&max_price=30000
Individual Craigslist post URLs look like: [city].craigslist.org/rea/d/[title]/[id].html — \
find them in the LINKS FOUND ON THIS PAGE section of the fetched content.

STEP 4 — web_search (use sparingly — max 2 calls total, DDG rate-limits aggressively):
Only if Steps 1-3 yield insufficient results. Good queries:
  "site:mhvillage.com singlewide louisiana for sale under 30000"

CRITICAL URL rule: every URL in your final answer must link to ONE specific home. \
Never return a search page or browse/county page. Use only individual listing URLs \
found in the LINKS sections of fetched pages.

SIZE FILTER — mandatory:
Most listings show dimensions (e.g. "16x76", "14x60", "28x56"). For every listing:
1. Find the dimensions in the listing text.
2. Multiply length × width to get square footage.
3. If square footage is over 1,550 sq ft — exclude the listing, do not include it.
4. If no dimensions are listed and you cannot determine the size — exclude the listing.
Only include listings where computed square footage is 1,550 sq ft or less.

Filter: Louisiana only, ≤ 1,550 sq ft (see above), ≤ $30,000 (or user's specified price).
For each listing: title/description, price, location, direct URL.
Format: clean Slack bullet points, no markdown headers.
Never end your response mid-task — complete all fetching before replying.
If results are genuinely scarce at the requested price, say so honestly."""


def run_agent(user_message: str, max_iterations: int = 20) -> str:
    """
    Run the agentic loop for a single user message.

    Returns the final text response from Claude, or an apology string if
    something goes wrong.
    """
    client = _get_client()
    messages: list[dict] = [{"role": "user", "content": user_message}]

    for iteration in range(max_iterations):
        logger.info("Agent iteration %d/%d", iteration + 1, max_iterations)

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        # Cache the system prompt — saves ~200 tokens per subsequent call
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=TOOL_DEFINITIONS,  # last tool entry also has cache_control
                messages=messages,
            )
        except anthropic.RateLimitError as exc:
            logger.error("Anthropic rate limit: %s", exc)
            return "I'm being rate-limited by the AI provider. Please try again in a minute."
        except anthropic.AuthenticationError as exc:
            logger.error("Anthropic auth error: %s", exc)
            return "API authentication failed — please check the ANTHROPIC_API_KEY."
        except anthropic.APIError as exc:
            logger.error("Anthropic API error (type=%s, status=%s): %s", type(exc).__name__, getattr(exc, 'status_code', 'n/a'), exc)
            return "Sorry, I hit an API error. Please try again in a moment."

        logger.info("Stop reason: %s", response.stop_reason)

        # Append Claude's full response block list to history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Extract all text blocks and join them
            text_parts = [
                block.text
                for block in response.content
                if hasattr(block, "text")
            ]
            return "\n".join(text_parts).strip() or "I found no results."

        if response.stop_reason == "tool_use":
            tool_results: list[dict] = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                logger.info("Tool call: %s(%s)", block.name, block.input)
                result_text = execute_tool(block.name, block.input)

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        else:
            logger.warning("Unexpected stop_reason: %s", response.stop_reason)
            break

    logger.warning("Agent reached max iterations (%d)", max_iterations)
    # Return whatever Claude last said, if anything
    last_texts = [
        block.text
        for block in response.content
        if hasattr(block, "text")
    ]
    return "\n".join(last_texts).strip() or "I ran out of search steps. Please try a more specific query."


def run_daily_digest() -> str:
    """
    Run the agent with a fixed prompt for the daily 8 AM listing digest.
    """
    prompt = (
        "Find today's latest singlewide mobile homes for sale in Louisiana "
        "with a maximum price of $30,000. "
        "Search MHVillage.com, Craigslist Louisiana (batonrouge, shreveport, "
        "lafayette, lakecharles, neworleans subdomains), 21st Mortgage repo homes, "
        "and VMF Homes. "
        "For each source: fetch the search results page, then find and follow the individual "
        "listing links within that page. Each result you return must be a direct link to one "
        "specific home (e.g. mhvillage.com/homes/12345 or a specific Craigslist post), "
        "NOT a link to a search or category page. "
        "Return as many individual listings as you can find (aim for 5-15). "
        "For each listing include: title/description, price, city/parish, and the direct URL. "
        "Group results by source site."
    )
    return run_agent(prompt)
