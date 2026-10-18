# Руководство по диагностике инцидентов

## Условия проверки

Результаты ниже получены на запущенном Docker Compose 18 октября 2026 года. Перед опытом выполнены `make up` и `make incident-status`; последний вернул `{"incident":null}`. Каждый инцидент активирован командой `make incident-activate ID=INC-XXX`, подтверждён через `make incident-status` и сброшен командой `make incident-reset` в блоке `finally`. Перед следующим опытом проверено отсутствие активного инцидента и восстановление затронутого сервиса. Incident Controller использовался только для управления опытом, не как источник диагноза.

Диагностические вызовы выполнены локальным установленным пакетом `diagnostic_tools` с переменными окружения из существующего `.env`. Значения секретов не выводились. Все приведённые JSON — фактические ответы инструментов. `latency_ms`, счётчики и `started_at` относятся к одному запуску и при повторении будут отличаться. `status=ok` означает успешную проверку без обнаруженной проблемы, `degraded` — выполненную проверку с обнаруженной проблемой, `error` — невозможность выполнить проверку. Для обычного HTTP-ответа `404` инструмент также возвращает `degraded`, хотя транспортная доступность может быть нормальной.

Исходные прямые проверки: `check_http_endpoint("user-service", "/health")` и `check_http_endpoint("order-service", "/health")` вернули `200`; `check_postgres()` подтвердил `SELECT 1`; `check_redis()` подтвердил `PING`. Контейнеры `user-service`, `order-service`, `postgres` и `redis` были `running` и `healthy`.

| Инцидент | Ключевой диагностический признак | Причина, подтверждённая наблюдением |
| --- | --- | --- |
| INC-001 — User Service Down (остановлен сервис пользователей) | Контейнер `exited`, прямой HTTP `connection_refused` | Остановлен контейнер User Service |
| INC-002 — Redis Down (остановлен Redis) | Контейнер `exited`, прямой `PING` и зависимость Order Service недоступны | Остановлен контейнер Redis |
| INC-003 — PostgreSQL Down (остановлен PostgreSQL) | Контейнер `exited`, `SELECT 1` недоступен, оба `/health/db` дают `503` | Остановлен контейнер PostgreSQL |
| INC-004 — Wrong User Service Port (неверный порт User Service) | Прямой `/health` доступен, зависимость Order Service недоступна, фактический порт `9999` | Неверный порт в конфигурации зависимости Order Service |
| INC-005 — Slow User Service (медленный сервис пользователей) | `/users/0` отвечает примерно за 5 с, проба зависимости завершается за 2 с по таймауту | Задержка ответа User Service превышает таймаут клиента Order Service |
| INC-006 — Wrong Redis Port (неверный порт Redis) | Прямой `PING` проходит, зависимость Order Service недоступна, фактический порт `9999` | Неверный порт Redis в конфигурации Order Service |

## INC-001 — User Service Down (остановлен сервис пользователей)

Ожидаемые симптомы: обращения к User Service не проходят; контейнер остановлен, хотя Order Service продолжает работать. До активации `get_service_status("user-service")` показывал `running` и `healthy`, а `check_http_endpoint("user-service", "/health")` вернул `200` за `9.13` мс.

Во время инцидента получены следующие ответы:

```json
{"tool":"get_service_status","status":"degraded","data":{"service":"user-service","container_status":"exited","health_status":"unhealthy","started_at":"2026-10-18T08:29:45.408874508Z"},"error":null}
{"tool":"check_http_endpoint","status":"degraded","data":{"service":"user-service","path":"/health","reachable":false,"latency_ms":9.35},"error":{"type":"connection_refused","message":"Connection failed"}}
{"tool":"get_service_status","status":"ok","data":{"service":"order-service","container_status":"running","health_status":"healthy","started_at":"2026-10-18T08:29:50.985541802Z"},"error":null}
{"tool":"get_service_logs","status":"ok","data":{"service":"user-service","logs":"2026-10-18T08:30:34.725906503Z INFO:     Application shutdown complete.\n2026-10-18T08:30:34.725910503Z INFO:     Finished server process [1]\n","truncated":false,"container_status":"exited"},"error":null}
```

Диагностическое основание: остановленный контейнер, отказ соединения и записи о завершении процесса согласуются между собой. После сброса контейнер снова `running`/`healthy`, а `/health` вернул `200` за `8.62` мс. Порядок расследования: проверить `get_service_status`, затем прямой `/health` и последние строки `get_service_logs`; статус Order Service помогает определить границу отказа. Наиболее полезны статус контейнера и прямая HTTP-проверка.

## INC-002 — Redis Down (остановлен Redis)

Ожидаемые симптомы: Redis недоступен как напрямую, так и для Order Service; сам Order Service может остаться работающим. До активации Redis был `running`/`healthy`, `check_redis()` вернул `ping=true` за `2.19` мс, а `check_service_dependency("order-service", "redis")` — `reachable=true`.

```json
{"tool":"get_service_status","status":"degraded","data":{"service":"redis","container_status":"exited","health_status":"unhealthy","started_at":"2026-10-18T08:29:38.780431338Z"},"error":null}
{"tool":"check_redis","status":"degraded","data":{"reachable":false,"ping":false,"latency_ms":1.09},"error":{"type":"connection_refused","message":"Connection failed"}}
{"tool":"check_service_dependency","status":"degraded","data":{"source_service":"order-service","dependency":"redis","reachable":false,"latency_ms":3.97},"error":{"type":"connection_refused","message":"Dependency probe failed"}}
{"tool":"get_service_status","status":"ok","data":{"service":"order-service","container_status":"running","health_status":"healthy","started_at":"2026-10-18T08:29:50.985541802Z"},"error":null}
{"tool":"get_service_logs","status":"ok","data":{"service":"redis","logs":"2026-10-18T08:30:50.129054510Z 1:M 18 Oct 2026 08:30:50.128 * DB saved on disk\n2026-10-18T08:30:50.129059927Z 1:M 18 Oct 2026 08:30:50.129 # Redis is now ready to exit, bye bye...\n","truncated":false,"container_status":"exited"},"error":null}
```

Диагностическое основание: отказ прямого `PING` вместе с `exited` отличает остановку Redis от ошибки конфигурации одного клиента. После сброса Redis стал `running`/`healthy`, `PING` — `true` за `1.57` мс, зависимость Order Service — `reachable=true`. Порядок расследования: `check_redis`, статус контейнера, проверка зависимости, затем логи. Наиболее полезны прямой `PING` и статус контейнера.

## INC-003 — PostgreSQL Down (остановлен PostgreSQL)

Ожидаемые симптомы: оба приложения теряют доступ к базе, хотя их HTTP-процессы отвечают. До активации `check_postgres()` подтвердил `SELECT 1` за `9.04` мс, оба `/health/db` вернули `200`.

```json
{"tool":"get_service_status","status":"degraded","data":{"service":"postgres","container_status":"exited","health_status":"unhealthy","started_at":"2026-10-18T08:29:38.697106879Z"},"error":null}
{"tool":"check_postgres","status":"degraded","data":{"reachable":false,"query_succeeded":false,"latency_ms":1.46},"error":{"type":"connection_refused","message":"Connection refused"}}
{"tool":"check_http_endpoint","status":"degraded","data":{"service":"user-service","path":"/health/db","reachable":true,"status_code":503,"latency_ms":20.73},"error":null}
{"tool":"check_http_endpoint","status":"degraded","data":{"service":"order-service","path":"/health/db","reachable":true,"status_code":503,"latency_ms":13.59},"error":null}
{"tool":"get_service_logs","status":"ok","data":{"service":"postgres","logs":"2026-10-18T08:31:05.132823378Z 2026-10-18 08:31:05.132 UTC [27] LOG:  checkpoint complete: wrote 6 buffers (0.0%); 0 WAL file(s) added, 0 removed, 0 recycled; write=0.001 s, sync=0.002 s, total=0.004 s; sync files=5, longest=0.001 s, average=0.001 s; distance=10 kB, estimate=10 kB; lsn=0/21B6F78, redo lsn=0/21B6F78\n2026-10-18T08:31:05.135629253Z 2026-10-18 08:31:05.135 UTC [1] LOG:  database system is shut down\n","truncated":false,"container_status":"exited"},"error":null}
```

Диагностическое основание: независимый `SELECT 1` не выполнен, контейнер остановлен, а обе прикладные проверки БД вернули `503`. После сброса PostgreSQL стал `running`/`healthy`, `SELECT 1` прошёл за `6.57` мс, оба `/health/db` вернули `200`. Порядок расследования: прямой `check_postgres`, статус и логи PostgreSQL, затем `/health/db` обоих сервисов. Прямая проверка БД и совпадение двух `503` — наиболее сильное подтверждение общего отказа.

## INC-004 — Wrong User Service Port (неверный порт User Service)

Ожидаемые симптомы: User Service доступен напрямую, но Order Service не может к нему подключиться. До активации оба контейнера были `running`/`healthy`, прямой `/health` вернул `200`, проверка зависимости — `reachable=true`, эффективный порт — `8001`.

```json
{"tool":"get_service_status","status":"ok","data":{"service":"user-service","container_status":"running","health_status":"healthy","started_at":"2026-10-18T08:30:37.122180879Z"},"error":null}
{"tool":"get_service_status","status":"ok","data":{"service":"order-service","container_status":"running","health_status":"healthy","started_at":"2026-10-18T08:29:50.985541802Z"},"error":null}
{"tool":"check_http_endpoint","status":"ok","data":{"service":"user-service","path":"/health","reachable":true,"status_code":200,"latency_ms":11.09},"error":null}
{"tool":"check_service_dependency","status":"degraded","data":{"source_service":"order-service","dependency":"user-service","reachable":false,"latency_ms":5.42},"error":{"type":"connection_refused","message":"Dependency probe failed"}}
{"tool":"get_dependency_config","status":"ok","data":{"service":"order-service","dependency":"user-service","host":"user-service","port":9999,"timeout_seconds":2.0},"error":null}
{"tool":"get_service_metrics","status":"ok","data":{"service":"order-service","metrics":{"requests_total":41,"errors_total":1,"cache_hits":0,"cache_misses":0,"dependency_timeouts_total":0,"dependency_errors_total":0,"redis_connection_errors_total":0,"database_pool":{"size":5,"checked_out":0,"checked_in":1,"overflow":0}}},"error":null}
```

Диагностическое основание: отказ только на пути от Order Service при исправном прямом `/health` и фактическом порте `9999` вместо исходных `8001`. `get_service_logs("order-service", tail=2, since_seconds=180)` вернул две записи о `GET /internal/stats` с `200`; они не объясняли отказ. Счётчик `dependency_errors_total` не вырос, поскольку во время опыта выполнялась диагностическая проверка, а не бизнес-операция. После сброса эффективный порт вновь `8001`, проверка зависимости — `reachable=true`, прямой `/health` — `200`. Порядок расследования: сравнить прямой `/health` с `check_service_dependency`, затем прочитать `get_dependency_config`; логи и метрики служат дополнительным контекстом. Наиболее полезны проверка зависимости и эффективная конфигурация.

## INC-005 — Slow User Service (медленный сервис пользователей)

Ожидаемые симптомы: контейнеры остаются здоровыми, но ответ User Service превышает таймаут клиента Order Service. Для безопасной проверки использован разрешённый read-only `GET /users/0` с `timeout_seconds=7`. Код `404` ожидаем и был получен ещё до активации; здесь важна задержка. Исходная задержка составила `10.04` мс, а зависимость Order Service отвечала за `6.09` мс со статусом `404` и `reachable=true`.

```json
{"tool":"get_service_status","status":"ok","data":{"service":"user-service","container_status":"running","health_status":"healthy","started_at":"2026-10-18T08:30:37.122180879Z"},"error":null}
{"tool":"get_service_status","status":"ok","data":{"service":"order-service","container_status":"running","health_status":"healthy","started_at":"2026-10-18T08:29:50.985541802Z"},"error":null}
{"tool":"check_http_endpoint","status":"degraded","data":{"service":"user-service","path":"/users/0","reachable":true,"status_code":404,"latency_ms":5022.02},"error":null}
{"tool":"check_service_dependency","status":"degraded","data":{"source_service":"order-service","dependency":"user-service","reachable":false,"latency_ms":2009.02},"error":{"type":"timeout","message":"Dependency probe failed"}}
{"tool":"get_service_metrics","status":"ok","data":{"service":"user-service","metrics":{"requests_total":30,"errors_total":1,"cache_hits":0,"cache_misses":0,"database_pool":{"size":5,"checked_out":0,"checked_in":1,"overflow":0}}},"error":null}
{"tool":"get_service_metrics","status":"ok","data":{"service":"order-service","metrics":{"requests_total":54,"errors_total":1,"cache_hits":0,"cache_misses":0,"dependency_timeouts_total":0,"dependency_errors_total":0,"redis_connection_errors_total":0,"database_pool":{"size":5,"checked_out":0,"checked_in":1,"overflow":0}}},"error":null}
```

Диагностическое основание: замедление прямого запроса до примерно пяти секунд и таймаут проверки зависимости примерно через две секунды при здоровых контейнерах. `get_service_logs("order-service", tail=2, since_seconds=180)` показал лишь успешный `GET /internal/stats`; сам по себе этот фрагмент не подтверждает таймаут. `dependency_timeouts_total` остался `0`: диагностическая проверка не инкрементирует бизнес-счётчик. После сброса тот же `/users/0` вернул `404` за `8.19` мс, зависимость — `reachable=true` за `5.76` мс. Порядок расследования: проверить статус контейнеров, измерить задержку прямого `/users/0`, сравнить её с таймаутом зависимости, затем изучить логи и метрики как дополнительные данные. Наиболее полезны две временные проверки; один лишь HTTP-код `404` здесь не диагностирует инцидент.

## INC-006 — Wrong Redis Port (неверный порт Redis)

Ожидаемые симптомы: Redis доступен напрямую, но Order Service не может подключиться по своей конфигурации. До активации Redis был `running`/`healthy`, прямой `PING` успешен за `1.6` мс, проверка зависимости — `reachable=true`, эффективный порт — `6379`.

```json
{"tool":"get_service_status","status":"ok","data":{"service":"redis","container_status":"running","health_status":"healthy","started_at":"2026-10-18T08:30:52.459592969Z"},"error":null}
{"tool":"check_redis","status":"ok","data":{"reachable":true,"ping":true,"latency_ms":2.08},"error":null}
{"tool":"check_service_dependency","status":"degraded","data":{"source_service":"order-service","dependency":"redis","reachable":false,"latency_ms":0.58},"error":{"type":"connection_refused","message":"Dependency probe failed"}}
{"tool":"get_dependency_config","status":"ok","data":{"service":"order-service","dependency":"redis","host":"redis","port":9999,"timeout_seconds":1.0},"error":null}
{"tool":"get_service_metrics","status":"ok","data":{"service":"order-service","metrics":{"requests_total":66,"errors_total":1,"cache_hits":0,"cache_misses":0,"dependency_timeouts_total":0,"dependency_errors_total":0,"redis_connection_errors_total":0,"database_pool":{"size":5,"checked_out":0,"checked_in":1,"overflow":0}}},"error":null}
```

Диагностическое основание: прямой `PING` проходит, а проба именно из Order Service получает отказ соединения; эффективный порт `9999` расходится с исходными `6379`. `get_service_logs("order-service", tail=2, since_seconds=180)` вернул записи об успешном `GET /internal/stats`, не свидетельствующие о причине. `redis_connection_errors_total` остался `0`, потому что бизнес-чтение через Redis в этом опыте не выполнялось. После сброса порт снова `6379`, проверка зависимости — `reachable=true`, прямой `PING` — `true` за `1.61` мс. Порядок расследования: прямой `check_redis`, затем `check_service_dependency`, после этого `get_dependency_config`; статус, логи и метрики уточняют контекст. Наиболее полезно расхождение прямой и внутрисервисной проверок вместе с эффективным портом.

## Выбор инструмента и ограничения

| Инструмент | Что подтверждает в этих опытах | Что не следует из результата |
| --- | --- | --- |
| `get_service_status` | Состояние контейнера и Docker health | Исправность всех зависимостей работающего процесса |
| `get_service_logs` | Фактические последние записи с временными метками | Полную историю и однозначную причину по двум строкам |
| `check_http_endpoint` | Доступность и время ответа разрешённого HTTP-пути | Внутренний путь вызова из другого сервиса |
| `check_postgres` | Прямое соединение и выполнение `SELECT 1` | Исправность прикладных SQL-запросов |
| `check_redis` | Прямой `PING` по диагностическому адресу | Доступность Redis из Order Service при его фактической конфигурации |
| `check_service_dependency` | Пробу зависимости из Order Service | Успешность бизнес-операции или рост бизнес-счётчиков |
| `get_dependency_config` | Фактические хост, порт и таймаут зависимости | Причину изменения конфигурации |
| `get_service_metrics` | Текущие счётчики и состояние пула | Изменение счётчиков от одних лишь диагностических проб |

`/users/0` возвращает `404` и в нормальном состоянии; для INC-005 сравнивать нужно `latency_ms` и `reachable`. Проверка зависимости User Service также возвращает `status_code=404` при `reachable=true`, что в данном случае означает успешное соединение с сервисом, а не наличие пользователя. Логи Order Service с `tail=2` в INC-004–INC-006 отражали только последние диагностические GET-запросы и не дали отдельного свидетельства о причине. Для доказательства бизнес-эффекта потребовались бы дополнительные бизнес-запросы; в этой серии их не выполняли.

После последнего сброса `make incident-status` подтвердил отсутствие активного инцидента, а проверенные прямые и внутрисервисные зависимости вернулись к исходному состоянию. Инструменты диагностики не обращались к Incident Controller и не использовали ID инцидента в результатах.
