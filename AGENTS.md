# AI Incident Investigator — Agent Instructions

## Project Overview

This repository contains an AI Incident Investigator project.

The system consists of a small e-commerce application designed to provide a realistic environment for investigating software incidents.

The complete system will eventually include:

- User Service (FastAPI)
- Order Service (FastAPI)
- PostgreSQL
- Redis
- Frontend (HTML, CSS, JavaScript)
- Incident Controller
- AI Investigation Agent

The complete architecture is described in `docs/architecture.md`.

**Always read `docs/architecture.md` before making architectural decisions.**

## Current Development Scope

The current priority is implementing the e-commerce application.

Focus on:

1. User Service
2. Order Service
3. PostgreSQL integration
4. Redis caching
5. Frontend
6. Docker Compose
7. Logging and health checks

Do not implement the Incident Controller, AI Agent, RAG, or LLM integrations unless explicitly requested.

## Technology Stack

- Python 3.12
- FastAPI
- Pydantic
- SQLAlchemy 2.0
- Alembic
- PostgreSQL
- Redis
- httpx
- uv
- pytest
- Ruff
- Docker / Docker Compose
- HTML / CSS / Vanilla JavaScript

Do not introduce additional frameworks or infrastructure without a clear justification.

## Architecture Rules

1. User Service owns user-related business logic and data.
2. Order Service owns products and orders.
3. Order Service must communicate with User Service through HTTP.
4. Order Service must not directly query User Service database tables.
5. PostgreSQL is the source of truth for persistent data.
6. Redis is used only for caching and must not be the source of truth.
7. External dependencies must be configurable through environment variables.
8. Business logic must be separated from HTTP route handlers.
9. Services must remain independently deployable.
10. Preserve realistic service dependencies and failure behavior for future incident investigations.

## Development Workflow

Follow an incremental development process.

For every task:

1. Read the relevant existing code.
2. Identify the minimal required changes.
3. Implement only the requested functionality.
4. Add or update relevant tests.
5. Run available tests and lint checks.
6. Explain what changed.
7. Report any limitations or unresolved issues.

Do not implement future development stages unless explicitly requested.

Avoid unnecessary refactoring of unrelated code.

## Code Quality

- Use Python type hints.
- Use Pydantic models for API validation.
- Follow existing project conventions.
- Prefer simple, readable implementations.
- Avoid unnecessary abstractions.
- Do not introduce premature optimizations.
- Use asynchronous operations only when they provide a clear benefit.
- Handle external service failures explicitly.
- Use appropriate HTTP status codes.
- Keep configuration separate from business logic.

## Database Rules

- Use SQLAlchemy 2.0.
- Manage schema changes through Alembic migrations.
- Do not use automatic table creation during application startup.
- Use database transactions for operations that modify multiple related records.
- Prevent race conditions when updating product stock.
- Do not use floating-point arithmetic for monetary values.
- Do not introduce cross-service foreign keys.

## Redis Rules

- Use the cache-aside pattern.
- PostgreSQL remains the source of truth.
- Redis failures must not make product reads unavailable.
- Do not cache inventory quantities.
- Use configurable TTL values.

## API Rules

- Use REST conventions.
- Validate incoming requests.
- Return consistent JSON responses.
- Use meaningful HTTP status codes.
- Do not expose internal exception details to API clients.
- Keep API contracts backward-compatible unless changes are explicitly requested.

## Logging and Observability

- Use structured JSON logging.
- Include service name, timestamp, log level, and request ID.
- Log failures of external dependencies.
- Do not log passwords, credentials, tokens, or secrets.
- Provide health-check endpoints.
- Preserve enough diagnostic information for future AI-based incident investigation.

## Testing

Use pytest for backend tests.

Tests should cover:

- Successful API operations
- Invalid input
- Missing resources
- Database errors
- External service failures
- Business logic edge cases

Use isolated test databases where appropriate.

Do not rely exclusively on mocked tests for database transaction correctness.

## Docker

- Use Docker Compose for local infrastructure.
- Keep service configuration in environment variables.
- Use persistent volumes for PostgreSQL.
- Do not hardcode container IP addresses.
- Use Docker service names for inter-container communication.
- Do not introduce Kubernetes.

## Security

- Never commit real secrets.
- Use `.env.example` for documenting environment variables.
- Do not expose internal diagnostic endpoints publicly.
- Do not execute arbitrary shell commands from API input.
- Do not expose Docker socket access to application services.

## Communication

When completing a task:

1. Summarize the implemented changes.
2. List modified files.
3. Provide commands to run and test the changes.
4. Describe expected results.
5. Mention any known limitations.

If a requirement is ambiguous and significantly affects architecture, ask for clarification instead of making a major assumption.