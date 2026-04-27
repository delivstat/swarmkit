# Swael runtime — production Docker image.
#
# Build:  docker build -t swael .
# Run:    docker run -p 8000:8000 -v /path/to/workspace:/workspace swael serve /workspace
#
# Multi-stage: build with uv, run with slim Python.

FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY packages/runtime/ packages/runtime/
COPY packages/schema/ packages/schema/
COPY pyproject.toml uv.lock ./

RUN uv sync --all-packages --no-dev --frozen

FROM python:3.11-slim

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/packages /app/packages
COPY reference/ /app/reference/
COPY design/ /app/design/
COPY docs/ /app/docs/
COPY README.md CLAUDE.md llms.txt /app/

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app

EXPOSE 8000

ENTRYPOINT ["swael"]
CMD ["serve", "/workspace", "--host", "0.0.0.0", "--port", "8000"]
