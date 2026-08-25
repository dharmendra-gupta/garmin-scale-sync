import json
import logging
import os
import re
import threading
from collections import deque
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from src.config import settings
from src.garmin_session import (
    FileTokenStore,
    GarminSession,
    PostgresTokenStore,
    SqliteTokenStore,
)

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


def _build_token_store():
    """Select the shared-token backend. See Settings.TOKEN_STORE on why this
    defaults to the file store."""
    kind = (settings.TOKEN_STORE or "file").strip().lower()
    if kind == "file":
        return FileTokenStore(os.path.join(settings.DATA_DIR, ".garminconnect"))
    if kind == "sqlite":
        return SqliteTokenStore(os.path.join(settings.DATA_DIR, "garmin_tokens.db"))
    if kind == "postgres":
        if not settings.TOKEN_DB_URL:
            raise ValueError("TOKEN_STORE=postgres requires TOKEN_DB_URL to be set.")
        return PostgresTokenStore(settings.TOKEN_DB_URL)
    raise ValueError(
        f"Unknown TOKEN_STORE '{settings.TOKEN_STORE}'. Use file, sqlite, or postgres."
    )


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


# The single entry point to Garmin. Re-reads the shared token store before every
# use and republishes rotations, so a peer service refreshing the account's one
# valid refresh token no longer strands this process.
session = GarminSession(
    store=_build_token_store(),
    scratch_dir=os.path.join(settings.DATA_DIR, ".session_scratch"),
    email=settings.GARMIN_EMAIL,
    password=settings.GARMIN_PASSWORD,
    prompt_mfa=prompt_mfa_callback,
)


def log_attempt(status: str, payload: dict, error_detail: str = None, http_code: int = None):
    """Logs the results of weight upload attempts and payload validation errors."""
    timestamp = datetime.now(UTC).isoformat()

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
                    with open(LOGS_FILE, encoding="utf-8") as f:
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
                with open(LOGS_FILE, encoding="utf-8") as f:
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


def _parse_offset_tz(tz_str: str) -> timezone:
    """Parse a UTC offset string into a timezone object.

    Accepts: +05:30, +0530, -04:00, Z, UTC+05:30, UTC-04:00
    """
    offset_str = tz_str.removeprefix("UTC")
    if offset_str.upper() in ("Z", ""):
        return UTC
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


def build_timestamp(date: str, time: str, tz_str: str | None = None) -> str:
    """Combines date, time, and optional timezone into a UTC ISO 8601 timestamp.

    Garmin Connect's API interprets all timestamps as UTC, so the local time is
    converted to UTC before being sent regardless of the original timezone.
    """
    naive_dt = datetime.fromisoformat(f"{date}T{time}")
    if tz_str is None:
        aware_dt = naive_dt.replace(tzinfo=UTC)
    else:
        try:
            aware_dt = naive_dt.replace(tzinfo=ZoneInfo(tz_str))
        except ZoneInfoNotFoundError:
            aware_dt = naive_dt.replace(tzinfo=_parse_offset_tz(tz_str))
    return aware_dt.astimezone(UTC).isoformat()


def upload_to_garmin(
    weight: float,
    fat: float | None = None,
    water: float | None = None,
    bone: float | None = None,
    lean_mass: float | None = None,
    date: str | None = None,
    time: str | None = None,
    tz: str | None = None,
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

        timestamp = build_timestamp(date, time, tz) if date and time else datetime.now(UTC).isoformat()
        logger.info(f"Uploading body composition data to Garmin Connect (timestamp: {timestamp})...")

        with session.client() as client:
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
        # The session has already dropped the rejected client, so the next
        # upload rebuilds from whatever the shared store currently holds.
        logger.warning(f"Garmin session rejected: {e}. Cached client discarded.")
        log_attempt(
            status="Failed",
            payload=payload,
            error_detail=(
                "Garmin Connect session expired or credentials invalid. "
                "Please re-login on your bridge server dashboard."
            ),
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
