import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GARMIN_EMAIL: str
    GARMIN_PASSWORD: str
    API_BEARER_TOKEN: str

    API_BASIC_AUTH_USERNAME: str = "admin"
    API_BASIC_AUTH_PASSWORD: str

    PERSIST_LOGS: bool = False
    DRY_RUN: bool = False

    HOST: str = "0.0.0.0"
    PORT: int = 8000

    DATA_DIR: str = "/app/data"

    # Where the shared Garmin refresh token lives. Defaults to "file" because
    # the other services on this account read garmin_tokens.json directly —
    # switching this service alone to sqlite/postgres would stop it sharing a
    # token with them, which is worse than the problem being fixed. Move the
    # whole fleet together.
    TOKEN_STORE: str = "file"
    TOKEN_DB_URL: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

# Dynamic initialization to support both Docker container and local test runner execution
if not os.path.exists(settings.DATA_DIR):
    try:
        os.makedirs(settings.DATA_DIR, exist_ok=True)
    except OSError:
        # Fallback local data folder
        settings.DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
        os.makedirs(settings.DATA_DIR, exist_ok=True)

# Set the GARMINTOKENS env var which python-garminconnect uses to save OAuth session tokens
os.environ["GARMINTOKENS"] = os.path.join(settings.DATA_DIR, ".garminconnect")
