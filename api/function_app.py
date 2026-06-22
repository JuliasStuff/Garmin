"""Azure Functions entry point: Garmin Connect → Firestore."""
from __future__ import annotations

import json
import logging

import azure.functions as func

from garmin_sync import run_sync

app = func.FunctionApp()
logger = logging.getLogger("garmin_tracker")


@app.timer_trigger(
    schedule="0 0 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=False,
)
def sync_timer(timer: func.TimerRequest) -> None:
    """Pull Garmin data once an hour."""
    logger.info("Timer-triggered Garmin sync starting")
    try:
        result = run_sync()
        logger.info("Timer-triggered sync OK: %s", result)
    except Exception:
        logger.exception("Timer-triggered Garmin sync failed")
        raise


@app.route(
    route="sync",
    auth_level=func.AuthLevel.FUNCTION,
    methods=["GET", "POST"],
)
def sync_http(req: func.HttpRequest) -> func.HttpResponse:
    """Manual sync triggered from the PWA."""
    logger.info("HTTP-triggered Garmin sync starting")
    try:
        result = run_sync()
        return func.HttpResponse(
            json.dumps({"ok": True, **result}),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as e:
        logger.exception("HTTP-triggered Garmin sync failed")
        return func.HttpResponse(
            json.dumps({"ok": False, "error": str(e)}),
            status_code=500,
            mimetype="application/json",
        )
