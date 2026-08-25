# Verified facts & gotchas

Findings confirmed by reading source or running code, with how they were checked.
Consult this before re-deriving anything. **Re-verify before relying on a claim** —
library internals move between versions.

## garminconnect internals

- **Refresh tokens rotate.** `Client._refresh_di_token`:
  `self.di_refresh_token = data.get("refresh_token", self.di_refresh_token)`.
  Old token dies server-side; exactly one is valid at a time.
  *Verified: `inspect.getsource` in the 0.3.3 image.*
- **`_run_request` raises the wrong type on 401.** On any `>=400` it raises
  `GarminConnectConnectionError(f"API Error {status}")`. It refreshes and retries
  once on 401 first. Only `Garmin.connectapi()` / download carry
  `@_handle_api_errors`, which translates 401 → auth error; `Client.post` (used by
  `add_body_composition`) bypasses it. *Verified: source, 0.3.3 / 0.3.8 / 0.3.11.*
- **`_refresh_session()` swallows refresh failures** (`except Exception as err:
  _LOGGER.debug(...)`) then returns, so the caller retries with a dead token.
  *Verified: source.*
- **`_refresh_session()` does persist** via `self.dump(self._tokenstore_path)`, and
  `Client.load()` sets `_tokenstore_path`. So the library writes rotations back to
  whatever path it was given — which is why we hand it a private scratch dir.
  *Verified: source.*
- **`dump()` is not atomic.** 0.3.3 uses `write_text()`; 0.3.8 uses
  `O_CREAT|O_TRUNC` then write. Both leave a window where a peer reads a truncated
  file. Our stores use `tmp` + `os.replace`. *Verified: source, both versions.*
- **`login(tokenstore=...)` treats a >512-char string as token data**, not a path.
  Do not rely on that heuristic; pass a directory.
- **`Garmin.login()` with no argument falls back to `os.getenv("GARMINTOKENS")`**,
  which `config.py` sets to the shared dir. Always pass the scratch path explicitly.
- **`garth` is gone.** Not installed, and `hasattr(Garmin, "garth")` is `False` in
  0.3.3, 0.3.8 and 0.3.11. Use `.client`. `client.garth.dump(...)` raises `AttributeError`
  — and does so only on the rare token-persist path, so it hides until re-auth is
  needed. `src/tests/test_no_garth_access.py` guards this.
  *Verified: `importlib.util.find_spec`, `hasattr`, in each image.*

## FastAPI / Starlette

- **`@app.get` does not add HEAD.** FastAPI's `APIRoute.__init__` sets
  `self.methods = {…}` from what you pass; raw Starlette's `Route` adds `"HEAD"`
  when `"GET"` is present. So `HEAD /` → 405. *Verified: source + live curl.*
- **`HTTPBearer` with no credentials now returns 401, not 403** (changed in
  FastAPI 0.112; we were on 0.111). Correct semantics, but anything alerting on
  403 needs updating. *Verified: test went red on the upgrade.*
- **`status.HTTP_422_UNPROCESSABLE_ENTITY` is deprecated** in favour of
  `HTTP_422_UNPROCESSABLE_CONTENT`. The wire status is still 422.
- **`fastapi` no longer bundles extras.** Since 0.112 the heavy deps
  (`email-validator`, `typer`, `rich`, `jinja2`, `orjson`, `ujson`, `fastapi-cli`)
  moved behind `fastapi[standard]`. Plain `fastapi` is slim — confirmed absent
  from the runtime image. Do not re-add them without a reason.

## Project constraints

- **Python 3.12 minimum** — required by `garminconnect>=0.3.3`.
- **Deployment target is a Raspberry Pi**, so the image stays on `python:*-slim`
  and low-memory: don't add heavyweight dependencies without a reason. `publish.yml`
  builds `linux/amd64` + `linux/arm64`.
- **No `garth` or other obsolete auth libraries** — an explicit project requirement
  from the outset, enforced by `src/tests/test_no_garth_access.py`.

## Postgres / Neon

- **Use `pg_advisory_xact_lock`, not `pg_advisory_lock`.** Neon's pooled endpoint
  is PgBouncer in transaction mode, where a session-scoped lock can be released
  onto a different backend than took it.
- **Verified working against the real Neon DB**: connect, `CREATE TABLE`, save/load
  round-trip, and cross-*connection* mutual exclusion (second holder blocked while
  the first held the lock, acquired after release).
- `psycopg2-binary` is **not** a transitive dependency — it must be declared.

## Fleet

Four services share this Garmin account. Versions differ, so do not assume one
service's library behaviour matches another's.

| Service | garminconnect | client lifetime | notes |
|---|---|---|---|
| garmin-scale-sync (this) | 0.3.11 | `GarminSession` | fixed |
| hevy2garmin-lite | 0.3.8 | `GarminSession` | fixed; shares the identical module |
| fitness-dashboard | 0.3.2 | **per call** | `login()` every call — main rate-limit source; also has the `garmin.garth.dump` bug at `backend/connectors/garmin.py`. **Not yet fixed.** |
| garmin_mcp | 0.3.2 | singleton at startup | no 401 recovery; `_GarminProxy` reports a 401 as *"Garmin Connect is unreachable"*. **Not yet fixed.** |

## Rate limiting

Garmin rate-limits SSO **per IP**, account-scoped — it can affect the phone app.
A throwaway login during testing returned `429: Mobile login returned 429 — IP rate
limited by Garmin`, and a real login logged the same on two strategies before
succeeding via another. Credential logins must stay rare; that is a large part of
why the shared store matters.

## Unverified / open

- Whether phone outages are *caused* by this rate limiting — plausible and
  mechanically coherent, never confirmed. Correlate 429s with outage times.
- End-to-end upload against the real account after the Postgres cutover has not
  been run (auth and storage are proven; `add_body_composition` is only mocked).
