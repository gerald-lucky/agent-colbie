"""
APScheduler setup for the daily 8 AM Central listing digest.

Uses a BackgroundScheduler so it runs in the same process as the Flask/Gunicorn
server. Gunicorn is started with --workers 1 --preload so the scheduler fires
exactly once (no per-worker duplication).
"""
import logging
import os

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

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
                "Good morning! Searching for today's Louisiana singlewide mobile home "
                "listings under $30,000 — I'll be back in a minute with results."
            ),
        )
    except Exception as exc:
        logger.error("Failed to post searching notice: %s", exc)

    try:
        digest = run_daily_digest()
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
