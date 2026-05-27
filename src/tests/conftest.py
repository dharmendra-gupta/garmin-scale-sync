import pytest
from unittest.mock import patch
import src.garmin_client as garmin_client
import src.main as main_module


@pytest.fixture(autouse=True)
def mock_upload_to_garmin():
    """Prevent real Garmin network calls from background tasks during webhook tests."""
    with patch("src.main.upload_to_garmin"):
        yield


@pytest.fixture(autouse=True)
def reset_shared_state():
    """Reset all shared mutable state before every test to prevent bleeding."""
    # Reset MFA state
    garmin_client.mfa_state["waiting"] = False
    garmin_client.mfa_state["code"] = None
    garmin_client.mfa_state["event"].clear()

    # Reset singleton client
    garmin_client._garmin_client_instance = None

    # Reset in-memory log buffer
    garmin_client.memory_logs.clear()

    # Reset login thread state in main
    main_module.login_thread = None
    main_module.login_error_detail = None

    yield

    # Teardown — same cleanup after each test
    garmin_client.mfa_state["waiting"] = False
    garmin_client.mfa_state["code"] = None
    garmin_client.mfa_state["event"].clear()
    garmin_client._garmin_client_instance = None
    garmin_client.memory_logs.clear()
    main_module.login_thread = None
    main_module.login_error_detail = None
