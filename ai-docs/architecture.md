# Architecture

## Request flow

```
client (openScale) --POST /v1/webhook/garmin--> FastAPI
                                                  |  validate BodyCompositionPayload
                                                  |  return 201 immediately
                                                  v
                                          BackgroundTasks
                                                  |
                                    upload_to_garmin() [garmin_client.py]
                                                  |  with session.client() as c:
                                                  v
                                    c.add_body_composition(...)
```

The webhook returns **201 before the upload runs**. A failed upload therefore never
shows as a non-2xx to the client — it is only visible in `/v1/logs` and stdout. This
is deliberate (the scale client should not retry), but it means *upload failures are
silent to the caller*, which is why the logging in `log_attempt()` matters.

## Field translation

Garmin stores *skeletal* muscle mass (bone excluded); scales report *lean body
mass* (bone included). `upload_to_garmin` converts:

```
muscle_mass = lean_body_mass - bone_mass
```

Everything else passes through (`body_fat`→`percent_fat`, `water`→
`percent_hydration`). If either input is missing, muscle mass is omitted rather
than guessed — see the table in `README.md`.

## Auth model

Two independent schemes, both in `main.py`:

| Scheme | Applies to | Dependency |
|---|---|---|
| Bearer token | `POST /v1/webhook/garmin` | `verify_token` |
| HTTP Basic | `/`, `/v1/auth/*`, `/v1/logs*` | `verify_basic_auth` |

Basic-auth comparison uses `secrets.compare_digest` on both username and password.

`GET /` is registered with `@app.get` only, so **`HEAD /` returns 405** — FastAPI's
`APIRoute` does not auto-add HEAD the way raw Starlette's `Route` does. Uptime
monitors probing with HEAD will see 405, and unauthenticated probes see 401. Both
are expected, not bugs. There is no unauthenticated health endpoint.

## Garmin session

`garmin_client.session` is the single entry point. It is a `GarminSession` built by
`_build_token_store()` from `TOKEN_STORE` (`file` | `sqlite` | `postgres`).

There is **no cached client singleton and no `reset_garmin_client()`** — both were
removed. The session owns client lifetime, re-reads the shared store before every
use, and drops the client itself on an auth failure. See `shared-token-store.md`.

MFA: `prompt_mfa_callback` blocks the login thread on a `threading.Event` for up to
60s while the dashboard POSTs the code to `/v1/auth/mfa`. There is no terminal in
the container, so interactive prompting is not an option.

## Error mapping in `upload_to_garmin`

| Exception | Logged `http_code` | Notes |
|---|---|---|
| `GarminConnectAuthenticationError` | 401 | session already dropped its client |
| `GarminConnectTooManyRequestsError` | 429 | Garmin rate limit |
| `GarminConnectConnectionError` | 503 | genuine transport failure |
| anything else | 500 | `exc_info=True` |

A 401 arriving from the upload path used to land in the 503 branch — see
`shared-token-store.md` and `RAG.md`. That is now typed correctly upstream in
`garmin_session/errors.py`, so the 503 branch means what it says.

## Logging

`log_attempt()` writes to an in-memory `deque(maxlen=50)` always, and additionally
to `$DATA_DIR/upload_logs.json` (capped at 100 entries) when `PERSIST_LOGS=true`.
Both are guarded by `log_lock`. `/v1/logs` reads disk when persisting, else memory.

A custom `RequestValidationError` handler logs malformed payloads as `Failed` with
`http_code=422`, converting Pydantic's non-serialisable `ctx["error"]` to `str`.
