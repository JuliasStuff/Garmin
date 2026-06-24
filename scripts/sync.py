"""Pull data from Garmin Connect and write a single Firestore document.

Designed to run as a GitHub Actions job. Secrets come from environment
variables (populated from GitHub Actions Secrets):

    GARMIN_EMAIL                    Garmin Connect login email
    GARMIN_PASSWORD                 Garmin Connect login password
    FIREBASE_SERVICE_ACCOUNT_JSON   Full JSON of a Firebase Admin SDK
                                    service account (paste the file contents
                                    into the secret value)

Optional overrides:

    FIREBASE_COLLECTION             default "garminTrackers"
    GARMIN_PROFILE_ID               default "default"
    GARMIN_HISTORY_DAYS             default 365
    GARMIN_ACTIVITY_LIMIT           default 20
    GARMIN_STEP_GOAL                default 7000
    GARMIN_TZ                       IANA timezone for determining "today",
                                    e.g. "America/Los_Angeles". Defaults to
                                    "UTC". MUST be set to the user's local
                                    timezone, otherwise the GitHub Actions
                                    UTC runner will ask Garmin for tomorrow's
                                    data once it's past local-evening.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import firebase_admin
from firebase_admin import credentials, firestore
from garminconnect import Garmin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("garmin_tracker.sync")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


FIREBASE_COLLECTION = os.getenv("FIREBASE_COLLECTION", "garminTrackers")
PROFILE_ID = os.getenv("GARMIN_PROFILE_ID", "default")
HISTORY_DAYS = int(os.getenv("GARMIN_HISTORY_DAYS", "365"))
ACTIVITY_LIMIT = int(os.getenv("GARMIN_ACTIVITY_LIMIT", "20"))
STEP_GOAL = int(os.getenv("GARMIN_STEP_GOAL", "7000"))
CALORIE_GOAL = int(os.getenv("GARMIN_CALORIE_GOAL", "2300"))
INTENSITY_GOAL = int(os.getenv("GARMIN_INTENSITY_GOAL", "140"))
INTENSITY_DAYS = 7


def _user_tz() -> ZoneInfo:
    name = os.getenv("GARMIN_TZ", "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid GARMIN_TZ=%r, falling back to UTC", name)
        return ZoneInfo("UTC")


USER_TZ = _user_tz()


def _today() -> date:
    """Return today's date in the user's local timezone.

    GitHub Actions runners are on UTC, so once it's past local-evening,
    `date.today()` would point to *tomorrow* and Garmin would return empty
    stats. Always derive the calendar date from USER_TZ instead.
    """
    return datetime.now(USER_TZ).date()


def _firestore() -> firestore.Client:
    if not firebase_admin._apps:
        sa_json = _required("FIREBASE_SERVICE_ACCOUNT_JSON")
        cred = credentials.Certificate(json.loads(sa_json))
        firebase_admin.initialize_app(cred)
    return firestore.client()


def _login_garmin() -> Garmin:
    email = _required("GARMIN_EMAIL")
    password = _required("GARMIN_PASSWORD")
    client = Garmin(email=email, password=password)
    result = client.login()
    if isinstance(result, tuple) and result and result[0] == "needs_mfa":
        raise RuntimeError(
            "Garmin account requires MFA, which non-interactive sync cannot satisfy. "
            "Disable MFA on the account used for sync, or pre-generate a Garth token."
        )
    return client


def _to_min(seconds: Any) -> int | None:
    if seconds is None:
        return None
    return int(round(seconds / 60))


def _current_hr(hr_obj: dict[str, Any]) -> tuple[int | None, str | None]:
    """Return (bpm, ISO timestamp) of the most recent non-null HR sample."""
    values = hr_obj.get("heartRateValues") or []
    for entry in reversed(values):
        if not entry or len(entry) < 2:
            continue
        ts, bpm = entry[0], entry[1]
        if bpm is None or ts is None:
            continue
        try:
            iso = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
        except Exception:
            iso = None
        return int(bpm), iso
    return None, None


def _collect_today(client: Garmin) -> dict[str, Any]:
    today_str = _today().isoformat()
    stats = client.get_stats(today_str) or {}
    hr = client.get_heart_rates(today_str) or {}
    current_hr, current_hr_at = _current_hr(hr)
    payload: dict[str, Any] = {
        "date": today_str,
        "steps": stats.get("totalSteps"),
        "stepGoal": stats.get("dailyStepGoal"),
        "distanceM": stats.get("totalDistanceMeters"),
        "totalCalories": stats.get("totalKilocalories"),
        "activeCalories": stats.get("activeKilocalories"),
        "restingHR": stats.get("restingHeartRate") or hr.get("restingHeartRate"),
        "minHR": hr.get("minHeartRate"),
        "maxHR": hr.get("maxHeartRate"),
        "currentHR": current_hr,
        "currentHRAt": current_hr_at,
        "bodyBattery": stats.get("bodyBatteryMostRecentValue"),
        "stress": stats.get("averageStressLevel"),
        "floors": stats.get("floorsAscended"),
    }
    # "Has the watch uploaded anything for today?" — if all the core
    # live metrics are None, Garmin Connect hasn't received fresh data,
    # usually because the watch hasn't been near the phone with BLE on.
    has_data = any(
        payload.get(k) is not None
        for k in ("steps", "totalCalories", "currentHR", "restingHR",
                  "minHR", "maxHR")
    )
    if has_data:
        payload["dataAsOfDate"] = today_str
        payload["staleData"] = False
    else:
        payload["staleData"] = True
        logger.warning(
            "Garmin returned no today data for %s — watch likely hasn't "
            "synced to Garmin Connect yet. Preserving prior values.",
            today_str,
        )
    return payload


def _collect_sleep(client: Garmin) -> dict[str, Any]:
    # Garmin keys each sleep period to its wake date (the night that ENDED
    # on that calendar day). Querying with today's date returns "last night".
    # Try today first; if Garmin hasn't uploaded yet (can happen when the
    # workflow runs in early-UTC hours, before the user's wake-up), fall
    # back to yesterday so we still return *something* rather than empty.
    for candidate in (_today(), _today() - timedelta(days=1)):
        d_str = candidate.isoformat()
        sleep = client.get_sleep_data(d_str) or {}
        dto = sleep.get("dailySleepDTO") or {}
        if dto.get("sleepTimeSeconds"):
            score = (dto.get("sleepScores") or {}).get("overall", {}).get("value")
            return {
                "date": d_str,
                "totalMin": _to_min(dto.get("sleepTimeSeconds")),
                "deepMin": _to_min(dto.get("deepSleepSeconds")),
                "lightMin": _to_min(dto.get("lightSleepSeconds")),
                "remMin": _to_min(dto.get("remSleepSeconds")),
                "awakeMin": _to_min(dto.get("awakeSleepSeconds")),
                "score": score,
            }
    return {
        "date": _today().isoformat(),
        "totalMin": None,
        "deepMin": None,
        "lightMin": None,
        "remMin": None,
        "awakeMin": None,
        "score": None,
    }


def _collect_intensity(client: Garmin, days: int, goal: int) -> dict[str, Any]:
    """Collect today + previous (days-1) days of intensity minutes.

    Garmin's standard scoring: each moderate-intensity minute counts as 1
    and each vigorous-intensity minute counts as 2.
    """
    out_days: list[dict[str, Any]] = []
    today = _today()
    for offset in range(days):
        d = today - timedelta(days=offset)
        d_str = d.isoformat()
        try:
            im = client.get_intensity_minutes_data(d_str) or {}
        except Exception as e:
            logger.warning("Intensity fetch failed for %s: %s", d_str, e)
            im = {}
        # Newer payloads use moderateMinutes / vigorousMinutes;
        # older builds used moderateValue / vigorousValue.
        moderate = int(
            im.get("moderateMinutes") or im.get("moderateValue") or 0
        )
        vigorous = int(
            im.get("vigorousMinutes") or im.get("vigorousValue") or 0
        )
        out_days.append({
            "date": d_str,
            "moderate": moderate,
            "vigorous": vigorous,
            "minutes": moderate + 2 * vigorous,
        })
    out_days.sort(key=lambda x: x["date"])
    total = sum(d["minutes"] for d in out_days)
    return {
        "days": out_days,
        "totalMinutes": total,
        "goal": goal,
        "windowDays": days,
    }


def _collect_history(client: Garmin, days: int) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    today = _today()
    for offset in range(days):
        d = today - timedelta(days=offset)
        d_str = d.isoformat()
        try:
            s = client.get_stats(d_str) or {}
            sl = client.get_sleep_data(d_str) or {}
            dto = sl.get("dailySleepDTO") or {}
            score = (dto.get("sleepScores") or {}).get("overall", {}).get("value")
            history.append({
                "date": d_str,
                "steps": s.get("totalSteps"),
                "calories": s.get("totalKilocalories"),
                "activeCalories": s.get("activeKilocalories"),
                "restingHR": s.get("restingHeartRate"),
                "maxHR": s.get("maxHeartRate"),
                "minHR": s.get("minHeartRate"),
                "sleepMin": _to_min(dto.get("sleepTimeSeconds")),
                "sleepScore": score,
            })
        except Exception as e:
            logger.warning("History fetch failed for %s: %s", d_str, e)
    history.sort(key=lambda x: x["date"])
    return history


def _collect_activities(client: Garmin, limit: int) -> list[dict[str, Any]]:
    raw = client.get_activities(0, limit) or []
    out: list[dict[str, Any]] = []
    for a in raw:
        start_local = a.get("startTimeLocal", "") or ""
        d, _, t = start_local.partition(" ")
        out.append({
            "id": str(a.get("activityId") or ""),
            "date": d or "",
            "startTime": (t or "")[:5],
            "type": ((a.get("activityType") or {}).get("typeKey") or ""),
            "name": a.get("activityName") or "",
            "durationSec": a.get("duration"),
            "distanceM": a.get("distance"),
            "avgHR": a.get("averageHR"),
            "maxHR": a.get("maxHR"),
            "calories": a.get("calories"),
        })
    return out


def _profile(client: Garmin) -> dict[str, Any]:
    try:
        full_name = client.get_full_name()
    except Exception:
        full_name = None
    try:
        unit = client.get_unit_system()
    except Exception:
        unit = None
    return {
        "fullName": full_name,
        "unitSystem": unit,
        "goalSteps": STEP_GOAL,
        "goalCalories": CALORIE_GOAL,
    }


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None.

    Firestore's set(merge=True) writes nulls for None values, which would
    wipe out previously-good fields. Stripping Nones lets prior values
    survive when Garmin returns an empty snapshot (watch not synced).
    """
    return {k: v for k, v in d.items() if v is not None}


def run_sync() -> dict[str, Any]:
    client = _login_garmin()
    today = _collect_today(client)
    sleep = _collect_sleep(client)
    payload = {
        "profile": _profile(client),
        # Strip Nones from today/sleep so an empty Garmin response doesn't
        # overwrite previously-good values via the merge write below.
        "today": _strip_none(today),
        "sleep": _strip_none(sleep),
        "intensity": _collect_intensity(client, INTENSITY_DAYS, INTENSITY_GOAL),
        "history": _collect_history(client, HISTORY_DAYS),
        "activities": _collect_activities(client, ACTIVITY_LIMIT),
        "lastSyncIso": datetime.now(timezone.utc).isoformat(),
        "syncSourceClientId": "github-actions",
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }
    db = _firestore()
    db.collection(FIREBASE_COLLECTION).document(PROFILE_ID).set(payload, merge=True)
    return {
        "syncedAt": payload["lastSyncIso"],
        "todayDate": today.get("date"),
        "todayStale": today.get("staleData", False),
        "activitiesCount": len(payload["activities"]),
        "historyCount": len(payload["history"]),
    }


if __name__ == "__main__":
    try:
        summary = run_sync()
    except Exception:
        logger.exception("Garmin sync failed")
        sys.exit(1)
    logger.info("Garmin sync OK: %s", summary)
