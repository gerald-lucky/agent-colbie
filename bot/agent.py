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
affordable mobile homes in Louisiana. Your job is to help users find singlewide mobile homes \
for sale in Louisiana with a maximum price of $30,000.

IMPORTANT — how to find individual listing URLs for each source:

MHVillage.com (JavaScript-rendered — do NOT fetch their search pages, they will be empty):
  - Instead use web_search with queries like:
    "site:mhvillage.com singlewide louisiana [city or parish] for sale"
  - The search results will contain direct individual listing URLs like:
    mhvillage.com/homes/[id] or mhvillage.com/listing/[id]
  - Use those URLs directly — they are individual listings.

Craigslist (server-rendered — fetching works well):
  - Use web_search to find the Craigslist Louisiana search URL, then use web_fetch on it.
  - Individual post URLs look like: [city].craigslist.org/rea/d/[title]/[id].html
  - Extract these from the fetched page content.
  - Try: batonrouge, shreveport, lafayette, lakecharles, neworleans subdomains.

21st Mortgage repo homes (21stmortgage.com) and VMF Homes (vmfhomes.com):
  - Use web_search with "site:21stmortgage.com repo louisiana singlewide" or similar.
  - Or use web_fetch on their repo/search pages and extract individual listing URLs.

CRITICAL URL rule: every URL in your final response must be a direct link to ONE specific home. \
Never return a search page, category page, or county/parish browse page as a result. \
If you cannot find individual listing URLs for a source, skip that source entirely.

Steps for each request:
1. Search or fetch to find individual listing URLs (not search pages).
2. Filter to Louisiana, singlewide, ≤ $30,000.
3. For each listing include: title/description, price, location (city/parish), direct URL.
4. Format as clean Slack-friendly text — bullet points, no markdown headers or bold.
5. If inventory under $30k is genuinely scarce, say so honestly and list what's closest.

For general questions (not listing searches) answer helpfully using your knowledge."""


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
                model="claude-sonnet-4-6",
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
