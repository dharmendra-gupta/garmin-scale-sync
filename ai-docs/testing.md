# Testing & investigation workflow

## Everything runs in Docker

No local `python`, `pip`, or `pytest`. CI does the same thing
(`.github/workflows/test.yml`): build the `test` stage, run `ruff`, then `pytest`.

```bash
docker compose run --rm test      # full suite, parallel
docker compose run --rm lint      # ruff check
```

Both bind-mount `src/`, so edits apply without a rebuild and `ruff --fix` changes
persist on the host rather than dying with the container.

## Tests run in parallel

`-n auto` (pytest-xdist) fans tests across all cores; each worker is a separate
process. Tests must therefore share no on-disk state — use `tmp_path` or
`tempfile`, never a fixed path. `conftest.py`'s autouse fixture resets in-process
state (`mfa_state`, the session, the log deque, `main`'s login globals), which is
per-worker and so safe.

Verified equivalent: 92 passed both under `-n auto` and `-p no:xdist`.

## Reuse one container

For anything the compose services don't cover, start one container and exec into
it rather than running a fresh one per command:

```bash
docker build --target test -t gss:dev .         # only when requirements change
docker run -d --name gss_dev \
  -v "$PWD/src:/app/src" \
  -e GARMIN_EMAIL=test@example.com -e GARMIN_PASSWORD=testpass \
  -e API_BEARER_TOKEN=testtoken -e API_BASIC_AUTH_PASSWORD=adminpass \
  gss:dev sleep infinity

docker exec gss_dev pytest src/tests/ -n auto -q
docker exec gss_dev pytest src/tests/test_garmin_session.py -q
docker exec gss_dev python -c "import inspect, garminconnect; print(inspect.getsource(garminconnect.Garmin.login))"
```

Tear down with `docker rm -f gss_dev` when finished. Remove throwaway images too.

## Strict TDD

1. Write the test. Run it. **Watch it fail for the right reason** — a test that
   passes before the fix is testing nothing.
2. Implement the smallest change that makes it pass.
3. Re-run the whole suite, not just the new test.

### Prove your guard tests can fail

A test that cannot fail is worse than none, because it looks like coverage. When
adding one, inject the violation and confirm red. Both examples below were verified
this way:

```bash
# the .garth guard actually fires
docker exec gss_dev sh -c \
  "echo '# client.garth.dump(p)' >> src/config.py && pytest src/tests/test_no_garth_access.py -q"

# the peer-rotation fix is really what makes the test pass
docker exec gss_dev sh -c \
  "sed -i 's/if self._client is not None and stored != self._synced_blob:/if False:/' \
   src/garmin_session/session.py && pytest src/tests/test_garmin_session.py -q"
```

Both must go red; revert the edits afterwards (`git checkout src/`).

## Never assume

Read the installed library rather than recalling its behaviour — the version here
(`garminconnect==0.3.11`) differs from hevy2garmin-lite's (`0.3.8`) and from
fitness-dashboard's / garmin_mcp's (`0.3.2`), and their internals differ.
Before bumping it, probe the internals `garmin_session/errors.py` relies on
(`Client._run_request`, `Client._api_session`, `dumps`/`loads`) — the unit tests
mock `Garmin`, so a rename would pass tests and break only in production.

```bash
docker compose run --rm --entrypoint python test -c "
import inspect, garminconnect.client as c
print(inspect.getsource(c.Client._run_request))"
```

## Do not hit Garmin from tests

Garmin rate-limits SSO **per IP**, and the account is shared with your phone. A
throwaway credential login during testing returned
`429: Mobile login returned 429 — IP rate limited by Garmin`. Mock the session
(`_patch_session_client` in `test_garmin_client.py`, or patch
`session._login_with_blob`). Only touch the real API when explicitly asked.

Likewise, never send a test weigh-in to the real account — it writes a visible
body-composition entry that is tedious to delete.

## Current state

92 tests passing, ruff clean.
