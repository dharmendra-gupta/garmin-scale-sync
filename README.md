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
- 🧪 **Dry-Run Mode:** Validate client payloads locally without touching Garmin servers — received data is logged to the dashboard so you can iterate on your integration freely.

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
   
   # Logging (set to true if you want UI logs to persist across restarts)
   PERSIST_LOGS=false
   
   # Dry-run (set to true to log payloads without uploading to Garmin)
   DRY_RUN=false

   # Where the shared Garmin token lives: file | sqlite | postgres
   # See "Sharing a Garmin account" below before changing this.
   TOKEN_STORE=file
   # TOKEN_DB_URL=postgresql://user:password@host/dbname?sslmode=require
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

## 🧪 Dry-Run Mode

Use `DRY_RUN=true` when integrating a new client (e.g. openScale+) to verify that your payloads are correctly formed and reaching the service — without sending any data to Garmin Connect.

**What changes in dry-run mode:**
- Webhook accepts and validates payloads normally (invalid payloads still return `422`).
- Instead of uploading to Garmin, the payload is written to the log with `status: DryRun`.
- The dashboard log table shows all received payloads so you can inspect them in real time.
- Startup session restore is skipped — no Garmin credentials needed.
- The response body signals `"status": "dry_run"` so your client can detect it.

**Enable via environment variable:**
```env
DRY_RUN=true
```

Or inline with Docker Compose:
```bash
DRY_RUN=true docker compose up
```

**Example response in dry-run mode:**
```bash
curl -X POST http://localhost:8000/v1/webhook/garmin \
  -H "Authorization: Bearer <API_BEARER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"weight": 80.5, "body_fat": 15.2}'

# Response:
# {"status":"dry_run","message":"Dry-run mode: payload received and logged, Garmin upload skipped"}
```

The dashboard log will show an entry like:
```json
{
  "timestamp": "2024-01-15T08:30:00+00:00",
  "status": "DryRun",
  "payload": { "weight": 80.5, "body_fat": 15.2, ... },
  "error": null,
  "http_code": null
}
```

---

## 🔑 Sharing a Garmin account with other services

**Read this before running a second service against the same Garmin account.**

Garmin issues a **new refresh token every time one is refreshed** and invalidates
the previous one. Your account therefore has exactly **one valid refresh token at
any moment**, no matter how many services use it.

That has a consequence that is easy to get wrong: if two services each keep their
own copy of the token, whichever refreshes second is holding a token Garmin has
already replaced. It gets a `401`, falls back to a full credential login, and in
doing so invalidates the *other* service's token. They then take turns locking
each other out, while the repeated logins hit Garmin's SSO rate limit — which is
applied per IP and per account, and can affect the Garmin Connect mobile app too.

The fix is that every service must read and write **the same token store**:

| `TOKEN_STORE` | Where the token lives | Use when |
|---|---|---|
| `file` (default) | `$DATA_DIR/.garminconnect/garmin_tokens.json` | services share a host and a bind mount |
| `sqlite` | `$DATA_DIR/garmin_tokens.db` | services share a host; real write locking |
| `postgres` | `TOKEN_DB_URL` | **services run on different hosts** |

Writes are atomic and guarded by a lock, so concurrent access is safe. `sqlite` is
same-host only — its locking is unreliable over NFS/SMB. `postgres` needs no shared
filesystem, so it is the option that lets services run on separate machines.

> ⚠️ **All services must use the same setting and the same database.** Pointing one
> at `postgres` while another still reads the JSON file silently splits them onto
> separate sessions, and they resume invalidating each other.

## 🏗 Development & Testing

All tests run inside Docker to match the production environment exactly. The
`Dockerfile` is multi-stage: the default (`runtime`) image ships no test tooling,
so tests run against the `test` target.

```bash
docker build --target test -t gss:test .
docker run --rm --env-file .env gss:test pytest src/tests/ -n auto -v
```

To iterate without rebuilding, mount `src/` into a long-lived container:

```bash
docker run -d --name gss_dev -v "$PWD/src:/app/src" --env-file .env gss:test sleep infinity
docker exec gss_dev pytest src/tests/ -n auto -q
docker rm -f gss_dev
```

---

## 🔄 CI/CD Pipelines
The project includes GitHub Actions workflows:
- **`test.yml`**: Runs `pytest` automatically on PRs and pushes to `main`. Once all tests successfully pass, it automatically posts a PR comment notifying the repository owner to review and merge.
- **`publish.yml`**: Builds and pushes multi-architecture (`amd64`/`arm64`) Docker images to GitHub Container Registry (`ghcr.io`) automatically whenever a new release is published.

---

## 🤝 Acknowledgements & Credits

This project relies heavily on the excellent [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library created by **Ron Klinkien** ([@cyberjunky](https://github.com/cyberjunky)) for its core communication with the Garmin Connect API. Special thanks to the authors and contributors of that library for their outstanding work bypassing Cloudflare blocks and simplifying smart scale data syncing!
