# GarminScaleSync

FastAPI bridge: openScale (or any client) POSTs a body-composition payload to
`/v1/webhook/garmin`; it is uploaded to Garmin Connect. Basic-auth dashboard at `/`.

## Guardrails — non-negotiable

**1. Never assume.** Verify before asserting. Read the installed library source
(`docker run --rm gss:dev python -c "import inspect, garminconnect; ..."`), not
your memory of it — this repo has been bitten by exactly that. Cite `file:line`.
If you could not verify something, say so explicitly instead of implying you did.

**2. Strict TDD.** Write the failing test first and *watch it fail* for the right
reason. Then implement. A guard test that cannot fail is worse than no test — when
adding one, prove it by injecting the violation and seeing red before you trust it.

**3. Everything runs in Docker.** No local `python`, `pip`, or `pytest`. The app,
tests, lint and one-off investigations all run in a container. CI does the same.

**4. Reuse one container.** Do not spin up a fresh container per command during
testing or investigation. Start `gss:dev` once, `docker exec` into it repeatedly.
Rebuild only when `requirements.txt` changes. See `ai-docs/testing.md`.

## Commands

```bash
docker build --target test -t gss:dev .       # rebuild only on dependency change
docker run -d --name gss_dev -v "$PWD/src:/app/src" --env-file .env gss:dev
docker exec gss_dev pytest src/tests/ -n auto -q      # iterate: source is mounted
docker exec gss_dev pytest src/tests/test_x.py -q     # single file
```

Tests need `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `API_BEARER_TOKEN`,
`API_BASIC_AUTH_PASSWORD`; dummy values are fine (see `.github/workflows/test.yml`).

## Layout

```
src/config.py           pydantic-settings; all env vars
src/main.py             FastAPI routes, auth deps, lifespan
src/garmin_client.py    upload_to_garmin, MFA callback, logging, store selection
src/garmin_session/     shared-token session — see ai-docs/shared-token-store.md
src/tests/              pytest; conftest resets shared state between tests
```

## Critical context

This Garmin account is shared by several services (hevy2garmin-lite, garmin_mcp,
fitness-dashboard). Garmin **rotates the refresh token on every refresh**, so only
one is valid at a time. Anything touching auth must read
`ai-docs/shared-token-store.md` first — it is the top source of breakage here.
`src/garmin_session/` is **byte-identical** to hevy2garmin-lite's copy: change it in
both, or not at all.

## Further reading

- `ai-docs/architecture.md` — request flow, auth model, error mapping
- `ai-docs/shared-token-store.md` — token rotation, stores, locking
- `ai-docs/testing.md` — Docker/TDD workflow
- `ai-docs/RAG.md` — verified facts and gotchas; read before re-deriving anything
