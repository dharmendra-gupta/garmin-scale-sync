import os
import json
import tempfile
import pytest
import threading
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError, GarminConnectConnectionError
import src.garmin_client as garmin_client_module
from src.garmin_client import (
    mfa_state, memory_logs, prompt_mfa_callback, upload_to_garmin,
    get_recent_logs, clear_recent_logs, log_attempt,
    build_timestamp,
)


# ---------------------------------------------------------------------------
# build_timestamp tests
# ---------------------------------------------------------------------------

def test_build_timestamp_no_timezone_defaults_to_utc():
    """Test that omitting timezone produces a UTC ISO timestamp."""
    result = build_timestamp("2024-01-15", "08:30:00")
    assert result == "2024-01-15T08:30:00+00:00"


def test_build_timestamp_hhmm_format():
    """Test that HH:MM time (no seconds) is accepted and normalised."""
    result = build_timestamp("2024-01-15", "08:30")
    assert result == "2024-01-15T08:30:00+00:00"


def test_build_timestamp_iana_timezone():
    """Test timestamp built from an IANA timezone name."""
    result = build_timestamp("2024-01-15", "08:30:00", "UTC")
    assert result == "2024-01-15T08:30:00+00:00"


def test_build_timestamp_iana_timezone_named():
    """Test local time is correctly converted to UTC for a named IANA zone."""
    # America/New_York in January is EST (UTC-5): 08:30 local → 13:30 UTC
    result = build_timestamp("2024-01-15", "08:30:00", "America/New_York")
    assert result == "2024-01-15T13:30:00+00:00"


def test_build_timestamp_positive_utc_offset():
    """Test UTC+ offset converts to UTC correctly: 08:30+05:30 → 03:00 UTC."""
    result = build_timestamp("2024-01-15", "08:30:00", "+05:30")
    assert result == "2024-01-15T03:00:00+00:00"


def test_build_timestamp_utc_prefix_offset():
    """Test 'UTC+HH:MM' prefix is stripped and converts to UTC correctly."""
    result = build_timestamp("2024-01-15", "08:30:00", "UTC+05:30")
    assert result == "2024-01-15T03:00:00+00:00"


def test_build_timestamp_negative_utc_offset():
    """Test UTC- offset converts to UTC correctly: 08:30-05:00 → 13:30 UTC."""
    result = build_timestamp("2024-01-15", "08:30:00", "-05:00")
    assert result == "2024-01-15T13:30:00+00:00"


def test_build_timestamp_z_suffix():
    """Test that 'Z' is treated as UTC."""
    result = build_timestamp("2024-01-15", "08:30:00", "Z")
    assert result == "2024-01-15T08:30:00+00:00"


def test_build_timestamp_compact_offset():
    """Test that compact offset without colon (+0530) is accepted."""
    result = build_timestamp("2024-01-15", "08:30:00", "+0530")
    assert result == "2024-01-15T03:00:00+00:00"


def test_build_timestamp_invalid_timezone_raises():
    """Test that an unrecognised timezone string raises ValueError."""
    with pytest.raises(ValueError, match="Unrecognized timezone"):
        build_timestamp("2024-01-15", "08:30:00", "Bogus/Zone")


# ---------------------------------------------------------------------------
# MFA callback tests
# ---------------------------------------------------------------------------

def test_mfa_callback_success():
    """Test that the MFA callback waits and returns the code when set."""
    def set_code():
        mfa_state["code"] = "123456"
        mfa_state["event"].set()

    timer = threading.Timer(0.1, set_code)
    timer.start()

    code = prompt_mfa_callback()

    assert code == "123456"
    assert mfa_state["waiting"] is False


def test_mfa_callback_timeout():
    """Test that the MFA callback raises an exception on timeout."""
    with patch.object(mfa_state["event"], "wait", return_value=False):
        with pytest.raises(Exception, match="MFA input timed out or was cancelled."):
            prompt_mfa_callback()

    assert mfa_state["waiting"] is False


# ---------------------------------------------------------------------------
# upload_to_garmin tests
# ---------------------------------------------------------------------------

@contextmanager
def _session_yielding(client):
    """Stand in for GarminSession.client(), which is a context manager."""
    yield client


def _patch_session_client(client=None, side_effect=None):
    """Patch the session's client() context manager.

    Pass `client` to have the upload succeed against a mock, or `side_effect`
    to have entering the session raise.
    """
    if side_effect is not None:
        return patch.object(
            garmin_client_module.session, "client", side_effect=side_effect
        )
    return patch.object(
        garmin_client_module.session,
        "client",
        return_value=_session_yielding(client),
    )


@patch("src.garmin_client.log_attempt")
def test_upload_to_garmin_success(mock_log):
    """Test that upload logic calculates muscle mass and calls Garmin API correctly."""
    mock_client = MagicMock()

    with _patch_session_client(mock_client):
        upload_to_garmin(weight=80.0, fat=20.0, water=55.0, bone=3.0, lean_mass=64.0)

    mock_client.add_body_composition.assert_called_once()
    call_kwargs = mock_client.add_body_composition.call_args.kwargs

    assert call_kwargs["weight"] == 80.0
    assert call_kwargs["percent_fat"] == 20.0
    assert call_kwargs["percent_hydration"] == 55.0
    assert call_kwargs["bone_mass"] == 3.0
    assert call_kwargs["muscle_mass"] == 61.0  # lean_mass - bone = 64 - 3

    mock_log.assert_called_once()
    assert mock_log.call_args.kwargs["status"] == "Success"


@patch("src.garmin_client.log_attempt")
def test_upload_to_garmin_only_weight(mock_log):
    """Test that upload works with only weight, leaving other fields None."""
    mock_client = MagicMock()

    with _patch_session_client(mock_client):
        upload_to_garmin(weight=75.0)

    mock_client.add_body_composition.assert_called_once()
    call_kwargs = mock_client.add_body_composition.call_args.kwargs

    assert call_kwargs["weight"] == 75.0
    assert call_kwargs["percent_fat"] is None
    assert call_kwargs["percent_hydration"] is None
    assert call_kwargs["bone_mass"] is None
    assert call_kwargs["muscle_mass"] is None

    mock_log.assert_called_once()
    assert mock_log.call_args.kwargs["status"] == "Success"


@patch("src.garmin_client.log_attempt")
def test_upload_to_garmin_uses_provided_date_time_tz(mock_log):
    """Test that upload builds and passes the correct timestamp when date/time/tz are given."""
    mock_client = MagicMock()

    with _patch_session_client(mock_client):
        upload_to_garmin(weight=80.0, date="2024-01-15", time="08:30:00", tz="+05:30")

    call_kwargs = mock_client.add_body_composition.call_args.kwargs
    # 08:30+05:30 converted to UTC = 03:00 UTC
    assert call_kwargs["timestamp"] == "2024-01-15T03:00:00+00:00"


@patch("src.garmin_client.log_attempt")
def test_upload_to_garmin_failure_defaults_to_500(mock_log):
    """Test that generic exceptions log http_code=500."""
    with _patch_session_client(side_effect=Exception("Garmin API down")):
        upload_to_garmin(weight=80.0, fat=20.0, water=55.0, bone=3.0, lean_mass=64.0)

    mock_log.assert_called_once()
    assert mock_log.call_args.kwargs["status"] == "Failed"
    assert "Garmin API down" in mock_log.call_args.kwargs["error_detail"]
    assert mock_log.call_args.kwargs["http_code"] == 500


@patch("src.garmin_client.log_attempt")
def test_upload_rate_limit_logs_429(mock_log):
    """Test that GarminConnectTooManyRequestsError is logged with http_code 429."""
    with _patch_session_client(side_effect=GarminConnectTooManyRequestsError("Rate limited")):
        upload_to_garmin(weight=80.0, fat=20.0, water=55.0, bone=3.0, lean_mass=64.0)

    assert mock_log.call_args.kwargs["http_code"] == 429


@patch("src.garmin_client.log_attempt")
def test_upload_connection_error_logs_503(mock_log):
    """A genuine connection failure still logs 503 — it must not be mistaken
    for an auth failure now that real 401s are typed correctly."""
    with _patch_session_client(side_effect=GarminConnectConnectionError("Connection failed")):
        upload_to_garmin(weight=80.0, fat=20.0, water=55.0, bone=3.0, lean_mass=64.0)

    assert mock_log.call_args.kwargs["http_code"] == 503


@patch("src.garmin_client.log_attempt")
def test_upload_auth_error_logs_401(mock_log):
    """A rejected session logs 401. The session drops its own cached client,
    so upload_to_garmin no longer has to reset anything itself."""
    with _patch_session_client(side_effect=GarminConnectAuthenticationError("Unauthorized")):
        upload_to_garmin(weight=80.0, fat=20.0, water=55.0, bone=3.0, lean_mass=64.0)

    assert mock_log.call_args.kwargs["http_code"] == 401


# ---------------------------------------------------------------------------
# Session wiring tests
# ---------------------------------------------------------------------------

def test_session_reports_authenticated_when_client_held():
    """is_authenticated is the dashboard's authoritative signal."""
    garmin_client_module.session._client = MagicMock()
    assert garmin_client_module.session.is_authenticated is True


def test_session_invalidate_clears_cached_client():
    """invalidate() drops the client so the next use re-reads the store."""
    garmin_client_module.session._client = MagicMock()
    garmin_client_module.session._synced_blob = "{}"

    garmin_client_module.session.invalidate()

    assert garmin_client_module.session.is_authenticated is False
    assert garmin_client_module.session._synced_blob is None


# ---------------------------------------------------------------------------
# In-memory deque log tests (PERSIST_LOGS=False path)
# ---------------------------------------------------------------------------

def test_log_attempt_writes_to_memory():
    """Test that log_attempt always writes to in-memory deque."""
    with patch("src.garmin_client.settings") as mock_settings:
        mock_settings.PERSIST_LOGS = False
        mock_settings.DATA_DIR = "/tmp"
        log_attempt(status="Success", payload={"weight": 70.0})

    assert len(memory_logs) == 1
    assert memory_logs[0]["status"] == "Success"
    assert memory_logs[0]["payload"] == {"weight": 70.0}


def test_get_recent_logs_returns_memory_when_persist_false():
    """Test that get_recent_logs returns in-memory deque when PERSIST_LOGS=False."""
    entry = {"timestamp": "t", "status": "Success", "payload": {}, "error": None, "http_code": None}
    memory_logs.appendleft(entry)

    with patch("src.garmin_client.settings") as mock_settings:
        mock_settings.PERSIST_LOGS = False
        result = get_recent_logs()

    assert len(result) == 1
    assert result[0]["status"] == "Success"


def test_clear_recent_logs_empties_memory():
    """Test that clear_recent_logs empties the in-memory deque."""
    memory_logs.appendleft({"status": "Success"})
    assert len(memory_logs) > 0

    with patch("src.garmin_client.settings") as mock_settings:
        mock_settings.PERSIST_LOGS = False
        clear_recent_logs()

    assert len(memory_logs) == 0


# ---------------------------------------------------------------------------
# Persistent disk log tests (PERSIST_LOGS=True path)
# ---------------------------------------------------------------------------

def test_log_attempt_writes_to_disk_when_persist_true():
    """Test that log_attempt writes to disk file when PERSIST_LOGS=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_file = os.path.join(tmpdir, "upload_logs.json")

        with patch("src.garmin_client.settings") as mock_settings, \
             patch("src.garmin_client.LOGS_FILE", logs_file):
            mock_settings.PERSIST_LOGS = True
            mock_settings.DATA_DIR = tmpdir
            log_attempt(status="Failed", payload={"weight": 60.0}, error_detail="timeout", http_code=504)

        assert os.path.exists(logs_file)
        with open(logs_file, "r") as f:
            disk_logs = json.load(f)

        assert len(disk_logs) == 1
        assert disk_logs[0]["status"] == "Failed"
        assert disk_logs[0]["http_code"] == 504


def test_get_recent_logs_reads_disk_when_persist_true():
    """Test that get_recent_logs reads from disk when PERSIST_LOGS=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_file = os.path.join(tmpdir, "upload_logs.json")
        disk_entry = [{"timestamp": "t", "status": "Failed", "payload": {}, "error": "x", "http_code": 500}]
        with open(logs_file, "w") as f:
            json.dump(disk_entry, f)

        with patch("src.garmin_client.settings") as mock_settings, \
             patch("src.garmin_client.LOGS_FILE", logs_file):
            mock_settings.PERSIST_LOGS = True
            result = get_recent_logs()

        assert len(result) == 1
        assert result[0]["status"] == "Failed"


def test_clear_recent_logs_clears_disk_when_persist_true():
    """Test that clear_recent_logs empties the disk file when PERSIST_LOGS=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_file = os.path.join(tmpdir, "upload_logs.json")
        with open(logs_file, "w") as f:
            json.dump([{"status": "Success"}], f)

        with patch("src.garmin_client.settings") as mock_settings, \
             patch("src.garmin_client.LOGS_FILE", logs_file):
            mock_settings.PERSIST_LOGS = True
            clear_recent_logs()

        with open(logs_file, "r") as f:
            assert json.load(f) == []
