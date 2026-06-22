"""Pull data from Garmin Connect and write a single Firestore document.

Secrets live in Azure Key Vault and are fetched via the Function App's
system-assigned managed identity. Firebase Admin SDK is initialised from
a service-account JSON also stored in Key Vault.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import firebase_admin
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from firebase_admin import credentials, firestore
from garminconnect import Garmin

logger = logging.getLogger("garmin_tracker.sync")

KEY_VAULT_URI = os.environ["KEY_VAULT_URI"]
SECRET_GARMIN_EMAIL = os.getenv("SECRET_GARMIN_EMAIL", "garmin-email")
SECRET_GARMIN_PASSWORD = os.getenv("SECRET_GARMIN_PASSWORD", "garmin-password")
SECRET_FIREBASE_SA = os.getenv("SECRET_FIREBASE_SA", "firebase-service-account-json")
FIREBASE_COLLECTION = os.getenv("FIREBASE_COLLECTION", "garminTrackers")
PROFILE_ID = os.getenv("GARMIN_PROFILE_ID", "default")
HISTORY_DAYS = int(os.getenv("GARMIN_HISTORY_DAYS", "30"))
ACTIVITY_LIMIT = int(os.getenv("GARMIN_ACTIVITY_LIMIT", "20"))
STEP_GOAL = int(os.getenv("GARMIN_STEP_GOAL", "10000"))

# Reused across warm invocations
_secret_cache: dict[str, str] = {}
_secret_client: SecretClient | None = None
_firestore_client: firestore.Client | None = None


def _kv_client() -> SecretClient:
    global _secret_client
    if _secret_client is None:
        _secret_client = SecretClient(
            vault_url=KEY_VAULT_URI,
            credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
        )
    return _secret_client


def _get_secret(name: str) -> str:
    if name not in _secret_cache:
        _secret_cache[name] = _kv_client().get_secret(name).value
    return _secret_cache[name]


def _firestore() -> firestore.Client:
    global _firestore_client
    if _firestore_client is None:
        if not firebase_admin._apps:
            sa_json = _get_secret(SECRET_FIREBASE_SA)
            cred = credentials.Certificate(json.loads(sa_json))
            firebase_admin.initialize_app(cred)
        _firestore_client = firestore.client()
    return _firestore_client


def _login_garmin() -> Garmin:
    email = _get_secret(SECRET_GARMIN_EMAIL)
    password = _get_secret(SECRET_GARMIN_PASSWORD)
    client = Garmin(email=email, password=password)
    result = client.login()
    # garminconnect returns ("needs_mfa", state) when MFA is required.
    # We cannot prompt interactively from a background function.
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


def _collect_today(client: Garmin) -> dict[str, Any]:
    today_str = date.today().isoformat()
    stats = client.get_stats(today_str) or {}
    hr = client.get_heart_rates(today_str) or {}
    return {
        "date": today_str,
        "steps": stats.get("totalSteps"),
        "stepGoal": stats.get("dailyStepGoal"),
        "distanceM": stats.get("totalDistanceMeters"),
        "totalCalories": stats.get("totalKilocalories"),
        "activeCalories": stats.get("activeKilocalories"),
        "restingHR": stats.get("restingHeartRate") or hr.get("restingHeartRate"),
        "minHR": hr.get("minHeartRate"),
        "maxHR": hr.get("maxHeartRate"),
        "bodyBattery": stats.get("bodyBatteryMostRecentValue"),
        "stress": stats.get("averageStressLevel"),
        "floors": stats.get("floorsAscended"),
    }


def _collect_sleep(client: Garmin) -> dict[str, Any]:
    sleep_date = (date.today() - timedelta(days=1)).isoformat()
    sleep = client.get_sleep_data(sleep_date) or {}
    dto = sleep.get("dailySleepDTO") or {}
    score = (dto.get("sleepScores") or {}).get("overall", {}).get("value")
    return {
        "date": sleep_date,
        "totalMin": _to_min(dto.get("sleepTimeSeconds")),
        "deepMin": _to_min(dto.get("deepSleepSeconds")),
        "lightMin": _to_min(dto.get("lightSleepSeconds")),
        "remMin": _to_min(dto.get("remSleepSeconds")),
        "awakeMin": _to_min(dto.get("awakeSleepSeconds")),
        "score": score,
    }


def _collect_history(client: Garmin, days: int) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    today = date.today()
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
                "restingHR": s.get("restingHeartRate"),
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
    }


def run_sync() -> dict[str, Any]:
    """Run a full sync and write the result to Firestore."""
    client = _login_garmin()
    payload = {
        "profile": _profile(client),
        "today": _collect_today(client),
        "sleep": _collect_sleep(client),
        "history": _collect_history(client, HISTORY_DAYS),
        "activities": _collect_activities(client, ACTIVITY_LIMIT),
        "lastSyncIso": datetime.now(timezone.utc).isoformat(),
        "syncSourceClientId": "azure-function",
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }
    db = _firestore()
    db.collection(FIREBASE_COLLECTION).document(PROFILE_ID).set(payload, merge=True)
    return {
        "syncedAt": payload["lastSyncIso"],
        "todayDate": payload["today"].get("date"),
        "activitiesCount": len(payload["activities"]),
        "historyCount": len(payload["history"]),
    }
