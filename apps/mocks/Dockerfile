FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.15 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml ./
COPY mocks ./mocks

RUN uv sync --no-dev

EXPOSE 8090

CMD ["uvicorn", "mocks.main:app", "--host", "0.0.0.0", "--port", "8090"]
