"""
Entry point for the Colbie mobile-homes Slack bot.

Gunicorn target: main:flask_app
  gunicorn --workers 1 --threads 4 --timeout 120 --preload \
           --bind 0.0.0.0:$PORT main:flask_app

Architecture notes:
  - Flask wraps the Slack Bolt app via SlackRequestHandler
  - Bolt verifies Slack's HMAC signature on every inbound request
  - APScheduler fires the daily 8 AM listing digest in a background thread
  - Gunicorn is run with --workers 1 --preload so the scheduler starts
    exactly once in the master process
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()  # Load .env for local development; no-op in Railway (env vars are injected)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from flask import Flask, request, jsonify  # noqa: E402 — after basicConfig
from slack_bolt import App  # noqa: E402
from slack_bolt.adapter.flask import SlackRequestHandler  # noqa: E402

from bot.slack_handlers import register_handlers  # noqa: E402
from bot.scheduler import start_scheduler  # noqa: E402

# --- Slack Bolt app -----------------------------------------------------------

bolt_app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)

register_handlers(bolt_app)

# --- Flask WSGI app -----------------------------------------------------------

flask_app = Flask(__name__)
_handler = SlackRequestHandler(bolt_app)


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return _handler.handle(request)


@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# --- Scheduler ----------------------------------------------------------------
# With Gunicorn --preload, this code runs in the master process before workers
# are forked.  --workers 1 means there is only one worker, so no duplication.

start_scheduler(bolt_app)
logger.info("Colbie bot is ready.")
