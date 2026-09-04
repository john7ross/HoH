# Headless-сервер HoH

[English](headless-server.md) · **Русский**

Headless-сервер выставляет те же сервисы реестра рабочих пространств, разговора с Supervisor,
планирования, цикла очереди и метрик, которыми пользуется десктопный интерфейс. Он не создаёт второго
пути оркестрации и не обходит аренды проекта, состояния и Git.

## Запуск на loopback

Создайте случайный токен вне файлов проекта и запустите службу:

```bash
export HOH_SERVER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export HOH_A2A_PUSH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
bash scripts/hoh.sh server --host 127.0.0.1 --port 8765
```

На Windows используйте `scripts\hoh.ps1` и задайте ту же переменную окружения до запуска. Сервер
отклоняет токены короче 24 символов и никогда не печатает их значение. `/v1/health` и
`/v1/openapi.json` публичны; каждый эндпоинт проекта или операции требует
`Authorization: Bearer <токен>`.

`HOH_A2A_PUSH_TOKEN` необязателен, пока удалённый провайдер A2A не использует доставку push. Он
отделён от операторского токена API и тоже обязан содержать не менее 24 символов. Задайте
`push_token_env` соединения A2A равным `HOH_A2A_PUSH_TOKEN`, а его URL обратного вызова — маршруту
`/v1/a2a/push` этого же сервера. Провайдер отправляет этот токен как Bearer-авторизацию; значение
токена в проекте не хранится.

Привязка к адресу не на loopback приводит к отказу закрытым, пока не переданы и `--tls-cert`, и
`--tls-key`. Принудительно требуется TLS 1.2 или новее. HoH не порождает и не сопровождает публичный
сертификат сервера; используйте сертификат, подходящий для сети, в которой выставлена служба.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/v1/health` | Версионированная проверка живости |
| GET | `/v1/openapi.json` | Обнаружение по OpenAPI 3.1 |
| GET/POST | `/v1/projects` | Список сводок очереди или регистрация корня Git |
| GET | `/v1/projects/{id}` | Чтение сводки одного проекта |
| GET | `/v1/projects/{id}/queue` | Чтение постоянной очереди |
| GET | `/v1/projects/{id}/metrics` | Чтение итогов по токенам, стоимости, времени и качеству |
| GET/POST | `/v1/projects/{id}/chat` | Чтение или продолжение выбранного разговора с Supervisor |
| POST | `/v1/projects/{id}/plan` | Подтверждение материализации и постановки плана из чата |
| POST | `/v1/projects/{id}/run` | Запуск защищённого цикла очереди проекта |
| POST | `/v1/run-due` | Запуск каждого подошедшего по расписанию зарегистрированного проекта |
| POST | `/v1/a2a/push` | Приём аутентифицированного события StreamResponse A2A для идущей задачи |

Пример:

```bash
curl -H "Authorization: Bearer $HOH_SERVER_TOKEN" http://127.0.0.1:8765/v1/projects
curl -X POST -H "Authorization: Bearer $HOH_SERVER_TOKEN" -H "Content-Type: application/json" \
  -d '{"root":"/home/me/project","name":"Project"}' http://127.0.0.1:8765/v1/projects
curl -X POST -H "Authorization: Bearer $HOH_SERVER_TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"Implement the agreed feature and verify it."}' \
  http://127.0.0.1:8765/v1/projects/PROJECT_ID/chat
```

Запросы ограничены 1 МиБ, ответы не содержат секретных значений, CORS не включён, и неаутентифицированного
изменяющего эндпоинта нет. Долгие вызовы очереди и модели занимают только свой поток запроса; сервер
остаётся отзывчивым, а лежащие в основе аренды HoH не дают небезопасного наложения.

Маршрут обратного вызова A2A использует выделенный push-токен, а не `HOH_SERVER_TOKEN`. Доставка push
локальна для процесса по замыслу: обратный вызов обязан дойти до того же процесса сервера HoH,
который ждёт удалённую задачу. Десктопные вызовы используют автоматически управляемый приёмник на
loopback; SSE остаётся предпочтительным, когда Agent Card его объявляет.

## Фоновая служба на Linux и macOS

Десктопная страница **Проекты** и CLI вызывают один и тот же менеджер служб:

```bash
bash scripts/hoh.sh workspace service-install --interval-minutes 1 --json
bash scripts/hoh.sh workspace service-status --json
bash scripts/hoh.sh workspace service-uninstall --json
```

Linux устанавливает `~/.config/systemd/user/hoh-workspace.service` и `.timer`, затем использует
`systemctl --user`. macOS устанавливает
`~/Library/LaunchAgents/com.hoh.workspace-scheduler.plist` и использует `launchctl bootstrap` в
домене текущего GUI-пользователя. Оба вызывают `workspace run-due`; учётных данных они не хранят и
планировщик не дублируют.
