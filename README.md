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
**Payload Format:**
```json
{
  "weight": 80.5,
  "body_fat": 15.2,
  "water": 58.0,
  "bone_mass": 3.1,
  "lean_body_mass": 68.2
}
```
*(All values are in kilograms/percentages as floats)*

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

### Running Locally (Without Docker)
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env # edit your credentials
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### Running Tests
The test suite uses `pytest` to validate core logic, MFA thread signaling, and FastAPI endpoint integrations.
```bash
pytest src/tests/ -v
```

---

## 🔄 CI/CD Pipelines
The project includes GitHub Actions workflows:
- **`test.yml`**: Runs `pytest` automatically on PRs and pushes to `main`. Once all tests successfully pass, it automatically posts a PR comment notifying the repository owner to review and merge.
- **`publish.yml`**: Builds and pushes multi-architecture (`amd64`/`arm64`) Docker images to GitHub Container Registry (`ghcr.io`) automatically whenever a new release is published.

---

## 🤝 Acknowledgements & Credits

This project relies heavily on the excellent [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library created by **Ron Klinkien** ([@cyberjunky](https://github.com/cyberjunky)) for its core communication with the Garmin Connect API. Special thanks to the authors and contributors of that library for their outstanding work bypassing Cloudflare blocks and simplifying smart scale data syncing!
