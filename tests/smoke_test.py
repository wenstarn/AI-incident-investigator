import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import httpx

FRONTEND_URL = os.getenv("SMOKE_FRONTEND_URL", "http://127.0.0.1:3000").rstrip("/")
USER_URL = os.getenv("SMOKE_USER_URL", "http://127.0.0.1:8001").rstrip("/")
ORDER_URL = os.getenv("SMOKE_ORDER_URL", "http://127.0.0.1:8002").rstrip("/")
TIMEOUT_SECONDS = 10.0


def compose(*arguments: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["docker", "compose", *arguments],
        check=True,
        capture_output=capture,
        text=True,
    )
    return result.stdout if capture else ""


def wait_for_health(url: str, expected_status: int = 200) -> None:
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2)
            if response.status_code == expected_status:
                return
        except httpx.HTTPError as error:
            last_error = error
        time.sleep(0.5)
    raise AssertionError(f"Service did not become ready: {url}; {last_error}")


def expect(response: httpx.Response, status_code: int) -> dict[str, Any] | list[Any]:
    if response.status_code != status_code:
        raise AssertionError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}, expected {status_code}: {response.text}"
        )
    return response.json()


def create_smoke_product(run_id: str) -> int:
    postgres_user = compose(
        "exec", "-T", "postgres", "printenv", "POSTGRES_USER", capture=True
    ).strip()
    postgres_db = compose(
        "exec", "-T", "postgres", "printenv", "POSTGRES_DB", capture=True
    ).strip()
    sql = (
        "INSERT INTO order_service.products (name, price, stock) "
        f"VALUES ('Smoke product {run_id}', 9.99, 4) RETURNING id;"
    )
    output = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-qAt",
        "-U",
        postgres_user,
        "-d",
        postgres_db,
        "-c",
        sql,
        capture=True,
    )
    ids = [line for line in output.splitlines() if line.isdigit()]
    if len(ids) != 1:
        raise AssertionError(f"Could not determine smoke product ID: {output!r}")
    return int(ids[0])


def redis_command(*arguments: str) -> str:
    return compose("exec", "-T", "redis", "redis-cli", *arguments, capture=True).strip()


def read_json_logs(request_id: str) -> dict[str, list[dict[str, Any]]]:
    output = compose(
        "logs", "--no-color", "user-service", "order-service", capture=True
    )
    matching: dict[str, list[dict[str, Any]]] = {
        "user-service": [],
        "order-service": [],
    }
    for line in output.splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            record = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        service = record.get("service")
        if record.get("request_id") == request_id and service in matching:
            matching[service].append(record)
    return matching


def run() -> None:
    run_id = uuid4().hex
    email = f"smoke-{run_id}@example.com"
    correlation_id = f"smoke-{run_id}"
    redis_stopped = False
    user_service_stopped = False

    wait_for_health(f"{USER_URL}/health")
    wait_for_health(f"{ORDER_URL}/health")
    wait_for_health(f"{ORDER_URL}/health/redis")

    try:
        frontend = httpx.get(FRONTEND_URL, timeout=TIMEOUT_SECONDS)
        assert frontend.status_code == 200
        assert "Incident Shop" in frontend.text
        expect(httpx.get(f"{USER_URL}/health/db"), 200)
        expect(httpx.get(f"{ORDER_URL}/health/db"), 200)

        product_id = create_smoke_product(run_id)
        product = expect(httpx.get(f"{FRONTEND_URL}/api/products/{product_id}"), 200)
        assert isinstance(product, dict)
        assert product["stock"] == 4

        user = expect(
            httpx.post(
                f"{FRONTEND_URL}/api/users",
                json={"name": "Smoke Test", "email": email},
                headers={"X-Request-ID": correlation_id},
                timeout=TIMEOUT_SECONDS,
            ),
            201,
        )
        assert isinstance(user, dict)

        products = expect(
            httpx.get(f"{FRONTEND_URL}/api/products?limit=100&offset=0"), 200
        )
        assert isinstance(products, list)
        assert any(item["id"] == product_id for item in products)

        order = expect(
            httpx.post(
                f"{FRONTEND_URL}/api/orders",
                json={"user_id": user["id"], "product_id": product_id, "quantity": 1},
                headers={"X-Request-ID": correlation_id},
                timeout=TIMEOUT_SECONDS,
            ),
            201,
        )
        assert isinstance(order, dict)
        after_order = expect(
            httpx.get(f"{FRONTEND_URL}/api/products/{product_id}"), 200
        )
        assert isinstance(after_order, dict)
        assert after_order["stock"] == 3

        compose("restart", "user-service", "order-service")
        wait_for_health(f"{USER_URL}/health")
        wait_for_health(f"{ORDER_URL}/health")
        persisted_user = expect(
            httpx.get(f"{FRONTEND_URL}/api/users/{user['id']}"), 200
        )
        persisted_order = expect(
            httpx.get(f"{FRONTEND_URL}/api/orders/{order['id']}"), 200
        )
        assert persisted_user == user
        assert persisted_order == order

        redis_command("DEL", f"product:{product_id}")
        stats_before = expect(httpx.get(f"{ORDER_URL}/internal/stats"), 200)
        expect(httpx.get(f"{ORDER_URL}/products/{product_id}"), 200)
        stats_after_miss = expect(httpx.get(f"{ORDER_URL}/internal/stats"), 200)
        expect(httpx.get(f"{ORDER_URL}/products/{product_id}"), 200)
        stats_after_hit = expect(httpx.get(f"{ORDER_URL}/internal/stats"), 200)
        assert isinstance(stats_before, dict)
        assert isinstance(stats_after_miss, dict)
        assert isinstance(stats_after_hit, dict)
        assert stats_after_miss["cache_misses"] == stats_before["cache_misses"] + 1
        assert stats_after_hit["cache_hits"] == stats_after_miss["cache_hits"] + 1
        assert int(redis_command("TTL", f"product:{product_id}")) > 0

        compose("stop", "redis")
        redis_stopped = True
        fallback = expect(httpx.get(f"{ORDER_URL}/products/{product_id}"), 200)
        assert isinstance(fallback, dict)
        assert fallback["id"] == product_id
        compose("start", "redis")
        wait_for_health(f"{ORDER_URL}/health/redis")
        redis_stopped = False

        stock_before_failure = expect(
            httpx.get(f"{ORDER_URL}/products/{product_id}"), 200
        )
        compose("stop", "user-service")
        user_service_stopped = True
        unavailable = httpx.post(
            f"{ORDER_URL}/orders",
            json={"user_id": user["id"], "product_id": product_id, "quantity": 1},
            timeout=TIMEOUT_SECONDS,
        )
        assert unavailable.status_code == 502
        stock_after_failure = expect(
            httpx.get(f"{ORDER_URL}/products/{product_id}"), 200
        )
        assert isinstance(stock_before_failure, dict)
        assert isinstance(stock_after_failure, dict)
        assert stock_after_failure["stock"] == stock_before_failure["stock"]
        compose("start", "user-service")
        wait_for_health(f"{USER_URL}/health")
        user_service_stopped = False

        def create_concurrent_order(_: int) -> int:
            response = httpx.post(
                f"{ORDER_URL}/orders",
                json={"user_id": user["id"], "product_id": product_id, "quantity": 1},
                timeout=TIMEOUT_SECONDS,
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=5) as executor:
            statuses = list(executor.map(create_concurrent_order, range(5)))
        final_product = expect(httpx.get(f"{ORDER_URL}/products/{product_id}"), 200)
        assert statuses.count(201) == 3
        assert statuses.count(409) == 2
        assert isinstance(final_product, dict)
        assert final_product["stock"] == 0

        logs = read_json_logs(correlation_id)
        assert logs["order-service"]
        assert logs["user-service"]
        for service_records in logs.values():
            assert any(record["event"] == "http_request" for record in service_records)
            assert all(
                record["request_id"] == correlation_id for record in service_records
            )

        print("Smoke test passed")
        print(f"run_id={run_id}")
        print(f"user_id={user['id']} product_id={product_id} order_id={order['id']}")
    finally:
        if redis_stopped:
            compose("start", "redis")
        if user_service_stopped:
            compose("start", "user-service")


if __name__ == "__main__":
    try:
        run()
    except (AssertionError, httpx.HTTPError, subprocess.CalledProcessError) as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
