"""
Slack Bolt event handlers.

Registered onto the Bolt App in main.py.  Handles:
  - app_mention  : @Colbie in any channel the bot has been added to
  - message      : Direct messages (DMs) to the bot
"""
import logging
import re

from slack_bolt import App

from bot.agent import run_agent

logger = logging.getLogger(__name__)

_THINKING_MSG = "_Searching listings... this may take up to a minute._"

# Strip Slack mention tokens like <@U12345>
_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


def _strip_mentions(text: str) -> str:
    return _MENTION_RE.sub("", text).strip()


def _get_thread_context(client, channel: str, thread_ts: str, current_ts: str) -> str:
    """
    Fetch previous messages in a thread and return them as a context string
    so the agent remembers what was already discussed.
    Returns empty string if the thread has no prior messages or on error.
    """
    try:
        result = client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            limit=20,
        )
        messages = result.get("messages", [])
        # Exclude the current (just-sent) message
        prior = [m for m in messages if m.get("ts") != current_ts]
        if not prior:
            return ""

        lines = []
        for m in prior:
            # Label bot messages vs user messages
            if m.get("bot_id") or m.get("app_id"):
                speaker = "Colbie"
            else:
                speaker = "User"
            text = _strip_mentions(m.get("text", "")).strip()
            if text:
                lines.append(f"{speaker}: {text}")

        return "\n".join(lines)
    except Exception as exc:
        logger.warning("Could not fetch thread context: %s", exc)
        return ""


def register_handlers(app: App) -> None:

    @app.event("app_mention")
    def handle_mention(body, say, client):
        """
        User @-mentions the bot in a channel.
        Passes thread history as context so Colbie remembers the conversation.
        """
        event = body["event"]
        channel = event["channel"]
        current_ts = event["ts"]
        thread_ts = event.get("thread_ts", current_ts)

        user_text = _strip_mentions(event.get("text", ""))
        if not user_text:
            user_text = "Find me the latest singlewide mobile homes for sale in Louisiana under $30,000."

        # Build context from prior thread messages (if this is a reply in a thread)
        thread_context = ""
        if event.get("thread_ts"):
            thread_context = _get_thread_context(client, channel, thread_ts, current_ts)

        if thread_context:
            full_message = (
                f"[Previous conversation in this thread:]\n{thread_context}\n\n"
                f"[New message from user:]\n{user_text}"
            )
        else:
            full_message = user_text

        say(text=_THINKING_MSG, thread_ts=thread_ts)

        try:
            reply = run_agent(full_message)
        except Exception as exc:
            logger.error("Agent error on mention: %s", exc, exc_info=True)
            reply = "Sorry, something went wrong. Please try again in a moment."

        say(text=reply, thread_ts=thread_ts)

    @app.event("message")
    def handle_dm(body, say):
        """
        Handle direct messages (DMs) to the bot.
        Only responds to human messages in IM channels; ignores bots and subtypes.
        """
        event = body.get("event", {})

        if event.get("bot_id") or event.get("subtype"):
            return

        if event.get("channel_type") != "im":
            return

        user_text = event.get("text", "").strip()
        if not user_text:
            return

        logger.info("DM received: %r", user_text)
        say(text=_THINKING_MSG)

        try:
            reply = run_agent(user_text)
        except Exception as exc:
            logger.error("Agent error on DM: %s", exc, exc_info=True)
            reply = "Sorry, I hit an error. Please try again."

        say(text=reply)
