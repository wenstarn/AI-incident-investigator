# Результаты оценки агентов

Сгенерировано 2026-10-18 22:42 UTC командой `make agent-eval-report` из файлов `eval-results/*/scores.json`. Каждая цифра посчитана по сохранённым оценкам отдельных расследований.

**Как читать:** *верно* — категория, компонент и (для неверной конфигурации) зависимость совпали с правильным ответом кейса; *подтверждено* — каждое доказательство в отчёте ссылается на реально вызванный инструмент; токены, вызовы и время — средние на одно расследование. *replay* — имитатор стенда, *live* — живой стенд.

## 1. Сводка по всем прогонам

| Прогон (папка) | Модель | Режим | Повторов | Система | Верно | Подтверждено | Инструментов | LLM-вызовов | Токенов | Время, с |
|---|---|---|---|---|---|---|---|---|---|---|
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash | replay | 3 | Только LLM | 11/21 | 0/21 | 0.0 | 1.0 | 2 964 | 21.2 |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash | replay | 3 | Одиночный агент | 21/21 | 21/21 | 13.8 | 3.8 | 19 776 | 27.5 |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash | replay | 3 | MAS | 21/21 | 19/21 | 23.1 | 13.2 | 65 062 | 132.5 |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash | replay · generic | 3 | Только LLM | 0/21 | 0/21 | 0.0 | 1.0 | 2 460 | 15.5 |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash | replay · generic | 3 | Одиночный агент | 21/21 | 21/21 | 20.8 | 4.9 | 27 076 | 35.0 |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash | replay · generic | 3 | MAS | 21/21 | 21/21 | 30.5 | 14.3 | 76 178 | 151.2 |

## 2. MAS против одиночного агента

| Прогон | Модель · режим | Точность single | Точность MAS | Токены MAS / single | Время MAS / single |
|---|---|---|---|---|---|
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | 21/21 | 21/21 | ×3.3 | ×4.8 |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | 21/21 | 21/21 | ×2.8 | ×4.3 |

## 3. Ошибки

| Прогон | Модель · режим | Система | Кейс | Повтор | Ожидалось | Получено |
|---|---|---|---|---|---|---|
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | INC-001 | 1 | `component_down/user-service` | `dependency_misconfiguration/order-service->user-service` |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | INC-002 | 2 | `component_down/redis` | `dependency_misconfiguration/order-service->redis` |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | INC-004 | 1 | `dependency_misconfiguration/order-service->user-service` | `component_down/user-service` |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | INC-004 | 2 | `dependency_misconfiguration/order-service->user-service` | `component_down/user-service` |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | INC-006 | 1 | `dependency_misconfiguration/order-service->redis` | `component_down/redis` |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | INC-006 | 2 | `dependency_misconfiguration/order-service->redis` | `component_down/redis` |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | INC-006 | 3 | `dependency_misconfiguration/order-service->redis` | `component_down/redis->redis` |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | healthy | 1 | `no_incident/none` | `component_down/redis` |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | healthy | 2 | `no_incident/none` | `unknown/none` |
| 20261018T213028Z-replay | deepseek/deepseek-v4.1-flash · replay | Только LLM | healthy | 3 | `no_incident/none` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-001 | 1 | `component_down/user-service` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-001 | 2 | `component_down/user-service` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-001 | 3 | `component_down/user-service` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-002 | 1 | `component_down/redis` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-002 | 2 | `component_down/redis` | `dependency_misconfiguration/order-service->redis` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-002 | 3 | `component_down/redis` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-003 | 1 | `component_down/postgres` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-003 | 2 | `component_down/postgres` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-003 | 3 | `component_down/postgres` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-004 | 1 | `dependency_misconfiguration/order-service->user-service` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-004 | 2 | `dependency_misconfiguration/order-service->user-service` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-004 | 3 | `dependency_misconfiguration/order-service->user-service` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-005 | 1 | `dependency_slow/user-service` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-005 | 2 | `dependency_slow/user-service` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-005 | 3 | `dependency_slow/user-service` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-006 | 1 | `dependency_misconfiguration/order-service->redis` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-006 | 2 | `dependency_misconfiguration/order-service->redis` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | INC-006 | 3 | `dependency_misconfiguration/order-service->redis` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | healthy | 1 | `no_incident/none` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | healthy | 2 | `no_incident/none` | `unknown/none` |
| 20261018T213036Z-replay | deepseek/deepseek-v4.1-flash · replay · generic | Только LLM | healthy | 3 | `no_incident/none` | `unknown/none` |

Полный ход каждого ошибочного расследования — в `eval-results/<прогон>/investigations.jsonl` и в Agent Console («Повтор сохранённых», отмечены ✗).

## 4. Подробно по кейсам

### 20261018T213028Z-replay — deepseek/deepseek-v4.1-flash · replay, повторов: 3, симптомы: specific

| Кейс | Система | Верно | Подтверждено | Инструментов | LLM-вызовов | Токенов | Время, с |
|---|---|---|---|---|---|---|---|
| INC-001 | Только LLM | 2/3 | 0/3 | 0.0 | 1.0 | 3 431 | 26.4 |
| INC-001 | Одиночный агент | 3/3 | 3/3 | 9.3 | 3.0 | 14 749 | 22.5 |
| INC-001 | MAS | 3/3 | 3/3 | 23.0 | 13.0 | 61 111 | 104.1 |
| INC-002 | Только LLM | 2/3 | 0/3 | 0.0 | 1.0 | 3 655 | 29.6 |
| INC-002 | Одиночный агент | 3/3 | 3/3 | 9.3 | 3.0 | 14 910 | 23.9 |
| INC-002 | MAS | 3/3 | 3/3 | 21.3 | 15.0 | 74 656 | 199.6 |
| INC-003 | Только LLM | 3/3 | 0/3 | 0.0 | 1.0 | 2 428 | 18.8 |
| INC-003 | Одиночный агент | 3/3 | 3/3 | 18.7 | 4.7 | 26 677 | 31.6 |
| INC-003 | MAS | 3/3 | 2/3 | 30.7 | 12.7 | 70 729 | 114.4 |
| INC-004 | Только LLM | 1/3 | 0/3 | 0.0 | 1.0 | 3 340 | 23.5 |
| INC-004 | Одиночный агент | 3/3 | 3/3 | 11.0 | 3.0 | 15 429 | 23.1 |
| INC-004 | MAS | 3/3 | 3/3 | 17.3 | 12.0 | 51 679 | 91.5 |
| INC-005 | Только LLM | 3/3 | 0/3 | 0.0 | 1.0 | 3 562 | 26.4 |
| INC-005 | Одиночный агент | 3/3 | 3/3 | 16.7 | 4.3 | 24 244 | 36.8 |
| INC-005 | MAS | 3/3 | 2/3 | 25.3 | 14.7 | 83 811 | 191.7 |
| INC-006 | Только LLM | 0/3 | 0/3 | 0.0 | 1.0 | 2 002 | 9.2 |
| INC-006 | Одиночный агент | 3/3 | 3/3 | 7.7 | 3.0 | 13 890 | 21.3 |
| INC-006 | MAS | 3/3 | 3/3 | 15.3 | 12.0 | 49 834 | 98.7 |
| healthy | Только LLM | 0/3 | 0/3 | 0.0 | 1.0 | 2 330 | 14.9 |
| healthy | Одиночный агент | 3/3 | 3/3 | 24.0 | 5.3 | 28 535 | 33.2 |
| healthy | MAS | 3/3 | 3/3 | 28.7 | 13.0 | 63 616 | 127.8 |

### 20261018T213036Z-replay — deepseek/deepseek-v4.1-flash · replay · generic, повторов: 3, симптомы: generic

| Кейс | Система | Верно | Подтверждено | Инструментов | LLM-вызовов | Токенов | Время, с |
|---|---|---|---|---|---|---|---|
| INC-001 | Только LLM | 0/3 | 0/3 | 0.0 | 1.0 | 2 475 | 16.4 |
| INC-001 | Одиночный агент | 3/3 | 3/3 | 17.0 | 4.3 | 23 526 | 28.8 |
| INC-001 | MAS | 3/3 | 3/3 | 28.0 | 15.3 | 82 723 | 166.8 |
| INC-002 | Только LLM | 0/3 | 0/3 | 0.0 | 1.0 | 2 412 | 14.0 |
| INC-002 | Одиночный агент | 3/3 | 3/3 | 21.3 | 4.7 | 25 660 | 37.7 |
| INC-002 | MAS | 3/3 | 3/3 | 45.3 | 20.0 | 117 669 | 230.1 |
| INC-003 | Только LLM | 0/3 | 0/3 | 0.0 | 1.0 | 3 233 | 22.3 |
| INC-003 | Одиночный агент | 3/3 | 3/3 | 20.3 | 4.3 | 25 408 | 30.3 |
| INC-003 | MAS | 3/3 | 3/3 | 29.7 | 12.7 | 69 147 | 157.2 |
| INC-004 | Только LLM | 0/3 | 0/3 | 0.0 | 1.0 | 2 287 | 14.2 |
| INC-004 | Одиночный агент | 3/3 | 3/3 | 19.7 | 4.7 | 23 267 | 28.0 |
| INC-004 | MAS | 3/3 | 3/3 | 24.7 | 13.0 | 59 318 | 75.0 |
| INC-005 | Только LLM | 0/3 | 0/3 | 0.0 | 1.0 | 2 344 | 18.6 |
| INC-005 | Одиночный агент | 3/3 | 3/3 | 22.0 | 5.0 | 30 547 | 48.4 |
| INC-005 | MAS | 3/3 | 3/3 | 28.3 | 13.0 | 66 811 | 128.2 |
| INC-006 | Только LLM | 0/3 | 0/3 | 0.0 | 1.0 | 2 196 | 11.4 |
| INC-006 | Одиночный агент | 3/3 | 3/3 | 19.3 | 5.0 | 24 518 | 33.7 |
| INC-006 | MAS | 3/3 | 3/3 | 28.0 | 13.0 | 72 628 | 169.8 |
| healthy | Только LLM | 0/3 | 0/3 | 0.0 | 1.0 | 2 273 | 11.5 |
| healthy | Одиночный агент | 3/3 | 3/3 | 25.7 | 6.3 | 36 608 | 38.1 |
| healthy | MAS | 3/3 | 3/3 | 29.7 | 13.0 | 64 954 | 131.6 |
