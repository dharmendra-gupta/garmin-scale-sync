import base64
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from src.config import settings
from src.main import app
from src.garmin_client import mfa_state, memory_logs, log_attempt
import src.garmin_client as garmin_client_module
import src.main as main_module

client = TestClient(app)


def get_basic_auth_headers():
    username = settings.API_BASIC_AUTH_USERNAME
    password = settings.API_BASIC_AUTH_PASSWORD
    auth_str = f"{username}:{password}"
    b64_auth_str = base64.b64encode(auth_str.encode()).decode()
    return {"Authorization": f"Basic {b64_auth_str}"}


def get_bearer_headers():
    return {"Authorization": f"Bearer {settings.API_BEARER_TOKEN}"}


# ---------------------------------------------------------------------------
# Dashboard (GET /) tests
# ---------------------------------------------------------------------------

def test_dashboard_requires_auth():
    """Test that the dashboard returns 401 without credentials."""
    response = client.get("/")
    assert response.status_code == 401


def test_dashboard_serves_html():
    """Test that the dashboard serves HTML when authenticated."""
    response = client.get("/", headers=get_basic_auth_headers())
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Garmin Scale Sync" in response.text


# ---------------------------------------------------------------------------
# Webhook (POST /v1/webhook/garmin) tests
# ---------------------------------------------------------------------------

def test_webhook_no_credentials_returns_403():
    """Test that webhook rejects requests with no credentials at all."""
    response = client.post("/v1/webhook/garmin", json={
        "weight": 80.0, "body_fat": 20.0, "water": 55.0,
        "bone_mass": 3.0, "lean_body_mass": 64.0
    })
    assert response.status_code == 403


def test_webhook_wrong_token_returns_401():
    """Test that webhook rejects requests with an incorrect Bearer token."""
    response = client.post(
        "/v1/webhook/garmin",
        headers={"Authorization": "Bearer wrong-token-value"},
        json={"weight": 80.0, "body_fat": 20.0, "water": 55.0,
              "bone_mass": 3.0, "lean_body_mass": 64.0}
    )
    assert response.status_code == 401


def test_webhook_missing_weight_returns_422():
    """Test that webhook validates required payload fields (missing weight)."""
    response = client.post(
        "/v1/webhook/garmin",
        headers=get_bearer_headers(),
        json={"body_fat": 20.0}
    )
    assert response.status_code == 422


def test_webhook_valid_payload_returns_201():
    """Test that webhook accepts a valid payload."""
    response = client.post(
        "/v1/webhook/garmin",
        headers=get_bearer_headers(),
        json={"weight": 80.0, "body_fat": 20.0, "water": 55.0,
              "bone_mass": 3.0, "lean_body_mass": 64.0}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "accepted"


def test_webhook_only_weight_payload_returns_201():
    """Test that webhook accepts a payload with only weight."""
    response = client.post(
        "/v1/webhook/garmin",
        headers=get_bearer_headers(),
        json={"weight": 80.0}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "accepted"


# ---------------------------------------------------------------------------
# Auth status (GET /v1/auth/status) tests
# ---------------------------------------------------------------------------

def test_status_no_credentials_returns_401():
    """Test that status endpoint rejects unauthenticated requests."""
    response = client.get("/v1/auth/status")
    assert response.status_code == 401


def test_status_wrong_basic_auth_returns_401():
    """Test that status endpoint rejects incorrect Basic Auth credentials."""
    bad_auth = base64.b64encode(b"wrong:credentials").decode()
    response = client.get(
        "/v1/auth/status",
        headers={"Authorization": f"Basic {bad_auth}"}
    )
    assert response.status_code == 401


def test_status_unauthenticated_when_no_client():
    """Test status returns 'unauthenticated' when no client is cached."""
    response = client.get("/v1/auth/status", headers=get_basic_auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "unauthenticated"


def test_status_authenticated_when_client_cached():
    """Test status returns 'authenticated' when singleton client exists."""
    garmin_client_module._garmin_client_instance = MagicMock()

    response = client.get("/v1/auth/status", headers=get_basic_auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "authenticated"


def test_status_shows_login_error_detail():
    """Test status returns 'unauthenticated' with error message when login failed."""
    main_module.login_error_detail = "Invalid credentials"

    response = client.get("/v1/auth/status", headers=get_basic_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unauthenticated"
    assert "Invalid credentials" in data["message"]


def test_status_mfa_required():
    """Test status returns 'mfa_required' when MFA is waiting."""
    mfa_state["waiting"] = True

    response = client.get("/v1/auth/status", headers=get_basic_auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "mfa_required"


# ---------------------------------------------------------------------------
# Login (POST /v1/auth/login) tests
# ---------------------------------------------------------------------------

@patch("src.main.threading.Thread")
def test_login_starts_thread_when_unauthenticated(mock_thread):
    """Test that login starts a background thread when not authenticated."""
    response = client.post("/v1/auth/login", headers=get_basic_auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "checking"
    mock_thread.return_value.start.assert_called_once()


def test_login_returns_already_authenticated():
    """Test that login returns 'success' when client is already cached."""
    garmin_client_module._garmin_client_instance = MagicMock()

    response = client.post("/v1/auth/login", headers=get_basic_auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_login_returns_mfa_required_when_waiting():
    """Test that login returns 'mfa_required' when MFA is pending."""
    mfa_state["waiting"] = True

    response = client.post("/v1/auth/login", headers=get_basic_auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "mfa_required"


# ---------------------------------------------------------------------------
# MFA (POST /v1/auth/mfa) tests
# ---------------------------------------------------------------------------

def test_mfa_submit_when_not_waiting_returns_400():
    """Test submitting MFA when not required returns 400."""
    response = client.post(
        "/v1/auth/mfa",
        headers=get_basic_auth_headers(),
        json={"code": "123456"}
    )
    assert response.status_code == 400
    assert "No active Garmin Connect MFA" in response.json()["detail"]


def test_mfa_submit_valid_code():
    """Test submitting a valid MFA code when required."""
    mfa_state["waiting"] = True
    mfa_state["event"].clear()

    response = client.post(
        "/v1/auth/mfa",
        headers=get_basic_auth_headers(),
        json={"code": "123456"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert mfa_state["code"] == "123456"
    assert mfa_state["event"].is_set()


def test_mfa_submit_whitespace_code_is_stripped():
    """Test that MFA code is stripped of surrounding whitespace."""
    mfa_state["waiting"] = True
    mfa_state["event"].clear()

    response = client.post(
        "/v1/auth/mfa",
        headers=get_basic_auth_headers(),
        json={"code": "  654321  "}
    )
    assert response.status_code == 200
    assert mfa_state["code"] == "654321"


def test_mfa_submit_empty_code():
    """Test that submitting an empty MFA code returns a 422 error."""
    mfa_state["waiting"] = True
    mfa_state["event"].clear()

    response = client.post(
        "/v1/auth/mfa",
        headers=get_basic_auth_headers(),
        json={"code": "   "}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "MFA code cannot be empty."
    assert mfa_state["code"] is None
    assert not mfa_state["event"].is_set()


# ---------------------------------------------------------------------------
# Logs (GET /v1/logs, POST /v1/logs/clear) tests
# ---------------------------------------------------------------------------

def test_get_logs_returns_empty_list_initially():
    """Test that logs endpoint returns an empty list when no logs exist."""
    response = client.get("/v1/logs", headers=get_basic_auth_headers())
    assert response.status_code == 200
    assert response.json() == []


def test_get_logs_reflects_log_entries():
    """Test that GET /v1/logs returns entries written by log_attempt (end-to-end)."""
    with patch("src.garmin_client.settings") as mock_settings:
        mock_settings.PERSIST_LOGS = False
        mock_settings.DATA_DIR = "/tmp"
        log_attempt(status="Success", payload={"weight": 75.0})
        log_attempt(status="Failed", payload={"weight": 80.0}, error_detail="timeout", http_code=504)

    response = client.get("/v1/logs", headers=get_basic_auth_headers())
    assert response.status_code == 200

    logs = response.json()
    assert len(logs) == 2
    # Most recent first (appendleft)
    assert logs[0]["status"] == "Failed"
    assert logs[0]["http_code"] == 504
    assert logs[1]["status"] == "Success"


def test_clear_logs_empties_logs():
    """Test that POST /v1/logs/clear removes all log entries."""
    memory_logs.appendleft({"status": "Success"})

    response = client.post("/v1/logs/clear", headers=get_basic_auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify logs are actually empty after clearing
    response = client.get("/v1/logs", headers=get_basic_auth_headers())
    assert response.json() == []
