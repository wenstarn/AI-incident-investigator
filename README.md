# AI Incident Investigator

Монорепозиторий проекта для исследования инцидентов с помощью AI. Сейчас в нём
находится тестовый интернет-магазин с User Service и Order Service. Отдельный
Incident Controller создаёт управляемые неисправности; AI Agent будет реализован
на следующих этапах.

## Требования

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker с Docker Compose
- `psql` (необязательно, для ручной диагностики)

Установить `uv` можно официальным установщиком:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Установка

Из корня репозитория установите зависимости всех пакетов workspace:

```bash
uv sync --all-packages
```

Команда `make install` выполняет ту же операцию. `uv` автоматически использует
версию Python из `.python-version` и создаёт общее виртуальное окружение
`.venv` в корне.

## Запуск всего приложения в Docker

Создайте локальный файл настроек и запустите весь стек одной командой:

```bash
test -f .env || cp .env.example .env
make up
```

Docker Compose собирает образы, дожидается PostgreSQL, применяет миграции обеих
схем, идемпотентно добавляет начальный каталог и запускает API с frontend.
Incident Controller и внутренний Docker Executor также запускаются через Compose.
Магазин доступен на `http://localhost:3000`. Backend также опубликован только
на loopback-интерфейсе: User Service на `http://localhost:8001`, Order Service
на `http://localhost:8002`.

Управление стеком:

```bash
make ps       # состояние контейнеров
make logs     # общие логи, выход — Ctrl+C
make build    # пересборка образов
make down     # остановка без удаления postgres_data
```

`make down` не удаляет named volume `postgres_data`, поэтому пользователи,
заказы и товары сохраняются между запусками. Не используйте
`docker compose down -v`, если эти данные нужны.

## Running and Testing Incidents

Clone the repository, create `.env`, and generate three **different** secrets:

```bash
git clone <repository-url> AI-incident-investigator
cd AI-incident-investigator
cp .env.example .env
# Generate three values with: openssl rand -hex 32
# Set FAULT_INJECTION_TOKEN, INCIDENT_CONTROLLER_TOKEN, DOCKER_EXECUTOR_TOKEN in .env.
make up
```

The frontend is at `http://127.0.0.1:3000`; User Service, Order Service,
and Incident Controller listen on loopback ports 8001, 8002, and 8003.
`GET /health` on port 8003 needs no token. All other controller routes require
`INCIDENT_CONTROLLER_TOKEN`; the Makefile reads it from `.env` without printing it.

```bash
make incident-list
make incident-activate ID=INC-001
make incident-status
make incident-logs     # Ctrl+C to stop following logs
make incident-reset
make incident-test     # intentionally disrupts the running local stack
make down              # preserves the PostgreSQL volume
```

Repeat activate/status/reset with each ID. `make incident-test` runs the six
scenarios in sequence, restores each one, and verifies that a second reset is
safe. It creates one test user and leaves that record in PostgreSQL. Ordinary
`make test` does not run these destructive tests.

| ID | Fault | Expected symptom |
|---|---|---|
| INC-001 | User Service stopped | User health unavailable; order creation fails |
| INC-002 | Redis stopped | Cache health fails; product reads use PostgreSQL fallback |
| INC-003 | PostgreSQL stopped | Database health fails in both services |
| INC-004 | Wrong User Service port | Order creation returns 502 |
| INC-005 | Slow User Service | Order creation returns 504 |
| INC-006 | Wrong Redis port | Redis container stays healthy; product reads fall back to PostgreSQL |

Docker Executor is reachable only on a private Compose network shared with
Incident Controller. It accepts only status, stop, start, and health-wait
operations for `user-service`, `redis`, and `postgres`, selected by labels of
the current Compose project. The controller has no Docker socket mount.
The executor **does** mount the Docker socket, which grants it high privileges
on the Docker host. Use this stack only in a trusted local test environment;
never expose the executor or controller to an untrusted network. Controller
state is in memory, so reset an active incident before recreating it.

Быстрые проверки после запуска:

```bash
curl http://localhost:8001/health
curl http://localhost:8001/health/db
curl http://localhost:8002/health
curl http://localhost:8002/health/db
curl http://localhost:8002/health/redis
curl http://localhost:3000/api/products
docker compose exec postgres pg_isready -U app -d shop
docker compose exec redis redis-cli ping
```

`user-migrate` и `order-migrate` — одноразовые контейнеры. Каждый использует
собственный Alembic-конфиг и таблицу версий в своей PostgreSQL-схеме. API
стартуют только после успешного завершения соответствующей миграции. Команды
`upgrade head` повторяемы: уже применённые ревизии не выполняются снова.
Одноразовый `order-seed` запускается после миграции Order Service и добавляет
только отсутствующие товары.

Все контейнеры находятся в создаваемой Compose сети и обращаются друг к другу
по DNS-именам сервисов: `postgres:5432`, `redis:6379` и
`user-service:8001`. Nginx раздаёт статические файлы и проксирует
`/api/users/*` в User Service, а `/api/products/*` и `/api/orders/*` — в
Order Service. Docker-образ frontend подменяет только `config.js`, задавая
относительный base URL `/api`; локальный `make run-frontend` продолжает
использовать адреса API на `localhost`.

## PostgreSQL, Redis и конфигурация

Создайте локальный файл окружения из примера. Файл `.env` не следует
добавлять в Git или заполнять реальными production-секретами:

```bash
cp .env.example .env
```

Для запуска User Service непосредственно на хосте `DATABASE_URL` должен
указывать на опубликованный порт PostgreSQL на `localhost`. Значения в
`.env.example` согласованы с локальной конфигурацией `docker-compose.yml`.
Контейнеры используют отдельную переменную `COMPOSE_DATABASE_URL` с Docker
DNS-именем `postgres`; при смене пользователя, пароля или имени БД обновите
также эту строку подключения.
Переменная `USER_SERVICE_DB_SCHEMA` по умолчанию равна `user_service`.
Для локального запуска `USER_SERVICE_URL=http://localhost:8001`. Значение не
зашито в код: Docker Compose использует `http://user-service:8001`. Таймаут вызова задаётся переменной
`USER_SERVICE_TIMEOUT_SECONDS`.

Order Service подключается к Redis по `REDIS_URL`; для локального запуска это
`redis://localhost:6379/0`. Таймаут Redis задаётся через
`REDIS_TIMEOUT_SECONDS`, а TTL карточки товара — через
`PRODUCT_CACHE_TTL_SECONDS` (по умолчанию 300 секунд).

Запустите только существующий контейнер PostgreSQL (volume с данными при этом
сохраняется):

```bash
make db-up
```

Остановить PostgreSQL без удаления volume и данных:

```bash
make db-down
```

Redis запускается и останавливается отдельно, не затрагивая PostgreSQL:

```bash
make redis-up
docker compose exec redis redis-cli ping
make redis-down
```

Проверка должна вывести `PONG`.

## Миграции

Примените миграции User Service из корня репозитория:

```bash
make db-migrate
```

Создание следующей автогенерируемой миграции:

```bash
make db-revision MESSAGE="describe the change"
```

Alembic создаёт таблицы `users` и `alembic_version` в отдельной схеме
`user_service`; миграции User Service не просматривают и не изменяют таблицы
других сервисов. Откат последней миграции можно выполнить командой:

```bash
uv run --package user-service alembic -c services/user-service/alembic.ini downgrade -1
```

## Запуск сервисов

Для запуска без Docker сначала запустите инфраструктуру и миграции:

```bash
make db-up
make redis-up
make db-migrate
make order-db-migrate
make order-db-seed
```

Затем запускайте процессы непосредственно на хосте:

```bash
uv run --package user-service uvicorn user_service.main:app --port 8001
uv run --package order-service uvicorn order_service.main:app --port 8002
```

Альтернативно используйте `make run-user` и `make run-order`. Makefile читает
локальный `.env`, если он существует. Liveness User Service доступен на
`/health`, реальная проверка PostgreSQL — на `/health/db`.

Создание и получение пользователя:

```bash
curl -i -X POST http://localhost:8001/users \
  -H 'Content-Type: application/json' \
  -d '{"name":"Alice","email":"alice@example.com"}'
curl -i http://localhost:8001/users/1
curl -i http://localhost:8001/health/db
```

## Проверка PostgreSQL вручную

Подключиться через контейнер, не публикуя пароль в команде:

```bash
docker compose exec postgres psql -U app -d shop
```

В интерактивном `psql` посмотреть схемы и таблицы User Service:

```text
\dn
\dt user_service.*
\d user_service.users
SELECT id, name, email FROM user_service.users;
```

## Frontend

Frontend написан на HTML, CSS и Vanilla JavaScript. После запуска обоих API
откройте его через локальный HTTP-сервер:

```bash
make run-frontend
```

Страница доступна на `http://localhost:3000`. Она позволяет создавать и искать
пользователей, просматривать товары, создавать заказы и загружать историю
заказов пользователя. Адреса API задаются в `frontend/config.js`; их можно
изменить без правки основной логики интерфейса.

Допустимые origins обоих FastAPI-сервисов задаются разделённым запятыми списком
в `CORS_ALLOWED_ORIGINS`. Значение из `.env.example` разрешает локальный
frontend на `localhost:3000` и `127.0.0.1:3000`.

## Order Service

Order Service хранит товары и заказы в отдельной PostgreSQL-схеме
`order_service` и не обращается к таблицам User Service. Перед созданием заказа
он вызывает `GET /users/{id}` по адресу `USER_SERVICE_URL`.

Применить миграцию и идемпотентно добавить десять тестовых товаров:

```bash
make db-up
make order-db-migrate
make order-db-seed
```

Затем запустите сервисы в двух терминалах из корня репозитория:

```bash
make run-user
```

```bash
make run-order
```

Для создания заказа сначала создайте пользователя в User Service. Если
пользователь отсутствует, Order Service возвращает `404`. Недоступный User
Service и неожиданный ответ дают `502`, истечение таймаута — `504`. Повторные
HTTP-запросы автоматически не выполняются.

Создание следующей миграции:

```bash
make order-db-revision MESSAGE="describe the change"
```

Примеры проверки API:

```bash
curl 'http://localhost:8002/products?limit=5&offset=0'
curl http://localhost:8002/products/1
curl -X POST http://localhost:8002/orders \
  -H 'Content-Type: application/json' \
  -d '{"user_id":1,"product_id":1,"quantity":2}'
curl http://localhost:8002/orders/1
curl 'http://localhost:8002/orders?user_id=1&sort_order=desc&limit=20'
curl http://localhost:8002/health/db
curl http://localhost:8002/health/redis
```

Посмотреть таблицы и данные:

```bash
docker compose exec postgres psql -U app -d shop
```

В `psql`:

```text
\dt order_service.*
\d order_service.products
\d order_service.orders
SELECT id, name, price, stock FROM order_service.products ORDER BY id;
```

Цена хранится как PostgreSQL `numeric(12,2)` и Python `Decimal`. Уменьшение
остатка выполняется условным атомарным `UPDATE`; заказ создаётся в той же
транзакции. Ограничение БД дополнительно запрещает отрицательный `stock`.

`GET /products/{id}` использует cache-aside: после обязательного чтения товара
из PostgreSQL Order Service берёт `name` и `price` из ключа Redis
`product:{id}`, а при промахе записывает их туда на 300 секунд. Поле `stock`
никогда не кешируется и всегда берётся из PostgreSQL. Поэтому удалённый из БД
товар нельзя получить из устаревшего кеша. При ошибке чтения или записи Redis
ошибка логируется, а запрос продолжает работать через PostgreSQL. Реальную
доступность кеша показывает `GET /health/redis`: `200` при успешном `PING` и
`503` при недоступности Redis.

## Проверки и форматирование

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Обычный запуск выполняет быстрые тесты и пропускает PostgreSQL-интеграционные
тесты, если `TEST_DATABASE_URL` не задан. Для полной проверки запустите
PostgreSQL, задайте `TEST_DATABASE_URL` и выполните:

```bash
make db-up
uv run pytest
```

Интеграционные тесты создают случайные схемы вида
`test_user_service_<uuid>` и `test_order_service_<uuid>`, применяют к ним
миграции и удаляют только эти схемы. Обычные схемы разработки и их данные не
очищаются. HTTP-ответы User Service в тестах Order Service эмулируются через
`httpx.MockTransport`.

Для автоматического форматирования запустите `make format`. Команды `make lint`
и `make test` выполняют статические проверки и тесты соответственно.

### Полная интеграционная проверка

Запустите PostgreSQL и Redis, затем передайте тестовые адреса зависимостей:

```bash
make db-up
make redis-up
TEST_DATABASE_URL=postgresql+psycopg://app:devpassword@localhost:5432/shop \
TEST_REDIS_URL=redis://localhost:6379/15 \
uv run pytest
```

PostgreSQL-тесты используют временные уникальные схемы и удаляют только эти
схемы. Redis-тест создаёт случайный ключ в отдельной logical database `15` и
удаляет только этот ключ. Схемы приложения, пользовательские строки и Docker
volumes тестами pytest не изменяются.

### Smoke-тест полного магазина

Для сборки, запуска и end-to-end проверки всего приложения выполните:

```bash
make smoke-test
```

Команда оставляет приложение запущенным и проверяет:

- frontend, health и database health endpoints;
- создание и повторное чтение пользователя;
- каталог, карточку товара и создание заказа;
- уменьшение stock и защиту от overselling при конкурентных заказах;
- сохранение пользователя и заказа после перезапуска backend-контейнеров;
- Redis cache miss, следующий за ним cache hit и TTL;
- чтение товара через PostgreSQL fallback при остановленном Redis;
- ответ `502` и неизменный stock при остановленном User Service;
- JSON-логи и один Request ID в Order Service и User Service.

Smoke-тест создаёт уникальные записи с UUID и поэтому не зависит от заранее
существующих ID. Он не очищает таблицы, не удаляет существующие данные и не
вызывает `docker compose down -v`. Временно остановленные Redis и User Service
запускаются снова даже при ошибке теста. Созданные smoke-пользователь, товар и
заказы намеренно остаются в PostgreSQL для проверки persistence.

После проверки посмотреть состояние и логи, а затем остановить приложение без
удаления данных можно так:

```bash
make ps
make logs
make down
```

## Логи и минимальная диагностика

User Service и Order Service записывают JSON-события в stdout. Каждый HTTP
запрос получает `X-Request-ID`: входящее значение сохраняется, а при его
отсутствии сервис генерирует новое. Order Service передаёт этот идентификатор
при HTTP-вызове User Service, поэтому связанные события можно найти в логах
обоих контейнеров:

```bash
docker compose logs user-service order-service
curl -i -H 'X-Request-ID: manual-check-1' http://localhost:8002/products
```

События `http_request` и `http_response` содержат метод, путь, HTTP-статус и
длительность. Отдельно регистрируются ошибки PostgreSQL, Redis и User Service,
таймауты пула соединений и медленные SQL-запросы. SQL и параметры запросов не
попадают в лог; для медленного запроса записывается только безопасный hash-ID.
Порог задаётся переменной `SLOW_QUERY_THRESHOLD_MS` и по умолчанию равен 500
мс. Пароли, DSN, токены и тела HTTP-запросов не логируются.

Текущие process-local счётчики и состояние SQLAlchemy pool доступны внутри
каждого сервиса:

```bash
curl http://localhost:8001/internal/stats
curl http://localhost:8002/internal/stats
```

Счётчики `requests_total`, `errors_total`, `cache_hits` и `cache_misses`
сбрасываются при перезапуске процесса. Статистика Order Service отражает
использование Redis-кеша; User Service возвращает нули для cache counters.

## Структура

- `services/user-service` — FastAPI-сервис пользователей;
- `services/order-service` — FastAPI-сервис товаров и заказов;
- `frontend` — статический клиент и его Nginx-образ;
- `infrastructure/nginx` — reverse proxy и Docker-вариант frontend-конфигурации;
- `docs` — архитектура и проектная документация.

Каждый сервис является отдельным Python-проектом и явно объявляет runtime-
зависимости. Корневой `pyproject.toml` объединяет их в единый uv Workspace,
хранит общие dev-зависимости и конфигурацию инструментов. Во всём репозитории
используются одно корневое виртуальное окружение и один `uv.lock`.
## Runtime fault injection: INC-004–006

The protected internal fault APIs keep their in-memory registries and use
`FAULT_INJECTION_TOKEN`. INC-004 changes Order Service's User Service URL,
INC-005 delays User Service lookups, and INC-006 changes Order Service's Redis
URL. The controller reaches both services by Docker DNS. Incidents INC-004–INC-006 have no expiry and remain active until
`make incident-reset`. Their settings are process-local, so restarting the
affected service also clears them. Direct fault API requests retain their
default 300-second TTL unless they explicitly send `ttl_seconds: null`. Use
`USER_SERVICE_TIMEOUT_SECONDS` below five seconds for the INC-005 timeout.
