# Диагностические инструменты

`services/diagnostic-tools` — устанавливаемый Python-пакет, не требующий отдельного сервера. Его восемь асинхронных функций наблюдают за магазином через HTTP GET-проверки по разрешённому списку адресов, прямой запрос PostgreSQL `SELECT 1`, Redis `PING` и эндпоинты Docker Executor только для чтения. Они никогда не импортируют и не вызывают Incident Controller. Они не используют доступ к Docker Socket.

## Быстрый запуск для разработчика

На компьютере должны быть установлены Python 3.12, `uv`, `make`, Docker с поддержкой Compose; Docker должен быть запущен. Из корня репозитория выполните команды по порядку:

```sh
uv sync --package diagnostic-tools
make setup
make up
uv run --package diagnostic-tools python examples/diagnostic_tools_demo.py
```

`uv sync --package diagnostic-tools` устанавливает пакет из uv workspace и его зависимости в проектное окружение. `make setup` создаёт `.env` из `.env.example`, если файла ещё нет, и генерирует только отсутствующие диагностические и служебные токены и пароль роли PostgreSQL для чтения; заполненные значения сохраняются. Команда не запускает Docker и не меняет уже существующий пароль PostgreSQL. `make up` собирает и запускает приложение с его зависимостями. Последняя команда вызывает все восемь инструментов и печатает по одному JSON-результату на строку. Для обычного запуска примера отдельные переменные окружения задавать не требуется: скрипт читает настройки из локального `.env`.

Если Docker Compose использует ранее созданный том PostgreSQL, его существующий административный пароль должен совпадать с текущей конфигурацией: `make setup` его не заменяет. Подробности настроек, ошибок и тестов приведены ниже.

## Проверка на инциденте

После быстрого запуска можно включить один из тестовых инцидентов и повторить диагностику. Например, для остановки User Service:

```sh
make incident-list
make incident-status
make incident-activate ID=INC-001
uv run --package diagnostic-tools python examples/diagnostic_tools_demo.py
make incident-reset
make incident-status
uv run --package diagnostic-tools python examples/diagnostic_tools_demo.py
```

`make incident-list` показывает доступные сценарии; `make incident-status` — активный сценарий или `{"incident":null}`. Команда `make incident-activate ID=...` включает выбранный сценарий. Первый запуск примера показывает результаты инструментов во время неисправности, второй — после сброса. В показанном примере полезно сравнить `get_service_status("user-service")` и `check_http_endpoint("user-service", "/health")`: остановленный контейнер и недоступный HTTP-адрес должны отразиться в их результатах. `make incident-reset` выполните и в случае, если проверка прервана или завершилась ошибкой.

Для остальных сценариев замените ID и сравните соответствующие функции:

| Сценарий | Основные проверки |
| --- | --- |
| `INC-001` | `get_service_status("user-service")`, `check_http_endpoint("user-service", "/health")`, `get_service_logs("user-service")` |
| `INC-002` | `get_service_status("redis")`, `check_redis()`, `check_service_dependency("order-service", "redis")` |
| `INC-003` | `get_service_status("postgres")`, `check_postgres()`, `check_http_endpoint("user-service", "/health/db")`, `check_http_endpoint("order-service", "/health/db")` |
| `INC-004` | `check_http_endpoint("user-service", "/health")`, `check_service_dependency("order-service", "user-service")`, `get_dependency_config("order-service", "user-service")` |
| `INC-005` | `check_http_endpoint("user-service", "/users/0", timeout_seconds=7)`, `check_service_dependency("order-service", "user-service")` |
| `INC-006` | `check_redis()`, `check_service_dependency("order-service", "redis")`, `get_dependency_config("order-service", "redis")` |

Демонстрационный скрипт вызывает фиксированный набор всех восьми функций. Для проверок из таблицы с другими аргументами импортируйте нужные функции в свой Python-код; пример такого вызова приведён ниже. Команды `make incident-*` управляют только тестовым стендом. Сами функции `diagnostic_tools` не активируют и не сбрасывают инциденты и не получают диагноз от Incident Controller.

## Формат результата

Каждая функция возвращает Pydantic-модель `DiagnosticResult`, которую можно сериализовать через `model_dump_json()`:

```json
{"tool":"check_redis","status":"ok","data":{"reachable":true,"ping":true,"latency_ms":2.08},"error":null}
```

| `status` | Значение |
| --- | --- |
| `ok` | Проверка выполнена, проблема не обнаружена. |
| `degraded` | Проверка выполнена и выявила недоступность, таймаут или другой симптом. |
| `error` | Проверку не удалось выполнить: например, неверные аргументы, нет учётных данных или доступа. |

`error`, если присутствует, содержит `type` и `message`. Подробности исключений и учётные данные не возвращаются.

## Справочник функций

| Функция | Что возвращает и что принимает |
| --- | --- |
| `get_service_status(service)` | Состояние контейнера, Docker health и время запуска. `service`: `user-service`, `order-service`, `postgres`, `redis` или `frontend`. |
| `get_service_logs(service, tail=50, since_seconds=None)` | Логи с временными метками и признаком усечения. Те же сервисы; `tail` от 1 до 200, `since_seconds` от 1 до 86400; ответ не более 60 КБ. |
| `check_http_endpoint(service, path, timeout_seconds=None)` | Доступность, HTTP-код и задержка. Только `GET`; для `user-service` разрешены `/health`, `/health/db`, `/users/0`, для `order-service` — `/health`, `/health/db`, `/health/redis`. Ответ не более 8 КБ; перенаправления не выполняются. |
| `check_postgres()` | Прямое соединение с PostgreSQL и результат `SELECT 1` в транзакции только для чтения. |
| `check_redis()` | Прямой `PING` Redis. |
| `get_service_metrics(service)` | Счётчики `/internal/stats` для `user-service` или `order-service`: запросы, ошибки, кеш, ошибки зависимостей и снимок пула БД, если соответствующие поля присутствуют. |
| `check_service_dependency(source_service, dependency)` | Доступность зависимости из `order-service`; `dependency` — `user-service` или `redis`. Использует фактический адрес и таймаут сервиса. |
| `get_dependency_config(service, dependency)` | Фактические хост, порт и таймаут зависимости `user-service` или `redis` для `order-service`; без полного URL и секретов. |

Неизвестный сервис, путь или недопустимые параметры дают `status=error`. Прямые `check_postgres()` и `check_redis()` проверяют доступность с места запуска инструментов; `check_service_dependency()` проверяет соединение именно из Order Service.

### Нюансы интерпретации

`GET /users/0` не создаёт пользователя и обычно возвращает `404`. При `reachable=true` это подтверждает рабочее соединение, хотя `check_http_endpoint()` присваивает ответу `status=degraded` из-за HTTP-кода. Проверка зависимости Order Service считает такой `404` успешной связью. Параметр `timeout_seconds=7` у `check_http_endpoint()` позволяет измерить медленный ответ; допустимый максимум — 15 секунд. Бизнес-таймаут Order Service для User Service по умолчанию составляет 2 секунды.

Счётчики `get_service_metrics()` накапливаются только в памяти процесса и обнуляются при его перезапуске. Диагностические проверки зависимости не увеличивают счётчики бизнес-ошибок. Задержки отдельных HTTP-запросов находятся в JSON-логах.

## Конфигурация

Демонстрационный скрипт читает локальный `.env`. При прямом импорте библиотека использует переменные окружения процесса; она не загружает `.env` самостоятельно. Для своего скрипта можно передать файл явно: `uv run --env-file .env --package diagnostic-tools python your_script.py`.

| Назначение | Локально | В контейнере Compose |
| --- | --- | --- |
| `USER_SERVICE_URL` | `http://127.0.0.1:8001` | `http://user-service:8001` |
| `ORDER_SERVICE_URL` | `http://127.0.0.1:8002` | `http://order-service:8002` |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `127.0.0.1`, `5432` | `postgres`, `5432` |
| `REDIS_HOST`, `REDIS_PORT` | `127.0.0.1`, `6379` | `redis`, `6379` |
| `DOCKER_EXECUTOR_READ_URL` | `http://127.0.0.1:8005` | `http://docker-executor:8004` |

Дополнительно используются `POSTGRES_DB`, `DIAGNOSTIC_POSTGRES_USER`, `DIAGNOSTIC_POSTGRES_PASSWORD`, `DOCKER_EXECUTOR_READ_TOKEN` и `DIAGNOSTICS_TOKEN`. Последние три значения заполняет `make setup`; демонстрационный скрипт сообщает названия отсутствующих параметров, не выводя секреты. Для доступа к Executor из контейнера нужна сеть `executor`, без монтирования Docker socket.

| Таймаут | Значение по умолчанию |
| --- | --- |
| `DIAGNOSTIC_HTTP_TIMEOUT_SECONDS` | 5 секунд |
| `DIAGNOSTIC_POSTGRES_TIMEOUT_SECONDS` | 3 секунды |
| `DIAGNOSTIC_REDIS_TIMEOUT_SECONDS` | 3 секунды |
| `DIAGNOSTIC_DOCKER_TIMEOUT_SECONDS` | 5 секунд |

Роль PostgreSQL для диагностики создаёт одноразовый сервис `diagnostic-db-init`; она не имеет доступа к таблицам. Если пароль этой роли изменён после запуска Compose, сервис нужно запустить повторно. Локальный прокси Docker Executor публикует только маршруты чтения на `127.0.0.1:8005`; управляющие маршруты на хост не опубликованы. Не передавайте диагностическому клиенту `DOCKER_EXECUTOR_TOKEN` или `INCIDENT_CONTROLLER_TOKEN`.

Проверки зависимости и конфигурации используют защищённые GET-маршруты Order Service с отдельным `DIAGNOSTICS_TOKEN`. Диагностические функции не изменяют данные и не обращаются к Incident Controller.

## Вызов из Python

```python
import asyncio
from diagnostic_tools import get_service_status


async def main():
    result = await get_service_status("user-service")
    print(result.model_dump_json())


asyncio.run(main())
```

Все восемь функций можно импортировать из `diagnostic_tools`. Пример в `examples/diagnostic_tools_demo.py` вызывает их без отдельного сервера.

## Тесты

```sh
uv run pytest services/diagnostic-tools/tests
make diagnostic-test
make diagnostic-test-advanced
```

Первая команда запускает модульные тесты без Docker Compose. Две команды `make` требуют запущенного Docker: первая проверяет INC-001–INC-003, вторая — INC-004–INC-006. Они временно меняют состояние стенда и сбрасывают сценарии в `finally`; в обычный `make test` эти проверки не входят.
