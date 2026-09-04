# Архитектура

[English](ARCHITECTURE.md) · **Русский**

HoH — control plane «над харнессами»: Supervisor планирует и владеет
интеграцией, недоверенный Worker предлагает ограниченные изменения, а
независимый Critic проверяет неизменяемые доказательства. Одна и та же модель
не получает право одновременно планировать, редактировать и одобрять результат.

## Схема системы

```text
Пользователь
   │ задача, уточнения, подтверждение
   ▼
Desktop GUI / Headless API ── Реестр проектов ── Состояние проекта
   │                              │
   ├─ чат и планировщик Supervisor ├─ queue/history/audit/journal
   ├─ реестр ролей/драйверов        └─ scheduler + уведомления
   └─ установка/auth/A2A gateway
          │             │
          ▼             ▼
   Supervisor       попытка Worker ──> детерминированный verifier ──> Git Gate
          │                                      │                    │
          └────────────── review Critic <────────┴── immutable bundle ┘
```

## Границы ответственности

- **GUI service** владеет проверенной конфигурацией, role profile, передачей
  credential и защищёнными операционными командами; виджеты не редактируют Git.
- **Protocol/loop engine** владеет state machine, переходами очереди,
  доказательствами, retry/recovery и финальной передачей результата.
- **Driver Registry** сопоставляет явные role/driver id с ACP, process,
  direct-model или A2A-транспортом. Имя агента никогда не выбирает драйвер
  неявно.
- **Worker adapter** получает только ограниченную задачу и allowed paths. Git
  Gate отклоняет изменённый `HEAD` попытки и остаётся единственным владельцем
  commit.
- **Review Gateway** связывает решение Critic с точными run, commit, digest
  патча, scope и детерминированными проверками.
- **A2A Gateway** обслуживает Agent Card, bearer/API-key/OAuth2/OIDC,
  JSON-RPC, SSE, push, cancellation и ограниченный polling fallback.
- **State Store** и **Interaction Journal** живут вне canonical repository и
  используют разные lease для состояния и Git-операций.

## Основной жизненный цикл

1. Пользователь настраивает роли и подтверждает план Supervisor.
2. Scheduler выбирает готовые по зависимостям задачи по приоритету и FIFO.
3. HoH создаёт изолированный worktree и запускает выбранный Worker driver.
4. Детерминированные policy/verifier проверяют возвращённый патч.
5. Включённый Critic читает immutable evidence.
6. Supervisor применяет, индексирует и коммитит только разрешённый патч.
7. Queue/history, metrics, journal, уведомления и audit evidence атомарно
   сохраняются вне рабочей директории проекта.

## Варианты развёртывания

- Desktop: Tkinter/ttk GUI и тот же structured application service.
- Headless: авторизованный HTTP control API; non-loopback требует переданных
  оператором TLS certificate и key.
- Локальные агенты: ACP stdio или явные process/direct-model drivers.
- Удалённые агенты: A2A v1 по HTTPS; loopback HTTP разрешён только локально.
- Фоновая работа: Windows Task Scheduler, Linux systemd user или macOS launchd;
  все они вызывают один и тот же защищённый queue loop.

## Источник полной спецификации

Полные component/sequence-диаграммы, protocol contracts, storage rules,
rollback policy, metrics и module map находятся в русской
спецификации [`docs/architecture.ru.md`](docs/architecture.ru.md). Английская
версия — [`docs/architecture.md`](docs/architecture.md).
