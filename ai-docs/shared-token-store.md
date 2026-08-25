# Shared token store

Read this before touching anything auth-related. This is the failure mode that
took the service down daily.

## Why it exists

One Garmin account is used by several services (this one, hevy2garmin-lite,
garmin_mcp, fitness-dashboard). They cannot have separate logins — Garmin flags
the account. So they share one refresh token.

Garmin **rotates the refresh token on every refresh**. In `garminconnect`'s
`Client._refresh_di_token`:

```python
self.di_refresh_token = data.get("refresh_token", self.di_refresh_token)
```

The old refresh token dies server-side. **Exactly one is valid at any moment.**

## The bug this fixed

1. Both services start, both read the file, both hold `RT1` in memory
2. Peer refreshes → gets `RT2`, writes it to the store. `RT1` is now dead
3. We are still holding `RT1` — we read the store once at startup and never again
4. Our access token expires → we refresh with `RT1` → Garmin rejects
5. `_refresh_session()` **swallows** that failure (`except Exception: _LOGGER.debug`)
6. We retry the call with a dead token → 401
7. `_run_request` raises `GarminConnectConnectionError("API Error 401 …")` — *not*
   an auth error, because `Client.post` → `_run_request` bypasses the
   `@_handle_api_errors` decorator that would translate it
8. The old code caught that as a connection error, logged 503, and **never dropped
   the cached client** → every later sync failed identically until a restart

## How `GarminSession` fixes it

- **Re-read before use.** If the store's blob differs from what we synced, a peer
  rotated; drop the client and rebuild from the current token. Works unilaterally.
- **Publish after use.** If our client's `dumps()` differs from what we synced, we
  rotated; write it back so peers adopt it.
- **Never publish a dead token.** On auth failure we drop the client *without*
  publishing — otherwise we would overwrite a peer's good token with a rejected one.
- **Lock across the operation.** `store.locked()` spans read → work → write, so a
  peer cannot interleave a refresh.
- **The library never sees the shared path.** Tokens are materialised into a private
  scratch dir which is what `Garmin.login()` receives, so garminconnect's internal
  `_refresh_session()` dumps somewhere harmless. `GarminSession` is the only writer.

`acquire()` / `publish()` exist for long operations (hevy2garmin-lite's sync cycle)
where holding the lock throughout would starve peers. `client()` does both.

## Backends (`TOKEN_STORE`)

| | change detection | locking | atomicity | scope |
|---|---|---|---|---|
| `file` | re-read blob | `flock` (**advisory**) | `tmp` + `os.replace` | one host |
| `sqlite` | re-read row | `BEGIN IMMEDIATE` | transaction | one host |
| `postgres` | re-read row | `pg_advisory_xact_lock` | transaction | **any host** |

`sqlite` is same-host only — its locking is POSIX advisory locks, unreliable over
NFS/SMB. `postgres` uses the *transaction-scoped* advisory lock, not the
session-scoped one, because Neon's pooled endpoint is PgBouncer in transaction mode
where a session-scoped lock can be released onto a different backend.

**All services must use the same backend and the same database.** Splitting them
onto different stores silently gives each its own session, and they resume
invalidating each other. `psycopg2-binary` is required for `postgres`.

## Typed 401

`garmin_session/errors.py` instruments each client's `_api_session` to record the
last response status, then wraps `_run_request` to raise
`GarminConnectAuthenticationError` when that status was 401. It keys off the **HTTP
status code**, not the error message. hevy2garmin-lite previously regex-matched
`"API Error 401"`, which coupled recovery to Garmin's error text and the library's
f-string — either could change silently. That workaround is now removed.

The real fix belongs upstream (decorate `_run_request`). When that lands, delete
`errors.py`.
