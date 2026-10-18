# AI Incident Investigator

Монорепозиторий проекта для исследования инцидентов с помощью AI. В нём
находится тестовый интернет-магазин с User Service и Order Service. Отдельный
Incident Controller создаёт управляемые неисправности, а агентская часть
(одиночный агент и мультиагентная система) расследует их по диагностическим
данным. См. [docs/agent.md](docs/agent.md).

## Требования

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- `make`
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
make setup
make up
```

`make setup` создаёт `.env` из `.env.example`, если файла нет, и заполняет
пустые диагностические и управляющие токены, а также
`DIAGNOSTIC_POSTGRES_PASSWORD` криптографически случайными значениями.
Повторный запуск сохраняет существующие значения, включая пароль PostgreSQL.
Команда не запускает Docker и не устанавливает зависимости.

Docker Compose собирает образы, дожидается PostgreSQL, применяет миграции обеих
схем, идемпотентно добавляет начальный каталог и запускает API с интерфейсом.
Incident Controller и внутренний Docker Executor также запускаются через Compose.
Магазин доступен на `http://localhost:3000`. Серверные API также опубликованы только
на loopback-интерфейсе: User Service на `http://localhost:8001`, Order Service
на `http://localhost:8002`.

Управление стеком:

```bash
make ps       # состояние контейнеров
make logs     # общие логи, выход — Ctrl+C
make build    # пересборка образов
make down     # остановка без удаления postgres_data
```

`make down` не удаляет именованный том `postgres_data`, поэтому пользователи,
заказы и товары сохраняются между запусками. Не используйте
`docker compose down -v`, если эти данные нужны.

## Запуск и проверка инцидентов

После установки и запуска приложения по инструкции выше можно включать тестовые
сценарии. Incident Controller доступен на `127.0.0.1:8003`. Его `GET /health`
не требует токена; остальные маршруты требуют `INCIDENT_CONTROLLER_TOKEN`.
Makefile читает токен из `.env` и не выводит его значение.

```bash
make incident-list
make incident-activate ID=INC-001
make incident-status
make incident-logs     # просмотр логов; остановка просмотра — Ctrl+C
make incident-reset
make incident-test     # временно нарушает работу локального приложения
```

Для каждого ID повторяйте цикл активации, проверки и сброса. `make incident-test`
последовательно проверяет все шесть сценариев, восстанавливает состояние после
каждого и проверяет повторный сброс. Тест создаёт одного пользователя; запись
остаётся в PostgreSQL. Обычный `make test` эти проверки не запускает.

| ID | Неисправность | Ожидаемый симптом |
|---|---|---|
| INC-001 | Остановлен User Service | Проверка сервиса недоступна; создание заказа не проходит |
| INC-002 | Остановлен Redis | Проверка кеша не проходит; чтение товара использует PostgreSQL |
| INC-003 | Остановлен PostgreSQL | Проверки БД не проходят в обоих сервисах |
| INC-004 | Неверный порт User Service | Создание заказа возвращает `502` |
| INC-005 | Медленный User Service | Создание заказа возвращает `504` |
| INC-006 | Неверный порт Redis | Контейнер Redis работает; чтение товара использует PostgreSQL |

Docker Executor принимает управляющие операции только для `user-service`,
`redis` и `postgres`. Отдельные маршруты только для чтения возвращают состояние
и логи также для `order-service` и `frontend`. Управляющий интерфейс доступен
Incident Controller во внутренней сети Compose; локальный прокси публикует
только маршруты чтения на `127.0.0.1:8005`. Контроллер не монтирует Docker
socket, а Executor монтирует, поэтому его управляющий доступ имеет высокие
права на Docker-хосте. Используйте стенд в доверенной локальной среде и не
открывайте управляющие маршруты внешней сети. Состояние контроллера хранится
в памяти: перед его пересозданием сбросьте активный сценарий.

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

Контейнеры приложения находятся в создаваемой сети Compose и обращаются друг к другу
по DNS-именам сервисов: `postgres:5432`, `redis:6379` и
`user-service:8001`. Nginx раздаёт статические файлы и проксирует
`/api/users/*` в User Service, а `/api/products/*` и `/api/orders/*` — в
Order Service. Docker-образ интерфейса подменяет только `config.js`, задавая
относительный базовый адрес `/api`; локальный `make run-frontend` продолжает
использовать адреса API на `localhost`.

## PostgreSQL, Redis и конфигурация

Файл `.env` создаётся командой `make setup` из раздела запуска. Его не следует
добавлять в Git или заполнять настоящими секретами рабочей среды.

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
`REDIS_TIMEOUT_SECONDS`, а время жизни кеша карточки товара — через
`PRODUCT_CACHE_TTL_SECONDS` (по умолчанию 300 секунд).

Запустите только контейнер PostgreSQL (том с данными при этом
сохраняется):

```bash
make db-up
```

Остановить PostgreSQL без удаления тома и данных:

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
make db-revision MESSAGE="описание изменения"
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
локальный `.env`, если он существует. Проверка доступности процесса User Service доступна на
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

## Веб-интерфейс

Веб-интерфейс написан на HTML, CSS и обычном JavaScript. После запуска обоих API
откройте его через локальный HTTP-сервер:

```bash
make run-frontend
```

Страница доступна на `http://localhost:3000`. Она позволяет создавать и искать
пользователей, просматривать товары, создавать заказы и загружать историю
заказов пользователя. Адреса API задаются в `frontend/config.js`; их можно
изменить без правки основной логики интерфейса.

Допустимые источники запросов обоих FastAPI-сервисов задаются разделённым запятыми списком
в `CORS_ALLOWED_ORIGINS`. Значение из `.env.example` разрешает локальный
интерфейс на `localhost:3000` и `127.0.0.1:3000`.

## Сервис заказов (Order Service)

Order Service хранит товары и заказы в отдельной PostgreSQL-схеме
`order_service` и не обращается к таблицам User Service. Перед созданием заказа
он вызывает `GET /users/{id}` по адресу `USER_SERVICE_URL`.

При отдельном запуске сервиса примените миграцию и добавьте тестовые товары
командами из раздела «Запуск сервисов». Повторное заполнение не дублирует
существующие товары.

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
make order-db-revision MESSAGE="описание изменения"
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

`GET /products/{id}` использует схему чтения с кешированием: после обязательного чтения товара
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
схемы. Redis-тест создаёт случайный ключ в отдельной логической базе `15` и
удаляет только этот ключ. Схемы приложения, пользовательские строки и тома Docker
тестами pytest не изменяются.

### Сквозной тест полного магазина

Для сборки, запуска и сквозной проверки всего приложения выполните:

```bash
make smoke-test
```

Команда оставляет приложение запущенным и проверяет:

- интерфейс и маршруты проверки состояния сервисов и баз данных;
- создание и повторное чтение пользователя;
- каталог, карточку товара и создание заказа;
- уменьшение остатка и защиту от продажи сверх запаса при конкурентных заказах;
- сохранение пользователя и заказа после перезапуска контейнеров приложений;
- промах и попадание кеша Redis, а также время жизни записи;
- чтение товара через PostgreSQL при остановленном Redis;
- ответ `502` и неизменный остаток при остановленном User Service;
- JSON-логи и общий идентификатор запроса в Order Service и User Service.

Сквозной тест создаёт уникальные записи с UUID и поэтому не зависит от заранее
существующих ID. Он не очищает таблицы, не удаляет существующие данные и не
вызывает `docker compose down -v`. Временно остановленные Redis и User Service
запускаются снова даже при ошибке теста. Созданные тестовые пользователь, товар и
заказы намеренно остаются в PostgreSQL для проверки сохранности данных.

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
попадают в лог; для медленного запроса записывается только безопасный хеш-идентификатор.
Порог задаётся переменной `SLOW_QUERY_THRESHOLD_MS` и по умолчанию равен 500
мс. Пароли, DSN, токены и тела HTTP-запросов не логируются.

Текущие счётчики процесса и состояние пула SQLAlchemy доступны внутри
каждого сервиса:

```bash
curl http://localhost:8001/internal/stats
curl http://localhost:8002/internal/stats
```

Счётчики `requests_total`, `errors_total`, `cache_hits` и `cache_misses`
сбрасываются при перезапуске процесса. Статистика Order Service отражает
использование кеша Redis; User Service возвращает нули для счётчиков кеша.

## Агентская часть

**Что добавлено:**
- `services/investigator-agent` — агенты-расследователи: одиночный агент и
  мультиагентная система (Координатор → специалисты → Критик). Они получают
  только текст симптома и ищут причину через read-only инструменты
  `diagnostic-tools`.
- `services/agent-console` — **Agent Console** (`make agent-console`,
  `http://127.0.0.1:3010`). В ней удобно включать и сбрасывать инциденты
  кнопками, запускать расследования на живом стенде и смотреть по ролям в
  реальном времени, как агенты их ведут. Сохранённые прогоны можно проиграть
  повторно. Модель — любая с OpenAI-совместимым API (OpenRouter, OpenAI,
  Ollama); задаётся в `.env` через `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`.

**Как проверялись ответы модели.** Агент сдаёт структурированный отчёт
(категория причины, компонент, зависимость, доказательства); его формат
проверяет Pydantic. Код оценки сравнивает отчёт с правильным ответом кейса
(6 инцидентов и «сбоя нет») и проверяет, что каждое доказательство ссылается
на реально вызванный инструмент. Уровни тестов:
- **unit** (`make test`) — механика агентов с заглушкой вместо LLM, без Docker;
- **replay** (`make agent-eval-replay`) — настоящая LLM на имитаторе стенда,
  построенном по измеренным ответам живой системы;
- **live** (`make agent-eval-live`) — настоящие сбои на стенде через Incident
  Controller;
- **fidelity** (`make agent-replay-fidelity`) — сверка имитатора со стендом.

**Модели и результаты.** Через OpenRouter прогнаны
`deepseek/deepseek-v4.1-flash` и `qwen/qwen3.5-9b` (без рассуждений).
Одиночный агент ни разу не ошибся; MAS дала ту же точность в 2–5 раз дороже,
а на qwen в live ошиблась в 2 кейсах из 7. Результаты оценок
(`eval-results/`, со сводкой `REPORT.md`) и сохранённые расследования
(`investigations/`) не хранятся в git и передаются архивами
`eval-results.zip` и `investigations.zip`. Распакуйте их в корень
репозитория, чтобы смотреть прогоны в консоли и пересобирать отчёт
командой `make agent-eval-report`.

Подробности: [docs/agent.md](docs/agent.md).

## Структура

- `services/user-service` — FastAPI-сервис пользователей;
- `services/order-service` — FastAPI-сервис товаров и заказов;
- `services/diagnostic-tools` — read-only диагностические инструменты;
- `services/investigator-agent` — агенты-расследователи и их оценка;
- `services/agent-console` — веб-консоль для наблюдения за агентами;
- `services/incident-controller` — управление тестовыми инцидентами;
- `frontend` — статический клиент и его Nginx-образ;
- `infrastructure/nginx` — обратный прокси и Docker-вариант конфигурации интерфейса;
- `docs` — архитектура и проектная документация.

Каждый сервис является отдельным Python-проектом и явно объявляет зависимости
для выполнения. Корневой `pyproject.toml` объединяет их в единое рабочее
пространство uv, хранит общие зависимости для разработки и конфигурацию
инструментов. Во всём репозитории используются одно корневое виртуальное
окружение и один `uv.lock`.

## Внутреннее воспроизведение INC-004–INC-006

Защищённые внутренние API хранят настройки в памяти процессов и используют
`FAULT_INJECTION_TOKEN`. INC-004 меняет адрес User Service в Order Service,
INC-005 задерживает поиск пользователя в User Service, а INC-006 меняет адрес
Redis в Order Service. Контроллер обращается к обоим сервисам по DNS-именам
Compose. Эти сценарии не имеют срока действия и остаются активными до
`make incident-reset`. Перезапуск затронутого сервиса также удаляет его
настройку из памяти. При прямом вызове внутреннего API срок действия по
умолчанию составляет 300 секунд; `ttl_seconds: null` отключает его. Чтобы
INC-005 приводил к таймауту, задайте `USER_SERVICE_TIMEOUT_SECONDS` меньше
пяти секунд.
