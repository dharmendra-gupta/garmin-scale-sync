# Garmin Scale Sync

A lightweight, robust bridge application designed to synchronize smart scale data directly to Garmin Connect via webhooks. It features a modern, glassmorphic dashboard for monitoring sync status and resolving Multi-Factor Authentication (MFA) challenges.

Designed explicitly with **Raspberry Pi (ARM64)** deployment in mind, this project bypasses Cloudflare protections on Garmin Connect seamlessly by leveraging `curl_cffi` within the `garminconnect` engine.

## Features
- 🚀 **Asynchronous Uploads:** Webhooks process immediately via FastAPI background tasks; slow Garmin API responses do not bottleneck your integration.
- 🔐 **Native MFA Handling:** When Garmin requires MFA, the sync process pauses and alerts the dashboard. You can enter the 6-digit code in the UI to resume the upload without losing data payload.
- 🎨 **Modern Dashboard UI:** A beautiful, responsive, glassmorphic dark-themed web interface to check connection status and read persistent logs.
- 🛡️ **Webhook Security:** Bearer token authentication ensures only your allowed services can queue body composition payloads.
- 📦 **Multi-Arch Docker Images:** Ready for `amd64` (Standard Servers/PCs) and `arm64` (Raspberry Pi 3/4/5).
- 💾 **Persistent Session & Logs:** Session tokens and upload logs survive container restarts via mounted volumes.
- 🕐 **Historical Data Backfill:** Post measurements for any past date and time with timezone support — useful for syncing missed readings or importing from another source.

---

## 🛠 Prerequisites

- Docker and Docker Compose installed.
- Your Garmin Connect email and password.
- Any smart scale integration (like Withings to IFTTT, Macrodroid, or iOS Shortcuts) capable of sending a POST JSON webhook.

---

## 🚀 Quick Start (Docker Compose)

1. Clone the repository:
   ```bash
   git clone https://github.com/mr13/garmin-scale-sync.git
   cd garmin-scale-sync
   ```

2. Create the configuration file:
   ```bash
   cp .env.example .env
   ```

3. Edit `.env` with your desired credentials:
   ```env
   # Garmin Connect Account
   GARMIN_EMAIL=your_garmin_email@example.com
   GARMIN_PASSWORD=your_garmin_password
   
   # Security
   API_BEARER_TOKEN=super_secret_webhook_token
   API_BASIC_AUTH_USERNAME=admin
   API_BASIC_AUTH_PASSWORD=secure_admin_password
   
   # Logging (Set to True if you want UI logs to persist)
   PERSIST_LOGS=True
   ```

4. Launch the container:
   ```bash
   docker compose up -d
   ```

5. Access the Dashboard:
   Open `http://<your-pi-ip>:8000/` in your browser.
   Use the `API_BASIC_AUTH_USERNAME` and `API_BASIC_AUTH_PASSWORD` when prompted.

---

## 📡 Webhook Usage

Send a `POST` request to the webhook endpoint whenever you weigh yourself.

**Endpoint:** `http://<your-pi-ip>:8000/v1/webhook/garmin`  
**Headers:**
```http
Authorization: Bearer <API_BEARER_TOKEN>
Content-Type: application/json
```

### Payload Format

All body composition fields are optional except `weight`.

```json
{
  "weight": 80.5,
  "body_fat": 15.2,
  "water": 58.0,
  "bone_mass": 3.1,
  "lean_body_mass": 68.2,
  "date": "2024-01-15",
  "time": "08:30:00",
  "timezone": "America/New_York"
}
```

*(Weight, bone mass, and lean body mass are in kilograms. Body fat and water are percentages.)*

### Datetime Fields

`date`, `time`, and `timezone` are all optional. When omitted, the upload is timestamped at the current UTC time.

| Field | Required | Format | Example |
|-------|----------|--------|---------|
| `date` | No — but required with `time` | `YYYY-MM-DD` | `"2024-01-15"` |
| `time` | No — but required with `date` | `HH:MM` or `HH:MM:SS` | `"08:30"` or `"08:30:00"` |
| `timezone` | No | IANA name or UTC offset | `"America/New_York"` or `"+05:30"` |

**Rules:**
- `date` and `time` must be provided together — one without the other returns `422`.
- `timezone` without `date`/`time` returns `422`.
- If `date`/`time` are provided but `timezone` is omitted, **UTC is assumed**.

---

## 📋 API Reference

All management endpoints use **HTTP Basic Auth** (`API_BASIC_AUTH_USERNAME` / `API_BASIC_AUTH_PASSWORD`).  
The webhook endpoint uses a **Bearer token** (`API_BEARER_TOKEN`).

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/` | Basic | Web dashboard UI |
| `GET` | `/v1/auth/status` | Basic | Returns current Garmin Connect session state |
| `POST` | `/v1/auth/login` | Basic | Initiates Garmin Connect login in the background |
| `POST` | `/v1/auth/mfa` | Basic | Submits MFA code to unblock the login thread |
| `GET` | `/v1/logs` | Basic | Returns recent sync log entries |
| `POST` | `/v1/logs/clear` | Basic | Clears all sync logs |
| `POST` | `/v1/webhook/garmin` | Bearer | Queues a body composition upload to Garmin Connect |

### Auth Status Response

`GET /v1/auth/status` returns one of:

```json
{ "status": "unauthenticated", "message": "Not authenticated." }
{ "status": "checking",        "message": "Authentication in progress..." }
{ "status": "mfa_required",    "message": "Multi-Factor Authentication code required." }
{ "status": "authenticated",   "message": "Garmin Connect session active." }
```

### Example: Post current weight

```bash
curl -X POST https://<host>/v1/webhook/garmin \
  -H "Authorization: Bearer <API_BEARER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"weight": 80.5, "body_fat": 15.2, "water": 58.0, "bone_mass": 3.1, "lean_body_mass": 68.2}'
```

### Example: Backfill a past measurement

```bash
curl -X POST https://<host>/v1/webhook/garmin \
  -H "Authorization: Bearer <API_BEARER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"weight": 80.5, "body_fat": 15.2, "date": "2024-01-15", "time": "08:30:00", "timezone": "America/New_York"}'
```

### Example: Trigger Login

```bash
curl -u admin:your_password -X POST https://<host>/v1/auth/login
```

### Example: Submit MFA Code

```bash
curl -u admin:your_password -X POST https://<host>/v1/auth/mfa \
  -H "Content-Type: application/json" \
  -d '{"code": "123456"}'
```

### Example: View Logs

```bash
curl -u admin:your_password https://<host>/v1/logs
```

---

## 🧠 MFA (Multi-Factor Authentication) Workflow
Garmin sometimes triggers MFA to verify logins. If this happens:
1. The background sync thread will **pause** for up to 60 seconds.
2. The UI dashboard will change status to `Multi-Factor Authentication Required` (pulsing yellow indicator).
3. Check your email/SMS for the 6-digit Garmin code.
4. Enter the code into the dashboard UI.
5. The thread resumes, caching the session tokens, and processes the paused payload upload successfully.

---

## 🏗 Development & Testing

### Running Tests (Docker)
All tests run inside Docker to match the production environment exactly.
```bash
docker compose build
docker compose run --rm garmin-scale-sync pytest src/tests/ -v
```

### Running Locally (Without Docker)
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env # edit your credentials
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🔄 CI/CD Pipelines
The project includes GitHub Actions workflows:
- **`test.yml`**: Runs `pytest` automatically on PRs and pushes to `main`. Once all tests successfully pass, it automatically posts a PR comment notifying the repository owner to review and merge.
- **`publish.yml`**: Builds and pushes multi-architecture (`amd64`/`arm64`) Docker images to GitHub Container Registry (`ghcr.io`) automatically whenever a new release is published.

---

## 🤝 Acknowledgements & Credits

This project relies heavily on the excellent [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library created by **Ron Klinkien** ([@cyberjunky](https://github.com/cyberjunky)) for its core communication with the Garmin Connect API. Special thanks to the authors and contributors of that library for their outstanding work bypassing Cloudflare blocks and simplifying smart scale data syncing!
