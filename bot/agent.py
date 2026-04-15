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

When searching for listings:
1. Use web_search to find relevant URLs on MHVillage.com, Craigslist Louisiana subdomains \
(batonrouge, shreveport, lafayette, lakecharles, neworleans), 21st Mortgage repo homes \
(21stmortgage.com), VMF Homes (vmfhomes.com), and Zillow.
2. Use web_fetch to retrieve listing pages and extract details.
3. Filter results to Louisiana only, singlewide homes, ≤ $30,000.
4. For each listing include: description/title, price, location (city/parish), and the URL.
5. Format your final response as clean Slack-friendly text — no markdown headers, use bullet \
points and line breaks.

If a website returns an error or blocks access, move on to the next source.
Be concise: one or two sentences per listing is enough.

For general questions (not listing searches) answer helpfully using your knowledge."""


def run_agent(user_message: str, max_iterations: int = 12) -> str:
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
        except anthropic.APIError as exc:
            logger.error("Anthropic API error: %s", exc)
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
        "Return as many listings as you can find (aim for 5-15). "
        "For each listing include: title/description, price, city/parish, and the URL. "
        "Group results by source site."
    )
    return run_agent(prompt)
