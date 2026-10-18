"""Incident Controller client used ONLY by the live evaluation harness.

Agents never receive this client, its URL or its token.
"""

import os

import httpx


class ControllerError(Exception):
    pass


class IncidentControllerClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_seconds: float = 180,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise ControllerError("INCIDENT_CONTROLLER_TOKEN is not configured")
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Incident-Token": token}
        self._timeout = timeout_seconds
        self._transport = transport

    @classmethod
    def from_env(cls) -> "IncidentControllerClient":
        return cls(
            os.getenv("INCIDENT_CONTROLLER_URL", "http://127.0.0.1:8003"),
            os.getenv("INCIDENT_CONTROLLER_TOKEN", ""),
        )

    async def _request(self, method: str, path: str) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, trust_env=False
            ) as client:
                response = await client.request(
                    method, self._base_url + path, headers=self._headers
                )
        except httpx.HTTPError as error:
            raise ControllerError("Incident Controller is unreachable") from error
        if response.status_code >= 400:
            raise ControllerError(
                f"Incident Controller returned HTTP {response.status_code} for {path}"
            )
        return response.json()

    async def active(self) -> str | None:
        incident = (await self._request("GET", "/incidents/active")).get("incident")
        return incident["id"] if incident else None

    async def activate(self, incident_id: str) -> None:
        await self._request("POST", f"/incidents/{incident_id}/activate")

    async def reset(self) -> None:
        await self._request("POST", "/incidents/active/reset")
