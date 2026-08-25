FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install basic networking utilities and CA certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Test stage: adds pytest and friends on top of the runtime image, so tests run
# against exactly the dependency set that ships. CI builds this target.
#   docker build --target test -t gss:test .
FROM base AS test

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

# ruff reads its config from here.
COPY pyproject.toml .

# Runtime stage. Last stage, so a plain `docker build .` produces this — without
# any test tooling.
FROM base AS runtime

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
