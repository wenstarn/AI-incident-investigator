"""Evaluation cases: symptom shown to the agent and expected diagnosis.

Symptoms describe what a user or an alert reports and never name the cause.
Cases with different causes deliberately share the same symptom.
"""

from pydantic import BaseModel

from investigator_agent.report import Category, Component, DependencyTarget

GENERIC_SYMPTOM = (
    "Пользователи сообщают, что с интернет-магазином что-то не так. "
    "Проверь систему и найди причину, если она есть."
)
ORDER_502 = (
    "Пользователи не могут оформить заказ: POST /orders в Order Service "
    "возвращает 502 Bad Gateway. Каталог товаров открывается."
)
REDIS_ALERT = (
    "Алерт мониторинга: в Order Service растёт число ошибок подключения к Redis "
    "(redis_connection_errors_total). Карточки товаров при этом открываются."
)


# Business requests that reproduce each symptom: the live harness sends them
# after activating a scenario, and replay worlds simulate their effects on logs
# and counters. The user ID never exists, so no order or stock is ever changed.
MISSING_USER_ID = 2_000_000_000
Request = tuple[str, str, str, dict | None]
ORDER_REQUEST: Request = (
    "order",
    "POST",
    "/orders",
    {"user_id": MISSING_USER_ID, "product_id": 1, "quantity": 1},
)
PRODUCT_REQUEST: Request = ("order", "GET", "/products/1", None)
CATALOG_REQUEST: Request = ("order", "GET", "/products", None)
USER_REQUEST: Request = ("user", "GET", "/users/1", None)
SYMPTOM_REQUESTS: dict[str, list[Request]] = {
    "INC-001": [ORDER_REQUEST] * 3,
    "INC-002": [PRODUCT_REQUEST] * 3,
    "INC-003": [CATALOG_REQUEST] * 3 + [USER_REQUEST] * 2,
    "INC-004": [ORDER_REQUEST] * 3,
    "INC-005": [ORDER_REQUEST] * 3,
    "INC-006": [PRODUCT_REQUEST] * 3,
}


class ExpectedDiagnosis(BaseModel):
    category: Category
    component: Component
    dependency: DependencyTarget = "none"


class EvalCase(BaseModel):
    id: str
    incident_id: str | None
    symptom: str
    expected: ExpectedDiagnosis


CASES: list[EvalCase] = [
    EvalCase(
        id="INC-001",
        incident_id="INC-001",
        symptom=ORDER_502,
        expected=ExpectedDiagnosis(category="component_down", component="user-service"),
    ),
    EvalCase(
        id="INC-002",
        incident_id="INC-002",
        symptom=REDIS_ALERT,
        expected=ExpectedDiagnosis(category="component_down", component="redis"),
    ),
    EvalCase(
        id="INC-003",
        incident_id="INC-003",
        symptom=(
            "Не открываются профили пользователей и каталог товаров: API отвечает 503."
        ),
        expected=ExpectedDiagnosis(category="component_down", component="postgres"),
    ),
    EvalCase(
        id="INC-004",
        incident_id="INC-004",
        symptom=ORDER_502,
        expected=ExpectedDiagnosis(
            category="dependency_misconfiguration",
            component="order-service",
            dependency="user-service",
        ),
    ),
    EvalCase(
        id="INC-005",
        incident_id="INC-005",
        symptom=(
            "Оформление заказа не проходит: POST /orders отвечает 504 Gateway "
            "Timeout примерно через 2 секунды."
        ),
        expected=ExpectedDiagnosis(
            category="dependency_slow", component="user-service"
        ),
    ),
    EvalCase(
        id="INC-006",
        incident_id="INC-006",
        symptom=REDIS_ALERT,
        expected=ExpectedDiagnosis(
            category="dependency_misconfiguration",
            component="order-service",
            dependency="redis",
        ),
    ),
    EvalCase(
        id="healthy",
        incident_id=None,
        symptom=(
            "Один пользователь написал в поддержку, что сайт «как-то странно "
            "работает». Других жалоб нет. Проверь, есть ли проблема."
        ),
        expected=ExpectedDiagnosis(category="no_incident", component="none"),
    ),
]
CASES_BY_ID = {case.id: case for case in CASES}


def select_cases(
    ids: list[str] | None, generic_symptom: bool = False
) -> list[EvalCase]:
    unknown = [case_id for case_id in ids or [] if case_id not in CASES_BY_ID]
    if unknown:
        raise ValueError(f"Unknown cases: {', '.join(unknown)}")
    cases = [CASES_BY_ID[case_id] for case_id in ids] if ids else list(CASES)
    if generic_symptom:
        cases = [case.model_copy(update={"symptom": GENERIC_SYMPTOM}) for case in cases]
    return cases
