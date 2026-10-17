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
COPY services/order-service services/order-service

RUN uv sync --frozen --no-dev --no-editable --package order-service \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8002

CMD ["uvicorn", "order_service.main:app", "--host", "0.0.0.0", "--port", "8002"]

