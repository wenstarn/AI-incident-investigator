"""JSON-safe, common diagnostic result."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class DiagnosticError(BaseModel):
    type: str
    message: str


class DiagnosticResult(BaseModel):
    model_config = ConfigDict(strict=True)

    tool: str
    status: Literal["ok", "degraded", "error"]
    data: dict[str, Any]
    error: DiagnosticError | None = None
