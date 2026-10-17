FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY services/incident-controller/pyproject.toml services/incident-controller/uv.lock ./
COPY services/incident-controller/src ./src
RUN uv sync --frozen --no-dev --no-editable && useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
HEALTHCHECK --interval=5s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/health', timeout=2)"
CMD ["uvicorn", "incident_controller.main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]
