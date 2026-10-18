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

## Контракт результата

Каждый вызов возвращает Pydantic-модель `DiagnosticResult` с полями `tool`, `status`, `data` и `error` (`null` или `{type, message}`). `model_dump_json()` сериализует её. `ok` означает, что проверка завершилась и целевой компонент исправен. `degraded` означает, что проверка завершилась и обнаружила неисправность, включая отказ в соединении или таймаут. `error` означает некорректный ввод, отсутствие учётных данных, отказ в доступе со стороны Executor или непригодный диагностический ответ. Сообщения об ошибках не содержат подробностей исключений зависимостей и учётных данных.

## Функции

- `get_service_status(service)`: состояние, статус работоспособности и время запуска Compose-контейнера из разрешённого списка.
- `get_service_logs(service, tail=50, since_seconds=None)`: текст логов с временными метками и ограниченным объёмом, от 1 до 200 строк и необязательное временное окно от 1 до 86400 секунд. Размер ответа не превышает 60 КБ, также сообщается об усечении. Строки, содержащие названия полей, связанных с секретами, исключаются Executor.
- `check_http_endpoint(service, path, timeout_seconds=None)`: только GET-запросы к User Service `/health`, `/health/db`, `/users/0` или Order Service `/health`, `/health/db`, `/health/redis`. Перенаправления не выполняются. Размер ответа ограничен 8 КБ.
- `check_postgres()`: прямой запрос `SELECT 1` в транзакции только для чтения.
- `check_redis()`: прямой `PING`.
- `get_service_metrics(service)`: текущие счётчики из разрешённого списка, получаемые из существующего эндпоинта `/internal/stats`.
- `check_service_dependency(source_service, dependency)`: проверка из Order Service с использованием фактического адреса зависимости.
- `get_dependency_config(service, dependency)`: безопасные значения хоста, порта и таймаута, которые в данный момент использует Order Service.

Функции получения состояния и логов принимают `user-service`, `order-service`, `postgres`, `redis`, `frontend`. HTTP-проверки принимают только два бизнес-сервиса. Неизвестные целевые компоненты возвращают `error`.

## Конфигурация

Установите `USER_SERVICE_URL`, `ORDER_SERVICE_URL`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `REDIS_HOST`, `REDIS_PORT`, `DIAGNOSTIC_POSTGRES_USER`, `DIAGNOSTIC_POSTGRES_PASSWORD`, `DOCKER_EXECUTOR_READ_URL`, `DOCKER_EXECUTOR_READ_TOKEN`, `DIAGNOSTICS_TOKEN`. По умолчанию используются localhost и порты 8001, 8002, 5432, 6379 и 8005. В контейнере агента внутри Compose используйте `http://user-service:8001`, `http://order-service:8002`, `postgres`, `redis` и `http://docker-executor:8004`. Контейнер агента должен быть подключён к сети Executor для доступа к состоянию и логам, без монтирования Docker Socket. Никогда не передавайте ему `DOCKER_EXECUTOR_TOKEN` или `INCIDENT_CONTROLLER_TOKEN`.

Таймауты: `DIAGNOSTIC_HTTP_TIMEOUT_SECONDS=5`, `DIAGNOSTIC_POSTGRES_TIMEOUT_SECONDS=3`, `DIAGNOSTIC_REDIS_TIMEOUT_SECONDS=3`, `DIAGNOSTIC_DOCKER_TIMEOUT_SECONDS=5`.

`make setup` автоматически создаёт отдельные случайные значения `DOCKER_EXECUTOR_READ_TOKEN`, `DIAGNOSTICS_TOKEN` и `DIAGNOSTIC_POSTGRES_PASSWORD` в `.env`, если они отсутствуют; уже заполненные значения сохраняются. Сервис Compose `diagnostic-db-init` создаёт роль только с правами входа и подключения к базе данных, в том числе при использовании существующего тома. Повторно запустите этот одноразовый сервис после изменения пароля пользователя для чтения. Его роль не имеет прав доступа к таблицам. Executor отклоняет токен для чтения, совпадающий с его привилегированным токеном. Nginx-прокси, доступный только через localhost, предоставляет исключительно GET-маршруты `/read-only/services/...` на порту 8005. Он и Docker Executor используют отдельную сеть `diagnostic_access`. Привилегированные операции не имеют опубликованного на хосте порта, а прокси не перенаправляет запросы к их маршрутам.

## Использование

После быстрого запуска можно отдельно запустить тесты:

```sh
uv run pytest services/diagnostic-tools/tests
make diagnostic-test
make diagnostic-test-advanced
```

```python
import asyncio
from diagnostic_tools import get_service_status


async def main():
    print((await get_service_status("user-service")).model_dump_json())


asyncio.run(main())
```

Автономный демонстрационный скрипт читает `.env` репозитория для разрешённых диагностических настроек, если они ещё не заданы в окружении процесса. Сама библиотека `diagnostic_tools` не загружает `.env`; приложения, импортирующие её, должны передавать настройки через переменные окружения (например, `uv run --env-file .env --package diagnostic-tools python your_script.py`). Демонстрационный скрипт завершается с выводом списка названий отсутствующих учётных данных, если три учётных параметра для доступа только на чтение не заданы.

`make diagnostic-test` запускается во временном диагностическом тестовом контейнере, изменяет состояние контейнеров через Incident Controller строго в рамках тестовой оркестрации и выполняет сброс в блоке `finally`. Держите его отдельно от обычного `make test`. Для создания роли базы данных требуется непустой диагностический пароль. Для выполнения интеграционной команды Docker должен быть запущен.

## Диагностика взаимодействий

### `get_service_metrics(service)`

Принимает `user-service` или `order-service`. Читает ответ существующего `GET /internal/stats` и возвращает только фактически присутствующие счётчики в `data.metrics`: `requests_total`, `errors_total`, `cache_hits`, `cache_misses`, `dependency_timeouts_total`, `dependency_errors_total`, `redis_connection_errors_total` и выбранные значения `database_pool`, если они присутствуют. Отсутствующие счётчики пропускаются. Эти счётчики накапливаются в течение времени работы процесса и сбрасываются при перезапуске. Задержка HTTP-запросов находится в JSON-логах, а не в агрегированных метриках. `status=error` означает некорректный сервис, недоступный эндпоинт, таймаут или непригодный ответ. Функция не отправляет токен или запрос на изменение данных.

```python
result = await get_service_metrics("order-service")
# {"tool":"get_service_metrics","status":"ok","data":{"service":"order-service","metrics":{"requests_total":12,"cache_hits":3}},"error":null}
```

### `check_service_dependency(source_service, dependency)`

Принимает только `order-service` в качестве источника и `user-service` или `redis` в качестве зависимости. Вызывает фиксированный аутентифицированный маршрут Order Service `GET /internal/diagnostics/dependencies/{dependency}`. Order Service проверяет User Service с помощью `GET /users/0` с существующим бизнес-таймаутом в 2 секунды или Redis с помощью `PING` с существующим таймаутом Redis. Эндпоинт использует тот же механизм выбора фактического адреса, что и бизнес-операции, включая переопределения во время выполнения. Ответ 404 от User Service означает, что сетевой путь работает: пользователь не создаётся. `data` содержит источник, зависимость, доступность, задержку и HTTP-статус, если применимо. Завершённая неуспешная проверка возвращает `degraded` с `timeout` или `connection_refused`; некорректные аргументы, отсутствующий токен, отказ в доступе или сбой Order Service возвращают `error`. Передача произвольного URL или метода, изменяющего данные, не допускается.

```python
result = await check_service_dependency("order-service", "user-service")
# {"tool":"check_service_dependency","status":"degraded","data":{"source_service":"order-service","dependency":"user-service","reachable":false,"latency_ms":2000},"error":{"type":"timeout","message":"Dependency probe failed"}}
```

### `get_dependency_config(service, dependency)`

Принимает ту же пару. Вызывает фиксированный аутентифицированный маршрут `GET /internal/diagnostics/config/{dependency}`. `data` содержит только сервис, зависимость, фактический хост, порт и таймаут. Не включает имена пользователей, пароли, полные URL, все переменные окружения, состояние неисправности и идентификаторы инцидентов. Некорректная пара, отклонённый токен или недоступный сервис возвращают `error`. Фактическая конфигурация считывается из Order Service в момент вызова.

```python
result = await get_dependency_config("order-service", "redis")
# {"tool":"get_dependency_config","status":"ok","data":{"service":"order-service","dependency":"redis","host":"redis","port":9999,"timeout_seconds":1.0},"error":null}
```

### Медленный User Service

`check_http_endpoint("user-service", "/users/0", timeout_seconds=7)` позволяет явно задать ограниченный таймаут не более 15 секунд. Функция измеряет полное время ответа и может обнаружить 5-секундную задержку в INC-005 без создания пользователя. Ответ 404 по-прежнему содержит `reachable=true` и `latency_ms`; его `status` равен `degraded`, поскольку это не HTTP-ответ 2xx. Проверка зависимости со стороны Order Service использует неизменённый бизнес-таймаут и поэтому сообщает о таймауте во время того же инцидента.

Новые маршруты Order Service требуют отдельного токена `DIAGNOSTICS_TOKEN` в заголовке. Они исключены из OpenAPI, не маршрутизируются через frontend и принимают только GET. Если токен не задан, они отключены. Маршрут управления неисправностями отклоняет токен, совпадающий с `DIAGNOSTICS_TOKEN`. Диагностический пакет никогда не читает Fault Registry и не вызывает Incident Controller; только запускаемый по явному запросу исполнитель интеграционных тестов использует Incident Controller для активации и сброса сценариев.

`make diagnostic-test-advanced` последовательно проверяет INC-004, INC-005 и INC-006 со сбросом в блоке `finally` и проверками восстановления. Он не входит в `make test`.
