"""Dependency addresses currently used by Order Service."""

from fastapi import Request

from order_service.config import get_redis_url, get_user_service_url


def effective_user_service_url(request: Request) -> str:
    override = request.app.state.fault_registry.get_fault_by_type(
        "user_service_url_override"
    )
    return override.parameters["url"] if override else get_user_service_url()


def effective_redis_url(request: Request) -> str:
    override = request.app.state.fault_registry.get_fault_by_type("redis_url_override")
    return override.parameters["url"] if override else get_redis_url()
