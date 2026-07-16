# syntax=docker/dockerfile:1
FROM python:3.12-slim

# A few wheels in the chroma/langchain stack still build from source on slim.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Editable install, deliberately: templates/ and static/ are resolved from __file__
# (web/config.py) and are not declared package_data, so a regular install would drop
# them. Editable also anchors rag.REPO_ROOT at /app, which is what makes "local"
# storage default to /app/data/chroma — inside the container.
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# The call-order dependency graph (read at runtime) and the pinned Meraki spec
# snapshot (so the CLI ingest path, and an in-container upload, both have it).
# The vector store itself is NOT baked in — it's built at runtime.
COPY data/graph/ ./data/graph/
COPY data/specs/meraki_open_api_spec.json ./data/specs/

# SQLite lives on a mounted volume so users/tests/KB versions survive a restart.
ENV CASEWRIGHT_DB_PATH=/data/casewright.db

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data /app/data/chroma \
    && chown -R app:app /data /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8000"]
