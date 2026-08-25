import os
import json
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator
from src.config import settings
from garminconnect import GarminConnectAuthenticationError
from src.garmin_client import (
    upload_to_garmin, session, mfa_state, log_attempt,
    get_recent_logs, clear_recent_logs
)
import src.garmin_client as garmin_client

# Logging Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("garmin_scale_sync")

# Global state for asynchronous login thread monitoring
login_thread = None
login_error_detail = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup: restore the Garmin session from the shared token store."""
    global login_thread
    if settings.DRY_RUN:
        logger.info("Dry-run mode enabled — Garmin Connect login skipped.")
    elif session.has_stored_tokens():
        logger.info("Shared Garmin token store populated — restoring session in background.")
        login_thread = threading.Thread(target=run_login_in_background, daemon=True)
        login_thread.start()
    yield


app = FastAPI(title="GarminScaleSync", version="1.0.0", lifespan=lifespan)

# Security schemes
security_bearer = HTTPBearer()
security_basic = HTTPBasic()

# Input schemas
class BodyCompositionPayload(BaseModel):
    weight: float = Field(..., description="Weight in kg")
    body_fat: Optional[float] = Field(None, description="Body fat percentage in %")
    water: Optional[float] = Field(None, description="Body hydration percentage in %")
    bone_mass: Optional[float] = Field(None, description="Bone mass in kg")
    lean_body_mass: Optional[float] = Field(None, description="Lean body mass in kg")
    date: Optional[str] = Field(None, description="Measurement date in YYYY-MM-DD format")
    time: Optional[str] = Field(None, description="Measurement time in HH:MM or HH:MM:SS format")
    timezone: Optional[str] = Field(None, description="Timezone as IANA name (e.g. 'America/New_York') or UTC offset (e.g. '+05:30')")

    @field_validator('date')
    @classmethod
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            try:
                datetime.strptime(v, '%Y-%m-%d')
            except ValueError:
                raise ValueError("Date must be in YYYY-MM-DD format.")
        return v

    @field_validator('time')
    @classmethod
    def validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            for fmt in ('%H:%M:%S', '%H:%M'):
                try:
                    datetime.strptime(v, fmt)
                    return v
                except ValueError:
                    continue
            raise ValueError("Time must be in HH:MM or HH:MM:SS format.")
        return v

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            import re
            try:
                ZoneInfo(v)
                return v
            except ZoneInfoNotFoundError:
                pass
            offset_str = v.removeprefix("UTC")
            # Accept Z / empty (UTC)
            if offset_str.upper() in ("Z", ""):
                return v
            # Accept compact form: +0530
            if re.fullmatch(r'[+-]\d{4}', offset_str):
                return v
            # Accept colon form: +05:30
            if re.fullmatch(r'[+-]\d{1,2}:\d{2}', offset_str):
                return v
            raise ValueError(
                f"Unrecognized timezone '{v}'. Use an IANA name (e.g. 'America/New_York'), "
                "a UTC offset (e.g. '+05:30', '+0530'), or 'Z' for UTC."
            )
        return v

    @model_validator(mode='after')
    def validate_datetime_fields(self) -> 'BodyCompositionPayload':
        if (self.date is None) != (self.time is None):
            raise ValueError("Both 'date' and 'time' must be provided together, or neither.")
        if self.timezone is not None and self.date is None:
            raise ValueError("'timezone' requires 'date' and 'time' to be provided.")
        return self

class MFAPayload(BaseModel):
    code: str = Field(..., description="6-digit Multi-Factor Authentication code")

# Authentication Dependency Helpers
def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security_bearer)):
    """Validates the Bearer token for client webhook ingress protection."""
    if credentials.credentials != settings.API_BEARER_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing access token"
        )
    return credentials.credentials

def verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security_basic)):
    """Validates administrative requests using HTTP Basic Auth."""
    current_username_bytes = credentials.username.encode("utf-8")
    correct_username_bytes = settings.API_BASIC_AUTH_USERNAME.encode("utf-8")
    is_correct_username = secrets.compare_digest(current_username_bytes, correct_username_bytes)

    current_password_bytes = credentials.password.encode("utf-8")
    correct_password_bytes = settings.API_BASIC_AUTH_PASSWORD.encode("utf-8")
    is_correct_password = secrets.compare_digest(current_password_bytes, correct_password_bytes)

    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect Basic Authentication credentials",
            headers={"WWW-Authenticate": "Basic"}
        )
    return credentials.username

def run_login_in_background():
    """Target function for background login thread execution."""
    global login_error_detail
    login_error_detail = None
    try:
        session.warm()
        logger.info("Background Garmin Connect session established successfully.")
    except GarminConnectAuthenticationError as e:
        login_error_detail = "Garmin Connect session expired or credentials invalid. Please re-login on your bridge server dashboard."
        logger.error(f"Background Garmin Connect authentication failed: {e}")
    except Exception as e:
        login_error_detail = str(e)
        logger.error(f"Background Garmin Connect authentication failed: {e}")

def check_auth_status():
    """Returns the current state of Garmin Connect integration."""
    if mfa_state["waiting"]:
        return {"status": "mfa_required", "message": "Multi-Factor Authentication code required."}

    if session.is_authenticated:
        return {"status": "authenticated", "message": "Garmin Connect session active."}

    if login_thread and login_thread.is_alive():
        return {"status": "checking", "message": "Authentication in progress..."}

    if login_error_detail:
        return {"status": "unauthenticated", "message": f"Authentication failed: {login_error_detail}"}

    return {"status": "unauthenticated", "message": "Not authenticated."}

# Custom Exception Handler to log malformed payload formats
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
        body_str = body.decode("utf-8")
        payload = json.loads(body_str) if body_str else {"raw": "Empty body"}
    except Exception:
        payload = {"raw": "Could not parse invalid request body."}

    error_detail = exc.errors()
    # Pydantic 2 embeds raw Exception objects in ctx which aren't JSON-serializable — convert them
    for error in error_detail:
        if "ctx" in error and isinstance(error["ctx"].get("error"), Exception):
            error["ctx"]["error"] = str(error["ctx"]["error"])

    # Write to persistence logs (if enabled) and stdout
    log_attempt(
        status="Failed",
        payload=payload,
        error_detail=f"Payload Validation Error: {error_detail}",
        http_code=422
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": error_detail}
    )

# Routes
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(verify_basic_auth)])
async def serve_dashboard():
    """Serves the primary CSS-glassmorphic frontend dashboard UI (Basic Auth protected)."""
    template_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "templates", "index.html"))
    if not os.path.exists(template_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard UI template index.html not found."
        )
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read UI template: {e}"
        )

@app.get("/v1/auth/status", dependencies=[Depends(verify_basic_auth)])
async def get_status():
    """Returns the current connection status of Garmin Connect."""
    return check_auth_status()

@app.post("/v1/auth/login", dependencies=[Depends(verify_basic_auth)])
async def initiate_login():
    """Asynchronously initiates the login sequence in a separate background thread."""
    global login_thread

    if settings.DRY_RUN:
        return {"status": "dry_run", "message": "Login is disabled in dry-run mode."}

    if mfa_state["waiting"]:
        return {"status": "mfa_required", "message": "MFA code is already requested and waiting."}

    status_info = check_auth_status()
    if status_info["status"] == "authenticated":
        return {"status": "success", "message": "Already authenticated."}

    if login_thread is None or not login_thread.is_alive():
        login_thread = threading.Thread(target=run_login_in_background)
        login_thread.start()

    return {"status": "checking", "message": "Garmin Connect login sequence initiated."}

@app.post("/v1/auth/mfa", dependencies=[Depends(verify_basic_auth)])
async def submit_mfa(payload: MFAPayload):
    """Submits the MFA code to release the waiting authentication thread."""
    if not mfa_state["waiting"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active Garmin Connect MFA authentication session is waiting."
        )
    
    stripped_code = payload.code.strip()
    if not stripped_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="MFA code cannot be empty."
        )

    mfa_state["code"] = stripped_code
    mfa_state["event"].set()
    return {"status": "success", "message": "MFA code received. Garmin login resumed."}

@app.post("/v1/webhook/garmin", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_token)])
async def receive_webhook(payload: BodyCompositionPayload, background_tasks: BackgroundTasks):
    """Webhook ingestion endpoint to queue weight upload to Garmin Connect."""
    logger.info(f"Received webhook weight payload: {payload.weight}kg")

    if settings.DRY_RUN:
        log_attempt(status="DryRun", payload=payload.model_dump())
        return {"status": "dry_run", "message": "Dry-run mode: payload received and logged, Garmin upload skipped"}

    # Dispatch connection and file upload asynchronously to a background thread
    background_tasks.add_task(
        upload_to_garmin,
        weight=payload.weight,
        fat=payload.body_fat,
        water=payload.water,
        bone=payload.bone_mass,
        lean_mass=payload.lean_body_mass,
        date=payload.date,
        time=payload.time,
        tz=payload.timezone,
    )

    return {"status": "accepted", "message": "Measurement queued for Garmin upload"}

@app.get("/v1/logs", dependencies=[Depends(verify_basic_auth)])
async def get_logs():
    """Queries weight sync logs — from disk if PERSIST_LOGS, otherwise in-memory."""
    try:
        return get_recent_logs()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read logs: {e}"
        )

@app.post("/v1/logs/clear", dependencies=[Depends(verify_basic_auth)])
async def clear_logs():
    """Clears both the in-memory and persistent logs."""
    try:
        clear_recent_logs()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear logs: {e}"
        )
    return {"status": "success", "message": "Diagnostic logs successfully cleared."}
