# Build context is the straight_rows/ folder (see compose.yaml).
FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM debian:bookworm-slim
COPY --from=uv /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
# uv installs the Python version pinned in .python-version, then the locked dependencies from uv.lock.
COPY .python-version pyproject.toml uv.lock ./
COPY main /app/main
RUN uv sync --frozen
ENTRYPOINT ["python"]
