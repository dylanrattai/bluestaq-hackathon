# Build context is the straight_rows/ folder (see compose.yaml). Same uv-pinned Python and locked dependencies as the evaluator.
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
COPY .python-version pyproject.toml uv.lock ./
RUN uv sync --frozen
EXPOSE 8000
ENTRYPOINT ["python"]
CMD ["-c", "print('webui: nothing implemented yet')"]
