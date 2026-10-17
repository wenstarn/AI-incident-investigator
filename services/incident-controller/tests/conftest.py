import pytest


@pytest.fixture(autouse=True)
def controller_token(monkeypatch, request):
    if request.node.get_closest_marker("destructive"):
        return
    monkeypatch.setenv("INCIDENT_CONTROLLER_TOKEN", "unit-test-controller-token")
