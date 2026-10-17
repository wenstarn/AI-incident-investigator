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
cp .env.example .env
make up
```

Docker Compose собирает образы, дожидается PostgreSQL, применяет миграции обеих
схем, идемпотентно добавляет начальный каталог и запускает API с frontend.
Incident Controller запускается отдельно на хосте и не входит в Compose.
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

## Incident Controller

После запуска магазина через Compose запустите контроллер на хосте:

```bash
cd services/incident-controller
uv sync --frozen
INCIDENT_COMPOSE_PROJECT=ai-incident-investigator uv run uvicorn incident_controller.main:app --host 127.0.0.1 --port 8003 --workers 1
```

`INCIDENT_COMPOSE_PROJECT` должен совпадать с именем Compose-проекта
(`docker compose config --format json` показывает поле `name`). Можно также
использовать `COMPOSE_PROJECT_NAME`. Контроллер выбирает контейнеры только по
labels проекта и сервисов `user-service` или `redis`; доступ к Docker API
нужен процессу на хосте. API доступен только локально на `127.0.0.1:8003`.

```bash
curl http://127.0.0.1:8003/incidents
curl -X POST http://127.0.0.1:8003/incidents/INC-001/activate
curl http://127.0.0.1:8003/incidents/active
curl -X POST http://127.0.0.1:8003/incidents/active/reset
```

`INC-001` останавливает User Service, `INC-002` останавливает Redis. Сброс
запускает только контейнер, остановленный контроллером. Активный сценарий и ID
контейнера хранятся в памяти, поэтому перед завершением контроллера выполните
reset. После перезапуска контроллер не восстановит прежнее состояние.

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
