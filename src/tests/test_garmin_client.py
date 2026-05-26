import os
import json
import tempfile
import pytest
import threading
from unittest.mock import patch, MagicMock

import src.garmin_client as garmin_client_module
from src.garmin_client import (
    mfa_state, memory_logs, prompt_mfa_callback, upload_to_garmin,
    get_recent_logs, clear_recent_logs, log_attempt, reset_garmin_client
)


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

@patch("src.garmin_client.get_garmin_client")
@patch("src.garmin_client.log_attempt")
def test_upload_to_garmin_success(mock_log, mock_get_client):
    """Test that upload logic calculates muscle mass and calls Garmin API correctly."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

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


@patch("src.garmin_client.get_garmin_client")
@patch("src.garmin_client.log_attempt")
def test_upload_to_garmin_only_weight(mock_log, mock_get_client):
    """Test that upload works with only weight, leaving other fields None."""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

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


@patch("src.garmin_client.get_garmin_client")
@patch("src.garmin_client.log_attempt")
def test_upload_to_garmin_failure_defaults_to_500(mock_log, mock_get_client):
    """Test that generic exceptions log http_code=500."""
    mock_get_client.side_effect = Exception("Garmin API down")

    upload_to_garmin(weight=80.0, fat=20.0, water=55.0, bone=3.0, lean_mass=64.0)

    mock_log.assert_called_once()
    assert mock_log.call_args.kwargs["status"] == "Failed"
    assert "Garmin API down" in mock_log.call_args.kwargs["error_detail"]
    assert mock_log.call_args.kwargs["http_code"] == 500


@patch("src.garmin_client.get_garmin_client")
@patch("src.garmin_client.log_attempt")
def test_upload_extracts_http_status_code(mock_log, mock_get_client):
    """Test that HTTP status code is extracted from exceptions that carry one."""
    err = Exception("Rate limited")
    err.status = 429
    mock_get_client.side_effect = err

    upload_to_garmin(weight=80.0, fat=20.0, water=55.0, bone=3.0, lean_mass=64.0)

    assert mock_log.call_args.kwargs["http_code"] == 429


@patch("src.garmin_client.reset_garmin_client")
@patch("src.garmin_client.log_attempt")
@patch("src.garmin_client.get_garmin_client")
def test_upload_401_resets_singleton(mock_get_client, mock_log, mock_reset):
    """Test that a 401 from Garmin triggers reset_garmin_client() for re-auth."""
    err = Exception("Unauthorized")
    err.status = 401
    mock_get_client.side_effect = err

    upload_to_garmin(weight=80.0, fat=20.0, water=55.0, bone=3.0, lean_mass=64.0)

    mock_reset.assert_called_once()
    assert mock_log.call_args.kwargs["http_code"] == 401


# ---------------------------------------------------------------------------
# Singleton client tests
# ---------------------------------------------------------------------------

def test_get_garmin_client_caches_instance():
    """Test that get_garmin_client returns cached instance on subsequent calls."""
    mock_client = MagicMock()
    garmin_client_module._garmin_client_instance = mock_client

    from src.garmin_client import get_garmin_client
    result = get_garmin_client()

    assert result is mock_client


def test_reset_garmin_client_clears_singleton():
    """Test that reset_garmin_client sets the singleton to None."""
    garmin_client_module._garmin_client_instance = MagicMock()
    reset_garmin_client()
    assert garmin_client_module._garmin_client_instance is None


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
