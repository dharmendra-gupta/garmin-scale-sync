import os
import json
import logging
import re
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from src.config import settings

logger = logging.getLogger("garmin_scale_sync")

# Thread lock to guarantee thread-safe operations on the logs file
log_lock = threading.Lock()
LOGS_FILE = os.path.join(settings.DATA_DIR, "upload_logs.json")

# Global thread-safe state for coordinating MFA verification with the FastAPI endpoints
mfa_state = {
    "waiting": False,
    "code": None,
    "event": threading.Event()
}

# In-memory log ring buffer (maxlen=50) used when PERSIST_LOGS=False
memory_logs: deque = deque(maxlen=50)

# Singleton Garmin client — avoids a redundant token-refresh network call on every upload
_garmin_client_instance: Optional[Garmin] = None
garmin_client_lock = threading.Lock()


def prompt_mfa_callback() -> str:
    """Callback triggered by python-garminconnect if Multi-Factor Authentication is required."""
    mfa_state["waiting"] = True
    mfa_state["code"] = None
    mfa_state["event"].clear()

    logger.warning("Garmin Connect MFA requested. Waiting for user input via the dashboard UI...")

    # Block the current connection thread for up to 60 seconds waiting for /v1/auth/mfa POST input
    success = mfa_state["event"].wait(timeout=60.0)

    mfa_state["waiting"] = False
    if success and mfa_state["code"]:
        logger.info("Garmin Connect MFA code successfully received from UI. Resuming login...")
        return mfa_state["code"]
    else:
        logger.error("Garmin Connect MFA input timed out or was cancelled by the user.")
        raise Exception("MFA input timed out or was cancelled.")


def log_attempt(status: str, payload: dict, error_detail: str = None, http_code: int = None):
    """Logs the results of weight upload attempts and payload validation errors."""
    timestamp = datetime.now(timezone.utc).isoformat()

    # Standard output logging for docker logs capture
    log_msg = f"Sync Status: {status} | Payload: {payload}"
    if error_detail:
        log_msg += f" | Error: {error_detail} (HTTP {http_code})"

    if status == "Success":
        logger.info(log_msg)
    else:
        logger.error(log_msg)

    entry = {
        "timestamp": timestamp,
        "status": status,
        "payload": payload,
        "error": error_detail,
        "http_code": http_code
    }

    # Write atomically using log_lock
    with log_lock:
        # Always write to in-memory deque for the UI to read
        memory_logs.appendleft(entry)

        # Optionally persist to disk when PERSIST_LOGS=True
        if settings.PERSIST_LOGS:
            logs = []
            if os.path.exists(LOGS_FILE):
                try:
                    with open(LOGS_FILE, "r", encoding="utf-8") as f:
                        logs = json.load(f)
                except Exception as e:
                    logger.error(f"Failed to read existing logs: {e}")
                    logs = []

            # Append new log entry to the front of the list (most recent first)
            logs.insert(0, entry)

            # Ring buffer: Keep only the 100 most recent logs to optimize disk space
            logs = logs[:100]

            try:
                with open(LOGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(logs, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to write persistent logs: {e}")


def get_recent_logs() -> list:
    """Returns logs from disk (if PERSIST_LOGS) or in-memory deque (thread-safe)."""
    with log_lock:
        if settings.PERSIST_LOGS and os.path.exists(LOGS_FILE):
            try:
                with open(LOGS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to read persistent logs: {e}")
        return list(memory_logs)


def clear_recent_logs():
    """Clears the in-memory deque and, if enabled, the persistent log file."""
    with log_lock:
        memory_logs.clear()
        if settings.PERSIST_LOGS and os.path.exists(LOGS_FILE):
            try:
                with open(LOGS_FILE, "w", encoding="utf-8") as f:
                    json.dump([], f)
            except Exception as e:
                logger.error(f"Failed to clear persistent logs: {e}")


def get_garmin_client() -> Garmin:
    """Returns the cached Garmin Connect client, creating and logging in only once (thread-safe)."""
    global _garmin_client_instance
    if _garmin_client_instance is not None:
        return _garmin_client_instance

    with garmin_client_lock:
        # Double-checked locking
        if _garmin_client_instance is not None:
            return _garmin_client_instance

        logger.info("Initializing connection to Garmin Connect...")
        client = Garmin(
            email=settings.GARMIN_EMAIL,
            password=settings.GARMIN_PASSWORD,
            prompt_mfa=prompt_mfa_callback
        )
        client.login()
        _garmin_client_instance = client
        return _garmin_client_instance


def reset_garmin_client():
    """Resets the cached Garmin client singleton, forcing re-authentication on next call."""
    global _garmin_client_instance
    with garmin_client_lock:
        _garmin_client_instance = None


def _parse_offset_tz(tz_str: str) -> timezone:
    """Parse a UTC offset string into a timezone object.

    Accepts: +05:30, +0530, -04:00, Z, UTC+05:30, UTC-04:00
    """
    offset_str = tz_str.removeprefix("UTC")
    if offset_str.upper() in ("Z", ""):
        return timezone.utc
    # Normalise compact form (+0530) to colon form (+05:30)
    m = re.fullmatch(r'([+-])(\d{2})(\d{2})', offset_str)
    if m:
        sign, h, mn = m.groups()
        offset_str = f"{sign}{h}:{mn}"
    m = re.fullmatch(r'([+-])(\d{1,2}):(\d{2})', offset_str)
    if not m:
        raise ValueError(
            f"Unrecognized timezone: '{tz_str}'. Use an IANA name (e.g. 'America/New_York'), "
            "a UTC offset with or without colon (e.g. '+05:30', '+0530'), or 'Z' for UTC."
        )
    sign, h, mn = m.groups()
    total = int(h) * 60 + int(mn)
    if sign == '-':
        total = -total
    return timezone(timedelta(minutes=total))


def build_timestamp(date: str, time: str, tz_str: Optional[str] = None) -> str:
    """Combines date, time, and optional timezone into a UTC ISO 8601 timestamp.

    Garmin Connect's API interprets all timestamps as UTC, so the local time is
    converted to UTC before being sent regardless of the original timezone.
    """
    naive_dt = datetime.fromisoformat(f"{date}T{time}")
    if tz_str is None:
        aware_dt = naive_dt.replace(tzinfo=timezone.utc)
    else:
        try:
            aware_dt = naive_dt.replace(tzinfo=ZoneInfo(tz_str))
        except ZoneInfoNotFoundError:
            aware_dt = naive_dt.replace(tzinfo=_parse_offset_tz(tz_str))
    return aware_dt.astimezone(timezone.utc).isoformat()


def upload_to_garmin(
    weight: float,
    fat: Optional[float] = None,
    water: Optional[float] = None,
    bone: Optional[float] = None,
    lean_mass: Optional[float] = None,
    date: Optional[str] = None,
    time: Optional[str] = None,
    tz: Optional[str] = None,
):
    """Executes asynchronous background connection and payload push to Garmin Connect."""
    payload = {
        "weight": weight,
        "body_fat": fat,
        "water": water,
        "bone_mass": bone,
        "lean_body_mass": lean_mass
    }

    try:
        # Calculate Skeletal Muscle Mass as required by Garmin (LBM - Bone Mass) if both exist
        muscle_mass = None
        if lean_mass is not None and bone is not None:
            muscle_mass = lean_mass - bone
            logger.info(f"Target Skeletal Muscle Mass calculated: {muscle_mass:.2f}kg")
        else:
            logger.info("Muscle mass calculation skipped: missing lean mass or bone mass.")

        client = get_garmin_client()

        timestamp = build_timestamp(date, time, tz) if date and time else datetime.now(timezone.utc).isoformat()
        logger.info(f"Uploading body composition data to Garmin Connect (timestamp: {timestamp})...")
        client.add_body_composition(
            timestamp=timestamp,
            weight=weight,
            percent_fat=fat,
            percent_hydration=water,
            bone_mass=bone,
            muscle_mass=muscle_mass
        )
        logger.info("Garmin Connect upload process completed successfully.")

        log_attempt(status="Success", payload=payload)

    except GarminConnectAuthenticationError as e:
        logger.warning(f"Garmin session expired or credentials invalid: {e}. Resetting client.")
        reset_garmin_client()
        log_attempt(
            status="Failed",
            payload=payload,
            error_detail="Garmin Connect session expired or credentials invalid. Please re-login on your bridge server dashboard.",
            http_code=401
        )
    except GarminConnectTooManyRequestsError as e:
        logger.error(f"Garmin Connect rate limit hit: {e}")
        log_attempt(status="Failed", payload=payload, error_detail=str(e), http_code=429)
    except GarminConnectConnectionError as e:
        logger.error(f"Garmin Connect connection error: {e}")
        log_attempt(status="Failed", payload=payload, error_detail=str(e), http_code=503)
    except Exception as e:
        logger.error(f"Asynchronous Garmin Sync Failed: {e}", exc_info=True)
        log_attempt(status="Failed", payload=payload, error_detail=str(e), http_code=500)
