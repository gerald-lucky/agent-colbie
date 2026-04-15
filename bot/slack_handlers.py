"""
Slack Bolt event handlers.

Registered onto the Bolt App in main.py.  Handles:
  - app_mention  : @Colbie in any channel the bot has been added to
  - message      : Direct messages (DMs) to the bot
"""
import logging

from slack_bolt import App

from bot.agent import run_agent

logger = logging.getLogger(__name__)

_THINKING_MSG = "_Searching listings... this may take up to a minute._"


def register_handlers(app: App) -> None:

    @app.event("app_mention")
    def handle_mention(body, say):
        """
        User @-mentions the bot in a channel.
        Strips the mention token and passes the rest to the agent.
        """
        event = body["event"]
        raw_text: str = event.get("text", "")

        # Slack encodes the mention as "<@UXXXXXXXX> rest of message"
        # Strip everything up to and including the first ">"
        if ">" in raw_text:
            user_text = raw_text.split(">", 1)[1].strip()
        else:
            user_text = raw_text.strip()

        if not user_text:
            user_text = "Find me the latest singlewide mobile homes for sale in Louisiana under $30,000."

        thread_ts = event.get("thread_ts", event["ts"])

        # Acknowledge immediately so the user sees feedback
        say(text=_THINKING_MSG, thread_ts=thread_ts)

        try:
            reply = run_agent(user_text)
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

        # Ignore bot messages and edited/deleted subtypes
        if event.get("bot_id") or event.get("subtype"):
            return

        # Only handle DMs (channel_type == "im")
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
