# GarminScaleSync

FastAPI bridge: openScale (or any client) POSTs a body-composition payload to
`/v1/webhook/garmin`; it is uploaded to Garmin Connect. Basic-auth dashboard at `/`.

## Guardrails — non-negotiable

**1. Never assume.** Verify before asserting — read the installed library source,
not your memory of it; this repo has been bitten by exactly that. Cite `file:line`.
If you could not verify something, say so instead of implying you did.

**2. Strict TDD.** Write the failing test first and *watch it fail* for the right
reason, then implement. A guard test that cannot fail is worse than none — prove a
new one by injecting the violation and seeing red first.

**3. Everything runs in Docker.** No local `python`, `pip`, or `pytest`. The app,
tests, lint and one-off investigations all run in a container. CI does the same.

**4. Reuse one container.** Don't spin up a fresh container per command while
testing or investigating — start one, `docker exec` into it repeatedly, and rebuild
only when dependencies change. See `ai-docs/testing.md`.

## Commands

```bash
docker compose run --rm test      # full suite, parallel (pytest-xdist -n auto)
docker compose run --rm lint      # ruff check (src/ is mounted, so --fix persists)

# Single file:
docker compose run --rm --entrypoint python test -m pytest src/tests/test_x.py -q
```

Lint is `ruff` (`pyproject.toml`); CI runs it before pytest. Tests are independent
and run under `-n auto` — keep them so: no shared on-disk state, use `tmp_path`.

## Layout

```
src/config.py           pydantic-settings; all env vars
src/main.py             FastAPI routes, auth deps, lifespan
src/garmin_client.py    upload_to_garmin, MFA callback, logging, store selection
src/garmin_session/     shared-token session — see ai-docs/shared-token-store.md
src/tests/              pytest; conftest resets shared state per test
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
