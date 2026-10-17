# AI Incident Investigator — архитектура MVP

## 1. Цель

Собрать небольшое **реально работающее** веб-приложение с несколькими зависимостями и воспроизводимыми неисправностями. AI-агент должен расследовать инциденты по логам, проверкам доступности, диагностике PostgreSQL/Redis и документации через RAG, **не получая заранее правильный ответ**.

Принципы MVP:
- минимум инфраструктуры и кода;
- настоящие сбои, а не подставные ответы инструментов;
- несколько разных причин с похожими симптомами;
- активация и сброс сценариев одной командой;
- отсутствие доступа агента к управлению инцидентами.

## 2. Стек и компоненты

| Компонент | Технология | Назначение |
|---|---|---|
| Frontend | HTML + CSS + JavaScript (`fetch`) | Простая страница пользователей, товаров и заказов |
| User Service | Python + FastAPI | Пользователи |
| Order Service | Python + FastAPI | Товары, заказы, Redis-кеш |
| Бизнес-БД | PostgreSQL | Таблицы `users`, `products`, `orders` |
| Кеш | Redis | Кеш карточек товаров |
| Оркестрация | Docker Compose | Запуск компонентов, локальные сети |
| Логи | JSON → stdout/stderr → Docker logs | Диагностические события |
| Диагностика | FastAPI internal endpoints + Docker API | Health, статистика, доступность зависимостей |
| Incident Controller | Небольшой FastAPI-сервис | Включение и сброс неисправностей |
| Agent Backend | Python | Tool calling, расследование, формирование отчёта |
| RAG | Embedding model + Qdrant | Поиск по документации и summaries расследований |
| История агента | SQLite (опционально для MVP) | Полные сообщения, вызовы и ответы инструментов |

**Не используем на первом этапе:** React, API Gateway, Prometheus, Loki, Grafana, Kubernetes, Kafka. Метрики времени ответа доступны из логов и простых диагностических счётчиков; полноценной истории временных рядов нет.

### Схема

```text
                       Browser (HTML/JS)
                         /           \
                        v             v
                 User Service     Order Service
                    :8001            :8002
                       |             /  |   \
                       |     HTTP --/   |    Redis
                       |   (User Service)|
                       +-------+--------+
                               v
                           PostgreSQL

      User/Order Service --> JSON stdout --> Docker logs
      User/Order Service --> /health, /internal/stats

                   Agent Backend (отдельно)
                  /          |           \
           Diagnostic     Qdrant       SQLite
              Tools     (RAG docs +   (history)
                |        summaries)
        Docker / HTTP / DB / Redis

    Incident Controller --> Docker / runtime fault config
    (только для тестов; агенту недоступен)
```

Оба бизнес-сервиса могут использовать один экземпляр PostgreSQL, но работать со своими таблицами. Это упрощение для MVP.

## 3. Данные PostgreSQL

### `users`

| Поле | Тип | Значение |
|---|---|---|
| `id` | integer, PK | Идентификатор |
| `name` | varchar | Имя |
| `email` | varchar, unique | Почта |

### `products`

| Поле | Тип | Значение |
|---|---|---|
| `id` | integer, PK | Идентификатор |
| `name` | varchar | Название |
| `price` | numeric | Цена |
| `stock` | integer | Остаток |

### `orders`

| Поле | Тип | Значение |
|---|---|---|
| `id` | integer, PK | Идентификатор |
| `user_id` | integer | Пользователь (проверяется через User Service) |
| `product_id` | integer, FK | Товар |
| `quantity` | integer | Количество |
| `status` | varchar | Статус |
| `created_at` | timestamp | Время создания |

Для сценария медленного SQL можно предварительно сгенерировать 100–500 тысяч заказов. Не следует создавать большой объём данных при каждом запуске.

## 4. Бизнес-API

### User Service (`:8001`)

| Метод | Маршрут | Действие |
|---|---|---|
| `POST` | `/users` | Создать пользователя в PostgreSQL |
| `GET` | `/users/{id}` | Найти пользователя по ID |
| `GET` | `/health` | Проверить, что процесс отвечает (без проверки БД) |
| `GET` | `/health/db` | Выполнить `SELECT 1` через соединение сервиса |

### Order Service (`:8002`)

| Метод | Маршрут | Действие |
|---|---|---|
| `GET` | `/products` | Список товаров из PostgreSQL (с лимитом) |
| `GET` | `/products/{id}` | Прочитать карточку из Redis, при промахе — из PostgreSQL |
| `POST` | `/orders` | Проверить пользователя, товар, остаток и создать заказ |
| `GET` | `/orders/{id}` | Получить заказ |
| `GET` | `/orders?user_id=...` | Список заказов пользователя, сортировка по времени, лимит |
| `GET` | `/health` | Liveness без зависимостей |
| `GET` | `/health/db` | Проверить БД из контекста Order Service |
| `GET` | `/health/redis` | Проверить Redis из контекста Order Service |
| `GET` | `/internal/stats` | Текущие счётчики и состояние пула соединений |

**`POST /orders` — последовательность:**

1. Order Service вызывает `GET /users/{id}` в User Service.
2. Проверяет наличие товара в PostgreSQL.
3. В транзакции атомарно проверяет/уменьшает `stock` и создаёт заказ (без гонки при параллельных запросах).
4. Инвалидирует кеш карточки товара, если кешируются изменяемые поля.
5. Возвращает созданный заказ.

Для простоты в Redis лучше кешировать только относительно стабильные поля (`name`, `price`), а `stock` всегда читать из PostgreSQL. Ошибка Redis не должна останавливать чтение товаров или создание заказов.

## 5. Redis

Ключи вида `product:{id}`, TTL 300 секунд. Алгоритм `GET /products/{id}`:

```text
Redis GET --> hit: вернуть кешируемые поля
          --> miss: PostgreSQL SELECT --> Redis SETEX --> ответ
          --> Redis error: логировать --> PostgreSQL fallback
```

Redis используется только как кеш, **не как источник истины**.

## 6. Логи и минимальная диагностика

Каждый FastAPI-сервис пишет **структурированные JSON-логи в stdout/stderr**, Docker сохраняет их. Отдельного Loki нет.

Пример:

```json
{
  "timestamp": "2026-10-14T12:00:00Z",
  "service": "order-service",
  "level": "ERROR",
  "event": "dependency_connection_failed",
  "request_id": "abc123",
  "message": "Cannot connect to User Service",
  "target": "user-service:9999"
}
```

Логировать события:

| `event` | Содержание |
|---|---|
| `http_request` / `http_response` | Метод, путь, статус, `duration_ms`, `request_id` |
| `dependency_connection_failed` | Ошибка вызова другого сервиса |
| `db_connection_failed` | Ошибка соединения/аутентификации PostgreSQL |
| `db_query_slow` | SQL-превышение порога времени, безопасный идентификатор запроса |
| `db_pool_timeout` | Ожидание свободного соединения истекло |
| `redis_connection_failed` | Redis недоступен |
| `service_started` | Запуск сервиса |

Передавать `request_id` из Order Service в User Service. Не логировать пароли, токены, полные DSN с секретами и персональные данные без необходимости.

**Минимальные метрики без Prometheus:** `GET /internal/stats` возвращает счётчики с момента запуска процесса (`requests_total`, `errors_total`, `cache_hits`, `cache_misses`) и моментальный снимок SQLAlchemy pool (`size`, `checked_out`, `checked_in`, `overflow`). HTTP latency записывается в логи; агрегирование p95 по истории можно отложить. После перезапуска процесса счётчики обнуляются.

## 7. Диагностические инструменты агента

Инструменты — **функции Agent Backend**, а не обязательно публичные endpoint магазина. Они вызывают Docker API, внутренние HTTP endpoint или ограниченные read-only диагностические операции.

| Tool | Аргументы | Что проверяет / откуда берёт данные |
|---|---|---|
| `get_service_logs` | `service`, `last_minutes=10`, `level?` | Читает Docker logs сервиса, фильтрует по времени/уровню, ограничивает объём ответа |
| `check_service_health` | `service` | HTTP `GET /health`; возвращает статус, ошибку соединения и latency |
| `check_dependency` | `source`, `target` | Проверяет соединение **из окружения source** с target; показывает используемый адрес и ошибку |
| `inspect_database` | `operation` | `ping`, `connections`, `explain_orders_query`, `slow_queries` |
| `inspect_redis` | `operation` | `ping`, `stats`, `memory` через Redis API |
| `get_service_diagnostics` | `service` | Читает `/internal/stats`: счётчики, пул соединений |
| `get_service_config` | `service` | Безопасная конфигурация: адреса зависимостей, порты, таймауты, размер пула; **без секретов** |

**Детали реализации:**

- `check_dependency` нельзя подменять проверкой с хоста агента: она должна использовать конфигурацию и сетевое окружение исходного сервиса. Удобно сделать защищённый диагностический endpoint внутри сервиса, который выполняет разрешённую проверку.
- `inspect_database("connections")` использует `pg_stat_activity`/настройки PostgreSQL; `explain_orders_query` выполняет **только заранее разрешённый** `EXPLAIN`.
- `inspect_database("slow_queries")` на первом этапе берёт события `db_query_slow` из логов; это не встроенная история PostgreSQL.
- `get_service_config` маскирует `DB_PASSWORD`, `DATABASE_URL` с паролем и другие секреты.
- Не давать агенту произвольное выполнение shell-команд, произвольного SQL, Docker control или доступ к Incident Controller.

### Пример ответа инструмента

```json
{
  "service": "order-service",
  "status": "healthy",
  "http_status": 200,
  "response_time_ms": 12
}
```

## 8. Инциденты

| ID | Сценарий | Внешние симптомы | Проверки агента | Ожидаемая причина |
|---|---|---|---|---|
| `wrong_user_service_url` | Неверный порт/адрес User Service в Order Service | `POST /orders` → 502; товары работают | Логи, health обоих сервисов, `check_dependency`, конфиг | Ошибка URL зависимости |
| `user_service_down` | Остановлен контейнер User Service | `POST /orders` → 502 | Health, логи, проверка зависимости | User Service недоступен |
| `wrong_db_password` | Неверные DB credentials **у Order Service** | Ошибки DB-операций; User Service может работать | Логи Order Service, `inspect_database`, `/health/db` | Ошибка аутентификации, не падение PostgreSQL |
| `redis_down` | Остановлен Redis | Товары продолжают открываться через БД; ошибки кеша | Логи, `inspect_redis`, stats | Недоступный кеш, graceful degradation |
| `slow_sql` | Медленный запрос списка заказов, например из-за отсутствия подходящего индекса | `GET /orders?user_id=...` медленный | HTTP duration в логах, `db_query_slow`, `EXPLAIN` | Неэффективный план запроса |
| `db_pool_exhaustion` | Соединения удерживаются, пул Order Service исчерпан | Под нагрузкой 500/таймауты при работающей БД | Логи `db_pool_timeout`, `/internal/stats`, `inspect_database` | Исчерпание пула соединений |
| `INC-004` | Неверный порт User Service через runtime fault Order Service | `POST /orders` → 502, User Service работает | `dependency_connection_failed`, health | Ошибка адреса зависимости |
| `INC-005` | Задержка 5000 мс для `GET /users/{id}` через runtime fault User Service | `POST /orders` → 504, health быстрый | `fault_injection_applied`, timeout Order Service | Медленный ответ User Service |
| `INC-006` | Неверный Redis endpoint через runtime fault Order Service | Карточка товара доступна через PostgreSQL | `redis_connection_failed`, health Redis | Ошибка адреса кеша |

**Примечания к воспроизводимости:**
- Неверный пароль должен применяться к новым подключениям: существующие открытые соединения нужно корректно закрыть/пересоздать в тестовом сценарии.
- Медленный SQL требует достаточного объёма данных и проверки `EXPLAIN`; отсутствие индекса не гарантирует заметной задержки на маленькой таблице.
- Для исчерпания пула использовать контролируемую конкурентную нагрузку и удержание соединений; обычное `pool_size=5` само по себе не создаёт сбой.
- Для `redis_down` ожидаем **ухудшение**, а не обязательный HTTP 500, поскольку реализован fallback.

## 9. Incident Controller

Только для тестового стенда:

| Метод | Маршрут | Действие |
|---|---|---|
| `POST` | `/incidents/activate` | Активировать сценарий по ID |
| `POST` | `/incidents/reset` | Вернуть систему к исходной конфигурации/состоянию |

Пример:

```json
{"incident": "wrong_user_service_url"}
```

Механизмы: остановка/запуск контейнера, изменение разрешённой runtime-конфигурации, включение тестового медленного SQL, генерация конкурентной нагрузки. Контроллер не подменяет логи и результаты диагностики. **Агент не видит ID активного сценария**; Docker control не предоставляется агенту.

Желательно после `reset` проверять health, восстановление конфигурации и отсутствие остаточной нагрузки. Контроллер доступен только в локальной тестовой сети.

## 10. RAG и история агента — отдельно от магазина

**Qdrant** хранит векторные представления и тексты чанков Markdown-документации, runbooks и summaries завершённых расследований. Метаданные: `source`, `service`, `type`, `incident_id` и т. п.

```text
Markdown/runbook -> chunks -> embeddings -> Qdrant
User query -> embedding -> Qdrant top-k -> retrieved text -> LLM
```

После расследования можно сформировать summary: симптомы, ключевые проверки, доказательства, причина, рекомендация. Его embedding и текст добавить в Qdrant. Похожие случаи дают **гипотезы**, а не готовый диагноз.

**SQLite (опционально)** хранит полную последовательность сообщений, вызовов инструментов и их результатов, статус расследования. Для самого RAG SQLite/PostgreSQL не требуются: Qdrant может хранить и embedding, и текст чанка.

PostgreSQL магазина и хранилище истории агента — разные логические задачи.

## 11. Пример расследования

**Симптом:** `POST /orders` возвращает 502.

1. `check_service_health("order-service")` → healthy.
2. `get_service_logs("order-service", 10)` → `Connection refused to user-service:9999`.
3. `check_service_health("user-service")` → healthy на порту 8001.
4. `check_dependency("order-service", "user-service")` → неуспешное соединение на `:9999`.
5. `get_service_config("order-service")` → `USER_SERVICE_URL=http://user-service:9999`.
6. Агент сообщает: неверный порт зависимости; доказательства и рекомендация изменить адрес на `:8001`.

Диагноз должен основываться на наблюдениях, а не на информации контроллера о включённом сценарии.

## 12. Порядок реализации

1. Docker Compose: PostgreSQL, Redis, два FastAPI-сервиса; простая HTML/JS-страница.
2. Три таблицы и базовые бизнес-endpoint; `request_id` и JSON-логи.
3. `/health`, `/health/db`, `/health/redis`, `/internal/stats`.
4. Agent Backend и семь диагностических инструментов.
5. Incident Controller + первые сценарии: `wrong_user_service_url`, `user_service_down`, `redis_down`.
6. Затем: `wrong_db_password`, `slow_sql`, `db_pool_exhaustion`.
7. Qdrant и Markdown-документация; затем summaries прошлых расследований.
8. Тестирование: корректность root cause, количество вызовов инструментов, время расследования, токены; сравнение с RAG и без RAG.

**Критерий готовности MVP:** каждый инцидент можно воспроизвести, наблюдать реальные последствия, сбросить, а агент способен получить достаточно диагностических данных, чтобы отличить его от похожих случаев.
