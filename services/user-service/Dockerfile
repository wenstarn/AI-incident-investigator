FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.12-slim

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY services/user-service/pyproject.toml services/user-service/pyproject.toml
COPY services/order-service/pyproject.toml services/order-service/pyproject.toml
COPY services/user-service services/user-service

RUN uv sync --frozen --no-dev --no-editable --package user-service \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8001

CMD ["uvicorn", "user_service.main:app", "--host", "0.0.0.0", "--port", "8001"]

