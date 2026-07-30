"""
APScheduler setup for the daily 8 AM Central listing digest.

Uses a BackgroundScheduler so it runs in the same process as the Flask/Gunicorn
server. Gunicorn is started with --workers 1 --preload so the scheduler fires
exactly once (no per-worker duplication).
"""
import logging
import os
import re
import time

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Matches bare https://... URLs and Slack's <https://...|text> link format
_SLACK_LINK_RE = re.compile(r"<(https?://[^|>\s]+)[|>]")
_BARE_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _get_seen_urls(bolt_app, channel: str) -> set[str]:
    """
    Fetch the past 7 days of messages from the listings channel and return
    every URL that appears, so the daily digest can skip repeat listings.
    Uses Slack as the persistence store — survives Railway redeploys.
    """
    seen: set[str] = set()
    oldest = str(time.time() - 7 * 24 * 3600)
    cursor = None

    try:
        while True:
            kwargs: dict = {"channel": channel, "oldest": oldest, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            result = bolt_app.client.conversations_history(**kwargs)
            for msg in result.get("messages", []):
                text = msg.get("text", "")
                for m in _SLACK_LINK_RE.finditer(text):
                    seen.add(m.group(1).rstrip(".,;)"))
                for m in _BARE_URL_RE.finditer(text):
                    seen.add(m.group(0).rstrip(".,;)"))
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except Exception as exc:
        logger.warning("Could not fetch channel history for URL dedup: %s", exc)

    logger.info("Found %d seen URLs from the past 7 days in %s", len(seen), channel)
    return seen


_scheduler: BackgroundScheduler | None = None
_CENTRAL = pytz.timezone("America/Chicago")


def start_scheduler(bolt_app) -> None:
    """
    Start the background scheduler.  Safe to call multiple times — only the
    first call has any effect.
    """
    global _scheduler

    if _scheduler is not None:
        logger.info("Scheduler already running; skipping start.")
        return

    channel = os.environ.get("SLACK_LISTINGS_CHANNEL", "#mobile-homes")

    _scheduler = BackgroundScheduler(timezone=_CENTRAL)
    _scheduler.add_job(
        func=_post_daily_listings,
        trigger=CronTrigger(hour=8, minute=0, timezone=_CENTRAL),
        args=[bolt_app, channel],
        id="daily_listings",
        name="Daily Mobile Home Listings",
        replace_existing=True,
        misfire_grace_time=60 * 30,  # Allow up to 30 min late if the server was down
    )
    _scheduler.start()
    logger.info(
        "Scheduler started — daily listings at 08:00 America/Chicago → %s", channel
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


def _post_daily_listings(bolt_app, channel: str) -> None:
    """
    Fired by APScheduler at 8 AM Central every day.
    Posts a "searching" notice immediately, then posts the listing results.
    """
    # Import here to avoid circular imports at module load time
    from bot.agent import run_daily_digest

    logger.info("Daily listings job triggered for channel %s", channel)

    try:
        bolt_app.client.chat_postMessage(
            channel=channel,
            text=(
                "Good morning! Searching for today's Louisiana & Alabama singlewide mobile home "
                "listings under $20,000 — I'll be back in a minute with results."
            ),
        )
    except Exception as exc:
        logger.error("Failed to post searching notice: %s", exc)

    seen_urls = _get_seen_urls(bolt_app, channel)

    try:
        digest = run_daily_digest(seen_urls=seen_urls)
        bolt_app.client.chat_postMessage(
            channel=channel,
            text=f"*Today's Mobile Home Listings*\n\n{digest}",
        )
        logger.info("Daily listings posted successfully.")
    except Exception as exc:
        logger.error("Daily listings job failed: %s", exc, exc_info=True)
        try:
            bolt_app.client.chat_postMessage(
                channel=channel,
                text=f"Daily listings search encountered an error: {exc}",
            )
        except Exception:
            pass
