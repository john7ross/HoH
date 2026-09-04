# LLM Harness

[English](README.md) · **Русский**

<p align="center">
  <img src="Promo-GitHub.gif" alt="promo" width="200"/>
</p>

Исходники, задачи и релизы: <https://github.com/john7ross/HoH>

LLM Harness — десктопный и headless control plane для двух- или трёхуровневого процесса разработки:

1. Выбранный Supervisor — harness или структурированная модель — превращает расплывчатое намерение
   пользователя в проверяемые рабочие элементы.
2. Локальный worker-агент получает атомарные задачи и возвращает только патчи.
3. Если заказчик это включил, независимый critic проверяет патч, тесты, метрики и качество передачи
   и является единственной ролью, которая вправе закрыть задачу.

Реализация намеренно оставляет облачные LLM и локальных coding-агентов вне пакета. HoH предоставляет
вокруг этих инструментов обвязку: коммуникацию, git, очередь, верификацию, аудит, рантайм и передачу
результата.

Обзор продукта для пользователя доступен на [английском](docs/overview.md) и
[русском](docs/overview.ru.md).

## Состав релиза

Реализовано сейчас:

- Нативный десктопный GUI под Windows без внешних зависимостей на встроенном рантайме Tkinter: RU/EN,
  светлая и тёмная темы, раздельные элементы управления агентом, моделью и драйвером для
  Supervisor/Worker/Critic, обновление ACP Registry, пошаговые настройки проекта, явная справка по
  хранилищу и переменным окружения, отказ на старте при недоступном агенте, приватный многоходовый
  чат с Supervisor, подтверждаемый переход от чата к плану, пошаговая правка process-профилей,
  маскированные учётные данные только на сессию, операции с очередью, статусом и историей,
  выполнение всей очереди, повтор и восстановление, Doctor и финальный аудит.
- Управляемая установка ACP в пользовательскую область для точно закреплённых пакетов `npx`/`uv` и
  Windows-архивов с контрольной суммой: атомарные записи об установке, ограниченная распаковка,
  управление обновлением и удалением, отказ закрытым для неподписанных бинарников из Registry.
- Входы через объявленные в ACP и известные вендорские CLI. HoH может запустить интерактивный вход и
  проверить полученную сессию, не читая и не сохраняя вендорский токен.
- Удалённые драйверы Supervisor, Worker и Critic по A2A v1 JSON-RPC: обнаружение Agent Card,
  аутентификация bearer/API-key/OAuth2/OIDC, потоковая передача SSE, аутентифицированная доставка
  push с ограниченным откатом на опрос, отмена, ограниченные ответы и снимок для Worker по принципу
  минимума данных, ограниченный явными `allowed_paths`.
- Политика встроенного рантайма Python и команда его проверки.
- Сканер системы для настроенных локальных CLI-агентов, включая `codex`, `claude`, `hermes`,
  `openclaw` и декларативный профиль Aider.
- Проверка доступности Hermes ACP, изолированный сборщик промпта и живой смоук-тест.
- Обычные файлы задач в JSON и Markdown для живого исполнения через Hermes.
- Манифест возможностей worker'а в секции `[worker.capabilities]`.
- Версионированный реестр `hoh.driver` для транспортов Supervisor, Worker и Critic с явным выбором
  драйвера, не зависящим от имён агента и исполняемого файла.
- Универсальные адаптеры ACP v1 для Supervisor, Worker и Critic с выбором модели сессии и правами по
  ролям; официальный ACP Registry обновляется и кэшируется явной командой `role-catalog`.
- Разрешение запуска из ACP Registry предпочитает точные установки под управлением HoH и сохраняет
  прежний путь через лаунчер и `PATH` для агентов, которыми оператор управляет сам.
- Универсальные границы процессов: `command` для Worker и `command_json` для Critic; новый
  соответствующий агент настраивается без изменения ядра HoH.
- Декларативные `process`-профили Worker для одноразовых CLI, которым нужно размещение промпта в
  stdin или файле, обязательные и запрещённые аргументы, маршрутизация модели и проба версии. Aider
  поставляется как первая такая декларация с отключённым автокоммитом.
- Набор проверок соответствия адаптера Worker для детерминированного и живого смоука.
- Именованные ролево-безопасные адаптеры Claude Code и OpenClaw с неинтерактивным вызовом под
  конкретного провайдера, строгими проверками изолированной попытки, классификацией отказов и
  телеметрией без содержимого.
- Сквозной смоук управляемого workflow для гейтов плана, очереди, worker'а, верификатора, аудита и
  уведомителя.
- Версионированные вендоронезависимые JSON-конверты для команд плана, статуса, очереди, истории и
  аудита, обращённых к Supervisor.
- Неизменяемые review-бандлы для внешнего верификатора, привязанные к точному прогону, коммиту,
  дайджесту патча, области и детерминированным свидетельствам проверки.
- Обязательный трёхголовый режим с раздельными манифестами личности и модели для Logic, Worker и
  Critic, ограниченным числом попыток, автоматическим экспортом review-бандла и решениями
  `approve` / `reject` / `escalate`.
- Необязательный живой адаптер Critic на Claude Code со схемно-ограниченным выводом, отключёнными
  инструментами, автоматическим импортом решения и повтором без перезапуска Worker.
- Подтверждаемая Supervisor'ом переделка по неизменяемому correction brief от critic; ни Worker, ни
  Critic не получают права на коммит, merge, изменение требований и самоодобрение.
- Выбор ролей в project-spec v2, обращённый к заказчику, порождает `.hoh/role-profile.json`; заказчик
  выбирает агентов и модели в брифе и никогда не правит TOML.
- Необязательный Critic: при выключенном закрытие идёт через Supervisor после детерминированных
  проверок; при включённом требуется независимое одобрение Critic.
- Дописываемый журнал с редактированием секретов для сообщений, кадров адаптера, раскрытых вызовов
  инструментов агента, патчей, проверочных команд, действий с Git, решений, уведомлений и переходов
  состояния.
- JSON-схемы, примеры протокола и одноразовый смоук-поток `protocol-conformance`.
- Постоянная файловая очередь задач и история выполнения.
- Межпроцессная координация «один писатель» для состояния очереди, истории, ревью, операторских
  событий и аудита: атомарная долговечная замена, сохранение неизменяемых свидетельств, ограниченное
  ожидание блокировки, метаданные владельца, подтверждённое ядром восстановление после зависшей
  блокировки и отказ закрытым при повреждённом состоянии.
- Отдельная аренда на выполнение в репозитории сериализует канонические операции применения патча,
  проверки, коммита и отката, не удерживая блокировку состояния во время ходов Worker или модели.
- Долговечный журнал операций уровня репозитория закрывает окно падения между Git и StateStore. Он
  записывает намерение до канонического изменения, связывает коммиты через трейлеры `HoH-Operation`,
  автоматически завершает только точные идемпотентные записи состояния и блокирует новую работу
  Worker и Git при неоднозначности.
- Детерминированное планирование по DAG с зависимостями задач, целочисленными приоритетами,
  разрешением равенства по FIFO, диагностикой готовности и явным исходом «заблокировано
  зависимостью».
- Ручной повтор и восстановление прерванного прогона для задач в очереди.
- Поддержка сканером сырых локальных эндпоинтов моделей, например OpenAI-совместимых серверов
  llama.cpp.
- Команда `doctor` для локальной предполётной диагностики окружения.
- Настраиваемые эндпоинты моделей для supervisor и verifier.
- HTTPS-провайдеры без внешних зависимостей для OpenAI Responses, DeepSeek Chat Completions и явно
  настроенных OpenAI-совместимых эндпоинтов: учётные данные только из окружения, ограниченный повтор
  с классификацией ошибок и свидетельства с вырезанными секретами.
- Планирование «требования → project-spec» через `model_json`, универсальный `command_json` или
  ACP-драйверы Supervisor; прежний путь `--spec` остаётся офлайновым.
- Необязательная семантическая проверка с отказом закрытым через `verifier_model` — после
  обязательного детерминированного верификатора и до подготовки и коммита, которыми владеет
  Supervisor.
- Настраиваемый уровень доверия локальному worker'у.
- Жизненный цикл задания worker'а с контрактом обратного вызова о завершении.
- Контракт worker'а, возвращающий унифицированный diff.
- Изолированные попытки в git worktree для локального исполнения worker'ом.
- Отказ закрытым, если Worker сдвинул `HEAD` изолированной попытки; HoH остаётся единственным
  владельцем коммитов, даже если внешний CLI игнорирует свои флаги запрета коммита.
- `git apply`, запуск тестов, подготовка и коммит, которыми владеет Supervisor.
- Независимые детерминированные гейты верификатора.
- Интерфейс уведомителя Telegram с заглушкой без отправки и адаптером `sendMessage` Bot API.
- Детерминированная команда `audit` для финальных проверок готовности.
- Встроенные офлайновые анализаторы мёртвого кода и неиспользуемых файлов для Python и
  JavaScript/TypeScript с явными свидетельствами `passed`, `findings`, `unavailable` и
  `unsupported`.
- Документация SDLC, метрик, архитектурных диаграмм, финального аудита и правил передачи.
- Автоматическое обнаружение зависших задач в состоянии `running` с явным операторским
  восстановлением.
- Обработчик операторских команд, не зависящий от транспорта, с журналом аудита.
- Детерминированная материализация спецификации жизненного цикла проекта в Markdown-документы, файлы
  задач и очередь.
- Отслеживаемый lock рантайм-зависимостей, используемый внутренним payload'ом обновления и скриптами
  офлайновой установки.
- Релизный манифест SHA-256, связывающий внутренний payload обновления, wheelhouse, lock
  зависимостей, исходный коммит, целевую платформу, встроенный рантайм, чистоту исходников и
  выполненные гейты сборки.
- Явно доверенные самоподписанные RSA-каталоги релизов, установка рядом в пользовательской области,
  указатель отката, безопасная распаковка ZIP и процесс обновления внутри приложения — без платного
  удостоверяющего центра.
- Подписанные статические каталоги совместимости для бесплатной централизованной публикации,
  отделённые от того, что реально установлено на этой машине.
- Нативные потоки сборки Windows Setup, десктопного пакета Debian и приложения/DMG для macOS;
  планирование через systemd user и launchd; нативные уведомления рабочего стола; аутентифицированный
  headless control API с необязательным TLS и OpenAPI 3.1.

Состояние дорожной карты:

- Промышленная межпроцессная координация «один писатель» реализована для состояния, ревью, Telegram,
  канонической интеграции Git и отката, включая защищённое восстановление после падения и
  машиночитаемую диагностику блокировок.
- Автоматическая сверка после падения реализована для коммитов очереди и Supervisor и для
  промышленного отката: детерминированная инъекция падения, восстановление при одновременном старте,
  отказ закрытым при повреждённом журнале, починка настройки ревью, диагностика через
  status/doctor/operator и защищённое операторское разрешение. Этим текущая веха промышленного
  восстановления закрыта.
- Ядро control plane принимает любого соответствующего агента ACP Registry через один трёхролевой
  адаптер, устанавливает любую безопасно закреплённую дистрибуцию с контрольной суммой и раскрывает
  объявленные им методы аутентификации. Квота провайдера, политика организации и доступ к модели
  остаются внешними ограничениями учётной записи, а не скрытыми обходными путями.
- A2A v1 — граница удалённых агентов для всех трёх ролей. Соединение A2A локально для проекта,
  учётные данные остаются только в окружении, и поддержка протокола не означает, что произвольный
  удалённый сервис заработает.
- Supervisor и Critic дополнительно раскрывают прямые вендоронезависимые цели `model_json`. Вторая
  использует `verifier_model`, поэтому ревью через DeepSeek, OpenAI или OpenAI-совместимый эндпоинт
  не требует harness-процесса.
- Запись агента в каталоге объявляет необязательный `supervisor_driver`, обязательный
  `worker_driver` и необязательный `critic_driver`; HoH никогда не выводит драйвер из имени агента
  или базового имени исполняемого файла.
- Текущее исследование экосистемы и дорожная карта совместимости «ACP в первую очередь» описаны в
  [`docs/harness-ecosystem.ru.md`](docs/harness-ecosystem.ru.md). Работает ли конкретный агент — это
  вопрос к вашей машине, а не утверждение HoH: запустите `role-conformance --role <роль>` и
  посмотрите.
- Правила релиза и хранения свидетельств описаны в [`docs/release.ru.md`](docs/release.ru.md);
  уборка рабочего пространства регулируется
  [`docs/workspace-cleanup.ru.md`](docs/workspace-cleanup.ru.md).
- Готовые к вставке заметки к релизу на GitHub поддерживаются в
  [`RELEASE_NOTES.md`](RELEASE_NOTES.md) и [`RELEASE_NOTES.ru.md`](RELEASE_NOTES.ru.md).
- `compatibility-matrix` перечисляет, какие агенты установлены здесь, их версию и роли, которые
  покрывает их драйвер. `role-conformance --role <роль>` один раз прогоняет эту роль на одноразовом
  репозитории и сообщает, сработала ли она. Ни та, ни другая команда не пишет запись: ответ
  принадлежит машине, на которой его спросили, а сохранённый только устаревает.

## Быстрая проверка

```powershell
python -m unittest discover -s tests
python -m llm_harness doctor
python -m llm_harness demo
python -m llm_harness runtime
python -m llm_harness init-project --project-root C:\path\to\HoH
python -m llm_harness role-catalog --refresh-registry --json
python -m llm_harness gui --project-root C:\path\to\repo
python -m llm_harness gui --smoke --locale ru --theme dark
python -m llm_harness driver-catalog --json
python -m llm_harness compatibility-matrix --project-root C:\path\to\repo --json
python -m llm_harness publisher import --publisher C:\path\hoh-publisher.public.json
python -m llm_harness update check --source https://host/HoH-releases.signed.json --json
python -m llm_harness update install --source https://host/HoH-releases.signed.json --json
python -m llm_harness server --host 127.0.0.1 --port 8765
python -m llm_harness role-conformance --project-root C:\path\to\repo --role worker --json
python -m llm_harness lock-status --project-root C:\path\to\repo --json
python -m llm_harness reconcile-status --project-root C:\path\to\repo --json
python -m llm_harness reconcile-apply --project-root C:\path\to\repo --operation-id <id> --action complete-state --json
python -m llm_harness hermes-check
python -m llm_harness hermes-smoke --timeout 180
python -m llm_harness worker-conformance --config harness.toml --timeout 300
python -m llm_harness worker-conformance --config harness.toml --timeout 300 --json
python -m llm_harness worker-smoke --config harness.toml --json
python -m llm_harness worker-smoke --config harness.toml --live --json
python -m llm_harness workflow-smoke
python -m llm_harness workflow-smoke --mode blocker
python -m llm_harness hermes-run --project-root C:\path\to\repo --task C:\path\to\task.md --timeout 300
python -m llm_harness worker-run --config harness.toml --project-root C:\path\to\repo --task C:\path\to\task.md
python -m llm_harness project-plan --project-root C:\path\to\repo --spec C:\path\to\project.json --write --enqueue
python -m llm_harness project-plan --config harness.toml --project-root C:\path\to\repo --requirements C:\path\to\requirements.md --write --enqueue
python -m llm_harness model-smoke --config harness.toml --role supervisor
python -m llm_harness model-smoke --config harness.toml --role supervisor --live
python -m llm_harness queue-add --project-root C:\path\to\repo --task C:\path\to\task.md
python -m llm_harness queue-list --project-root C:\path\to\repo
python -m llm_harness queue-run-next --project-root C:\path\to\repo --timeout 300
python -m llm_harness queue-run-loop --project-root C:\path\to\repo --timeout 300 --config harness.toml --parallelism 2
python -m llm_harness rollback-plan --project-root C:\path\to\repo --task-id task-001 --json
python -m llm_harness rollback-history --project-root C:\path\to\repo --json
python -m llm_harness queue-history --project-root C:\path\to\repo
python -m llm_harness audit-history --project-root C:\path\to\repo
python -m llm_harness supervisor-status --project-root C:\path\to\repo
python -m llm_harness supervisor-status --project-root C:\path\to\repo --json
python -m llm_harness review-export --project-root C:\path\to\repo --run-id <run-id> --json
python -m llm_harness review-import --project-root C:\path\to\repo --decision C:\path\to\decision.json --json
python -m llm_harness critic-run --config harness.toml --project-root C:\path\to\repo --json
python -m llm_harness review-list --project-root C:\path\to\repo --json
python -m llm_harness journal-list --project-root C:\path\to\repo --json
python -m llm_harness journal-record --project-root C:\path\to\repo --event C:\path\to\event.json --json
python -m llm_harness journal-export --project-root C:\path\to\repo --output C:\path\to\journal.md
python -m llm_harness protocol-conformance --distribution-root . --json
python -m llm_harness three-head-conformance --json
python -m llm_harness queue-stale --project-root C:\path\to\repo --max-age-minutes 60
python -m llm_harness queue-retry --project-root C:\path\to\repo --task-id task-001
python -m llm_harness queue-recover-running --project-root C:\path\to\repo --task-id task-001
python -m llm_harness operator-command --project-root C:\path\to\repo --text "/status"
python -m llm_harness telegram-poll --config harness.toml --project-root C:\path\to\repo
python -m llm_harness telegram-watch --config harness.toml --project-root C:\path\to\repo --iterations 12
python -m llm_harness audit --config harness.toml --check "python -m unittest discover -s tests" --markdown
```

Демонстрация создаёт временный git-репозиторий, просит у stub-worker'а патч, применяет его через
supervisor, выполняет проверочную команду и коммитит результат.

Для упакованного десктопного использования запустите
`scripts\hoh-gui.ps1 -ProjectRoot C:\path\to\repo`. Окно хранит в профиле пользователя только язык,
тему и последний путь к проекту. Политика проекта пишется в `.hoh/harness.json`, выбор ролей — в
`.hoh/role-profile.json`, а значения секретов GUI не хранит никогда. Сценарии, границы безопасности и
приёмочные проверки описаны в [`docs/gui.ru.md`](docs/gui.ru.md).

Команда аудита — детерминированный гейт готовности. Она проверяет чистоту git, наличие README,
Markdown-документацию, обязательные архитектурные диаграммы, артефакты жизненного цикла в
`docs/hoh/` и `tasks/hoh/`, если они есть, блокирующие маркеры вроде TODO/FIXME/placeholder <!-- hoh-audit: ignore-line -->,
сгенерированные артефакты и явные проверочные команды, переданные
через `--check`. Внутри сгенерированных спецификаций жизненного цикла требования, которые явно
запрещают такие маркеры или утверждают их отсутствие, считаются текстом политики; настоящий
неразрешённый маркер остаётся блокирующим. Аудит также запускает встроенные анализаторы, выбранные в
секции `[audit]`. Анализ Python использует AST стандартной библиотеки, чтобы найти недостижимые
инструкции и не упомянутые нигде приватные объявления верхнего уровня. Анализ неиспользуемых файлов
для Python, JavaScript и TypeScript строит статический граф относительных импортов. При заданных
`entry_points` любой отслеживаемый исходный файл вне достижимого графа блокирует; без них
консервативное поведение по умолчанию сообщает только о приватных файлах-сиротах. Свидетельства
анализаторов попадают в Markdown и JSON даже тогда, когда язык отсутствует (`unsupported`) или
анализатор не может запуститься (`unavailable`). HoH только сообщает о находках и никогда не удаляет
файлы-кандидаты.

Команда doctor — предполётная проверка локального окружения только на чтение. Она проверяет Git,
раскладку встроенного рантайма, настроенный исполняемый файл worker'а, согласованность манифеста
возможностей worker'а, настроенные CLI агентов, локальные эндпоинты моделей, URL провайдеров
supervisor и verifier, наличие переменных окружения с учётными данными и наличие переменных
окружения Telegram. Она не выполняет ни одного запроса к провайдеру и не отправляет сообщений в
Telegram.

## Лицензия

HoH распространяется по Apache License 2.0. Copyright 2026 Sergey Lebedev. См. [`LICENSE`](LICENSE)
и [`NOTICE`](NOTICE). Лицензия разрешает использование, изменение и распространение, включая
коммерческое, при условии сохранения уведомления об авторских правах и текста лицензии. Вложенные
сторонние компоненты сохраняют собственные лицензии — см.
[`THIRD-PARTY-NOTICES.ru.md`](THIRD-PARTY-NOTICES.ru.md).

О работе над самим HoH — [`CONTRIBUTING.ru.md`](CONTRIBUTING.ru.md).

## Набросок конфигурации

Встроенные интеграции с провайдерами OpenAI и DeepSeek используют конфигурацию TOML или JSON такого
вида:

```toml
# Ключи верхнего уровня должны идти до первого заголовка [таблицы].
worker_trust_level = "patch_only"

[supervisor_model]
provider = "openai"
model = "<одобренная модель OpenAI>"
api_key_env = "OPENAI_API_KEY"
timeout_seconds = 60
max_retries = 2
max_output_tokens = 8192

[supervisor]
# model_json использует [supervisor_model]; acp и command_json запускают внешний harness.
driver = "model_json"
timeout_seconds = 300

[verifier_model]
provider = "deepseek"
model = "<одобренная модель DeepSeek>"
api_key_env = "DEEPSEEK_API_KEY"
timeout_seconds = 60
max_retries = 2
max_output_tokens = 8192

[runtime]
require_embedded_python = true
embedded_python_path = "runtime/python/python.exe"
wheels_path = "vendor/wheels"

[scheduler]
max_parallel_tasks = 2

[coordination]
state_lock_timeout_seconds = 5
execution_lock_timeout_seconds = 5
poll_interval_seconds = 0.05

[audit]
language_analyzers = ["python", "javascript", "typescript"]
# Заданные точки входа включают строгую проверку достижимости для каждого языка с подходящим корнем.
entry_points = ["src/app.py", "src/index.ts"]
exclude_paths = ["vendor/**", "generated/**"]
fail_on_unavailable = true

[worker]
driver = "hermes_acp"
command = "hermes"
args = ["acp"]
timeout_seconds = 300

[worker.capabilities]
task_transport = "acp_stdio"
artifact_contract = "worktree_diff"
requires_isolated_worktree = true
supports_subagents = false

[critic]
# manual = явные review-export и review-import
# model_json = прямое структурированное ревью через [verifier_model]
# claude_code = автоматическое ревью Claude Code только на чтение
# command_json = любой совместимый JSON-CLI только на чтение
driver = "manual"
command = "claude"
args = []
timeout_seconds = 300
# max_budget_usd = 2.0

[telegram]
enabled = false
bot_token_env = "HOH_TELEGRAM_BOT_TOKEN"
chat_id_env = "HOH_TELEGRAM_CHAT_ID"
user_id_env = "HOH_TELEGRAM_USER_ID"

[[agents]]
name = "codex"
command = "npx"
args = ["-y", "@agentclientprotocol/codex-acp@1.7.0"]
supervisor_driver = "acp"
worker_driver = "acp"
critic_driver = "acp"

[[agents]]
name = "claude"
command = "claude"
worker_driver = "claude_code"
critic_driver = "claude_code"

[[agents]]
name = "hermes"
command = "hermes"
args = ["acp"]
supervisor_driver = "acp"
worker_driver = "hermes_acp"

[[agents]]
name = "openclaw"
command = "openclaw"
worker_driver = "openclaw"

# Пример необязательного локального эндпоинта модели. Раскомментируйте, только когда он запущен.
# [[local_models]]
# name = "qwen-llama-cpp"
# base_url = "http://127.0.0.1:8080"
# model = "qwen"
# protocol = "openai_compatible"
```

Уровни доверия worker'у:

- `patch_only` — единственный уровень, реализованный в этом релизе: worker получает одноразовый
  worktree и возвращает патч. Он никогда не создаёт ветку или коммит в каноническом репозитории;
  каждым изменением состояния Git владеет supervisor.
- `branch_only` и `branch_and_commit` описаны в `docs/architecture.ru.md` как предполагаемое
  расширение. Они **не поддерживаются**, и настройка любого из них отклоняется при загрузке, а не
  молча понижается.

Локальные цели исполнения намеренно разделены:

- `local_agent` — CLI coding-агента, например Codex, Claude Code или Hermes.
- `local_model_endpoint` — сырой сервер модели вроде llama.cpp; ему нужен адаптер-исполнитель,
  прежде чем он сможет безопасно касаться репозиториев.

Политика жизненного цикла проекта:

- Supervisor сначала запускает `role-catalog --json` и задаёт четыре вопроса обычным языком: какой
  агент или модель будет Supervisor, какой — Worker, нужен ли Critic (и какой агент или модель) и
  сколько попыток Worker разрешено. Имена моделей необязательны.
- `examples/project-spec-v2.json` — машинный артефакт, который Supervisor создаёт из разговора с
  заказчиком; это не форма, которую заказчик обязан заполнять.
- `project-plan --write` порождает `.hoh/role-profile.json`, и команды исполнения загружают его
  автоматически. Один и тот же продукт-агент или модель может занимать разные роли в разных сессиях.
- `project-plan` проверяет составленную supervisor'ом спецификацию проекта до постановки работы в
  очередь.
- Обязательные поля спецификации: идентификатор проекта, заголовок, цель, заказчик, бизнес-требования,
  определение готовности и хотя бы одна атомарная задача.
- `project-plan --write` материализует Markdown-документы в `docs/hoh/` и JSON-файлы задач в
  `tasks/hoh/`.
- `project-plan --write --enqueue` дополнительно ставит порождённые файлы задач в состояние HoH.
- Команда детерминирована: она проверяет и материализует явный ввод, но не придумывает требования и
  критерии приёмки.

Протокол, обращённый к Supervisor:

- Внешний supervisor пишет JSON спецификации проекта с бизнес-требованиями, определением готовности и
  атомарными задачами.
- HoH проверяет и материализует эту спецификацию через `project-plan --write --enqueue`.
- Долговечные артефакты передачи — `docs/hoh/project-brief.md`, `docs/hoh/roadmap.md`, JSON-файлы
  задач в `tasks/hoh/`, состояние очереди, история прогонов, отчёты аудита и коммиты git.
- Supervisor наблюдает за исполнением через `queue-list`, `queue-history`, `audit-history`,
  `rollback-history`, `supervisor-status`, `operator-command /status` и свидетельства финального
  аудита.
- Добавьте `--json` к `project-plan`, `supervisor-status`, `audit`, `audit-history`, к каждой команде
  `rollback-*` и к каждой команде `queue-*`, чтобы получить стабильный конверт `hoh.protocol` v1.
  `queue-run-loop --json` выдаёт один итоговый документ, а не смесь строк прогресса. Контракт
  верхнего уровня — `protocol`, `protocol_version`, `message_type`, `ok`, `generated_at_utc` и
  `data`, причём `error` присутствует только при отказе.
- Контракт верификатора и ревьюера явный: каждая задача обязана определять критерии приёмки,
  проверочные команды, разрешённые пути и не-цели; HoH записывает находки до применения, находки
  после применения, результаты команд, идентификаторы коммитов и свидетельства финального аудита.
- `workflow-smoke` доказывает этот протокол без секретов, прогоняя весь путь в одноразовом
  репозитории с детерминированным command-worker'ом и уведомителем-заглушкой.

Передача внешнему верификатору:

- `three_head.mode = "manual"` сохраняет ручной процесс внешнего ревью версии v1. В режиме
  `mode = "required"` HoH переводит успешный прогон worker'а в `review_pending` и экспортирует
  critic-бандл версии v2.
- `[critic] driver = "manual"` останавливается на этом для явной передачи.
  `[critic] driver = "model_json"` вызывает настроенный `verifier_model` с той же закрытой схемой
  решения, без пути к репозиторию и без инструментов. `[critic] driver = "claude_code"` вызывает
  Claude Code неинтерактивно, с отключёнными инструментами и схемно-ограниченным структурированным
  выводом, а затем импортирует решение через тот же валидатор протокола.
- `critic-run` повторяет неудавшееся автоматическое ревью на существующем неизменяемом бандле; он
  никогда не перезапускает Worker. Отказы процесса и таймауты оставляют задачу в `review_pending`.
- Обязательный режим использует раздельные ролевые личности сессий. Снимок роли и предел попыток
  вложены в каждый неизменяемый бандл, поэтому позднейшая настройка не может ослабить задачу в
  полёте.

- `review-export --run-id <id>` пишет канонический бандл под корнем состояния HoH. Параметр
  `--output` может дополнительно скопировать те же свидетельства по пути передачи верификатору.
- Бандл содержит атомарную задачу, разрешённые пути, не-цели, изменённые файлы, полный diff коммита,
  свидетельства SHA-256 для коммита и патча, детерминированные находки политики, вывод проверочных
  команд и матрицу полномочий.
- Внешний верификатор возвращает документ, соответствующий
  `schemas/verifier-decision-v1.schema.json`. `review-import` проверяет закрытые поля, версию
  протокола, дайджест бандла, отревьюенный коммит и дайджест отревьюенного патча до того, как
  дописать неизменяемое свидетельство.
- Экспорт бандла создаёт ожидающий гейт ревью. `supervisor-status` не сообщает о готовности, пока
  последнее экспортированное ревью задачи в состоянии «ожидает» или «отклонено». Действительное
  одобрение снимает этот гейт.
- `queue-run-loop --final-audit` отказывается уведомлять о готовности, пока экспортированное ревью
  ожидает или отклонено, и отправляет через настроенный уведомитель сообщение о необходимости
  действия пользователя.
- Отклонение никогда не меняет Git автоматически. Supervisor может создать работу по исправлению или
  воспользоваться явной политикой отката, изучив свидетельства `rollback-plan` и подтвердив точный
  коммит, причину и проверки после отката.
- Дополнительных провайдеров Supervisor и verifier, а также дополнительных провайдеров Critic можно
  добавить за документированными границами. Любой агент или сервис, умеющий читать и писать
  JSON-контракт, по-прежнему может использовать ручную границу.
- Адаптер Claude спрашивает у модели только поля решения. HoH подставляет канонический идентификатор
  бандла, дайджест бандла, личность Critic, аттестацию коммита, аттестацию патча и метку времени, и
  только затем проверяет и сохраняет итоговое решение.

Решения critic в трёхголовом режиме используют `schemas/critic-decision-v2.schema.json`:

- `approve` закрывает задачу и снимает гейт готовности;
- `reject` записывает находки и обязательный correction brief, после чего ставит `rework_required`;
- `review-rework --decision-id <id>` — явное подтверждение supervisor'а, ставящее в очередь именно
  эти инструкции по исправлению, не меняя исходной цели, области и не-целей;
- `escalate` блокирует задачу и уведомляет заказчика вопросом с вариантами, включая «реши сам» и
  «свой»;
- достижение неизменяемого `max_attempts` бандла также приводит к эскалации, а не к бесконечному
  циклу.

Запустите `three-head-conformance --json`, чтобы доказать одобрение, отклонение, подтверждённую
supervisor'ом переделку, одобрение со второй попытки, эскалацию, закрытие при выключенном Critic и
чистое каноническое состояние git — без провайдеров, сети и секретов.

Журнал взаимодействий:

- `interaction-journal.jsonl` живёт под внешним корнем состояния HoH. У каждого события есть время в
  UTC, действующее лицо, получатель, действие, идентификаторы задачи, прогона и корреляции,
  содержимое и метаданные.
- `journal-list` фильтрует события; `journal-export` создаёт JSONL или Markdown с вырезанными
  секретами.
- Ключи, похожие на секреты, токены ботов Telegram, учётные данные Bearer и присвоения секретных
  переменных окружения вырезаются до записи на диск.
- Записываются сообщения инструментов ACP и телеметрия процессов обычных CLI. Приватные вызовы
  инструментов внутри непрозрачного агента можно показать, только если его протокол их выдаёт.
- Внешние поверхности Supervisor и Critic используют закрытую
  `schemas/journal-ingress-v1.schema.json` и команду `journal-record`, чтобы дописать сообщения
  брифинга или события инструментов, происходящие вне адаптеров HoH.

Политика вендоронезависимых драйверов:

- `project-plan --requirements` исполняется через настроенный адаптер `[supervisor]`.
- `worker-run` загружает один файл задачи в JSON или Markdown и исполняет его через настроенный
  адаптер `[worker]`.
- `queue-run-next` и `queue-run-loop` используют тот же адаптер `[worker]` вместо жёстко зашитого
  конкретного агента.
- `driver` — каноническое поле конфигурации. Устаревшее `type` остаётся псевдонимом для
  совместимости, а конфликтующие значения `driver` и `type` приводят к отказу закрытым.
- Встроенный реестр версионирован как `hoh.driver` `1.0`; изучить его можно командой
  `driver-catalog`.
- Внешний ACP Registry кэшируется только по явной команде `role-catalog --refresh-registry`; после
  этого идентификатор из реестра может занять любую роль без правки TOML.
- Драйвер worker'а по умолчанию остаётся `hermes_acp` ради совместимости при обновлении.
- `command` запускает обычный CLI-worker в изолированном git worktree и позволяет HoH собрать
  получившийся diff. Используйте его для обёрток над Hermes, собственных скриптов и других локальных
  CLI агентов, которые умеют работать из пути репозитория.
- `process` использует декларативный одноразовый профиль, когда CLI нужен временный файл промпта или
  фиксированные флаги безопасности и модели. Декларация Aider по умолчанию обеспечивает исполнение
  без потоковой передачи, без интерактивности и без автокоммита; другой CLI добавляется в
  `agents[].worker_process_profile` без Python-адаптера.
- `claude_code` вызывает Claude Code в режиме print/JSON с `safe-mode`, `dontAsk`, без сохранения
  сессии и только с инструментами `Read`, `Edit`, `Write`, `Glob` и `Grep`. Bash, MCP, плагины,
  Chrome, фоновые агенты, обход разрешений и пользовательские флаги безопасности недоступны.
- `openclaw` вызывает `openclaw agent --local --json` с уникальной сессией и временной конфигурацией
  без секретов. Рабочим пространством служит worktree попытки; присутствуют только ограниченные
  рабочим пространством инструменты `read`, `write`, `edit` и `apply_patch`. Exec, процессы, браузер,
  обмен сообщениями, доставка, фоновые сессии и привилегированные инструменты запрещены.
- `stub` доступен только для детерминированных тестов и локальных демонстраций.
- Неизвестные идентификаторы драйверов приводят к явному отказу; молчаливого отката к другому
  worker'у нет.
- Именованные адаптеры провайдеров отклоняют принадлежащие HoH и небезопасные флаги CLI в
  `[worker].args`.
- `worker-smoke` по умолчанию выполняет только предполётную проверку исполняемого файла, версии и
  безопасности. Флаг `--live` требуется, чтобы разрешить ход соответствия на одноразовом репозитории.

Манифест возможностей worker'а:

- `[worker.capabilities]` объявляет контракт, которого HoH ждёт от настроенного worker'а.
- `task_transport` описывает, как HoH передаёт задачу адаптеру worker'а, например `acp_stdio`,
  `stdin_prompt`, `profiled_process` или `in_process`.
- `artifact_contract` описывает артефакт, который HoH принимает от адаптера. Сейчас поддерживаются
  значения `worktree_diff` и `direct_patch`.
- `requires_isolated_worktree` обязан совпадать с реальным поведением адаптера; `doctor` и
  `worker-conformance` падают при расхождении.
- `supports_subagents` документирует, вправе ли worker запускать собственных внутренних субагентов.
  HoH всё равно считает границу worker'а недоверенной и принимает только объявленный контракт
  артефакта.

Контракт command-worker'а:

- HoH запускает `command` с `args`, а рабочим каталогом процесса делает worktree попытки.
- HoH отправляет ограниченный промпт задачи в stdin.
- HoH задаёт `HOH_JOB_ID`, `HOH_CALLBACK_TOKEN`, `HOH_REPOSITORY`, `HOH_WORK_ITEM_ID`,
  `HOH_WORK_ITEM_TITLE` и `HOH_WORK_ITEM_JSON`.
- Код выхода `0` означает, что worker закончил писать файлы-кандидаты; затем HoH собирает git diff из
  изолированного worktree.
- Ненулевой код выхода или таймаут — блокер worker'а, и канонический репозиторий при этом не
  меняется.
- Worker не должен коммитить, ветвиться, мержить, пушить и решать вопрос готовности.
- До сбора diff HoH проверяет, что попытка всё ещё указывает на исходный коммит. Сдвинутый `HEAD`
  отклоняет завершение, а одноразовая попытка удаляется.

Декларативные process-профили описаны в
[`docs/agent-neutral-drivers.ru.md`](docs/agent-neutral-drivers.ru.md). Используйте их вместо
обёртки, когда внешний CLI умеет отработать один раз, править свой текущий рабочий каталог и давать
детерминированную семантику промпта и кода выхода.

Пример конфигурации обычного command-worker'а:

```toml
[worker]
driver = "command"
command = "python"
args = ["path/to/worker_cli.py"]
timeout_seconds = 300

[worker.capabilities]
task_transport = "stdin_prompt"
artifact_contract = "worktree_diff"
requires_isolated_worktree = true
supports_subagents = false
```

Пример worker'а Claude Code:

```toml
[worker]
driver = "claude_code"
command = "claude"
model = "sonnet"
timeout_seconds = 300
max_budget_usd = 2.0
```

Пример worker'а OpenClaw:

```toml
[worker]
driver = "openclaw"
command = "openclaw"
model = "openai/gpt-5.6-sol"
thinking = "high"
timeout_seconds = 300
```

OpenClaw требует явно указанной модели, потому что его адаптер в HoH намеренно не наследует
потенциально более широкую постоянную конфигурацию агента у оператора. Порождённая конфигурация на
один ход удаляется после завершения процесса.

Политика Hermes ACP:

- `hermes acp --check` проверяет, что Hermes ACP доступен.
- HoH подавляет глобально настроенные MCP-серверы для изолированной ACP-сессии. На Windows он
  запускает Hermes вне родительского Win32 Job Object и применяет локальный для процесса обходной
  приём ровно для той пробы работоспособности Git Bash, которая может привести к взаимной блокировке
  во вложенных ACP-хостах. Обходной приём не обходит разрешения ACP на правку и команды, не заменяет
  установку Hermes у пользователя и не одобряет произвольные команды.
- `hermes-dry-run` печатает точный промпт worker'а для изолированного worktree, который отправил бы
  HoH.
- `hermes-smoke` выполняет настоящий ход Hermes ACP во временном git-репозитории через изолированную
  попытку и коммитит только после прохождения проверок верификатора.
- `worker-conformance --config harness.toml` прогоняет набор проверок соответствия обычного адаптера
  против настроенного worker'а. При `[worker] driver = "hermes_acp"` это переносимый поток живого
  смоука worker'а для Hermes.
- `hermes-run` остаётся командой совместимости, специфичной для Hermes. Для настраиваемого исполнения
  используйте `worker-run` или команды очереди с `[worker] driver = "hermes_acp"`.
- Адаптеры worker'а теперь можно обернуть в изолированную попытку git worktree. Обёртка позволяет
  локальному агенту писать только внутри временного worktree, подготавливает этот worktree, собирает
  двоичный унифицированный diff, удаляет worktree и возвращает diff supervisor'у.
- Живое исполнение Hermes принимается только с ветки вида `hoh/attempt/*`; прямое исполнение на
  канонической ветке репозитория отклоняется.

Формат файла задачи в JSON:

```json
{
  "id": "task-001",
  "title": "Create artifact",
  "objective": "Create HARNESS_DEMO.md with a short sentence.",
  "acceptance_criteria": ["HARNESS_DEMO.md exists."],
  "verification_commands": ["python -c \"from pathlib import Path; assert Path('HARNESS_DEMO.md').exists()\""],
  "allowed_paths": ["HARNESS_DEMO.md"],
  "non_goals": ["Do not edit docs."],
  "depends_on": ["task-000"],
  "priority": 10
}
```

Формат файла задачи в Markdown:

```markdown
# Create artifact

## id

task-001

## objective

Create HARNESS_DEMO.md with a short sentence.

## acceptance criteria

- HARNESS_DEMO.md exists.

## verification commands

- python -c "from pathlib import Path; assert Path('HARNESS_DEMO.md').exists()"

## allowed paths

- HARNESS_DEMO.md

## non-goals

- Do not edit docs.

## depends on

- task-000

## priority

10
```

Политика очереди и истории:

- `queue-add` сохраняет проверенную задачу в постоянной очереди.
- `depends_on` необязателен и перечисляет идентификаторы задач, которые должны дойти до `done`;
  `priority` — необязательное целое со значением по умолчанию `0`.
- Планы проекта проверяют все идентификаторы зависимостей и отклоняют циклы до материализации.
  Отдельный `queue-add` требует, чтобы каждая зависимость уже существовала.
- `queue-run-next` выбирает только задачи, готовые по зависимостям, затем берёт наивысший приоритет и
  использует порядок вставки как детерминированное разрешение равенства. Выбор и переход
  `queued → running` — одно захваченное под блокировкой действие, поэтому разные процессы HoH не
  могут захватить одну задачу.
- Если задачи в очереди есть, но ни одна не готова, команды очереди сообщают `dependency_blocked` и
  перечисляют каждую зависимость с её текущим статусом; это отличается от пустой очереди.
- Выбранная задача проходит через настроенный адаптер worker'а, при необходимости через изолированные
  попытки worktree, проверки верификатора и коммит, которым владеет supervisor.
- `queue-run-loop` формирует ограниченные пакеты готовых по зависимостям задач в порядке приоритета и
  FIFO. Подготовка worker'ов совмещается только тогда, когда области `allowed_paths` не пересекаются;
  проверки верификатора, каноническое применение патча, коммиты, переходы ревью и записи состояния
  остаются сериализованными.
- Значение по умолчанию задаётся через `[scheduler] max_parallel_tasks = N` или переопределяется
  через `--parallelism N`. По умолчанию `1`. Задача без `allowed_paths` считается монопольной.
- Обязательное трёхголовое ревью и адаптеры worker'а без поддержки изолированного worktree
  автоматически используют параллелизм `1`, сохраняя барьеры ревью и канонического репозитория.
- `queue-run-loop --final-audit --final-check "<команда>"` выполняет финальный аудит готовности, когда
  очередь пустеет, сохраняет Markdown-отчёт аудита под корнем состояния и затем уведомляет заказчика,
  что проект готов или что аудит не пройден.
- `queue-list` показывает выбранную задачу, множества готовых и ожидающих, приоритеты, блокирующие
  зависимости, попытки, последний коммит и последнюю ошибку.
- `queue-history` показывает неизменяемые записи прогонов.
- `audit-history` показывает сохранённые записи свидетельств финального аудита и пути к отчётам.
- `queue-stale` сообщает о задачах в состоянии `running`, чей `updated_at_utc` старше заданного
  порога.
- `queue-retry` возвращает в очередь неудавшуюся задачу после того, как оператор изучил отказ.
- `queue-recover-running` возвращает в очередь вручную подтверждённую зависшую задачу `running` после
  прерванного процесса или перезагрузки машины.
- По умолчанию состояние очереди хранится вне канонического репозитория по пути
  `<репозиторий-родитель>/.hoh-state/<имя-репозитория>-<хеш>`, чтобы операционное состояние не делало
  git worktree грязным до создания изолированной попытки.
- Каталог состояния переопределяется параметром `--state-root`.
- Каждое изменение состояния использует аренду на уровне ОС по пути
  `<state-root>/.coordination/state.lock`. Метаданные владельца фиксируют идентификатор аренды, PID,
  хост, команду, действие и время захвата. Состояние в JSON и JSONL пишется через уникальный
  временный файл в том же каталоге, сбрасывается на диск через `fsync` и заменяется атомарно.
- Каноническое изменение Git использует отдельную аренду репозитория по пути
  `<репозиторий-родитель>/.hoh-leases/<имя-репозитория>-<хеш>/execution.lock`. Медленные ходы Worker
  не удерживают аренду состояния; каноническая интеграция и откат аренду репозитория удерживают.
- Секция `[coordination]` настраивает ограниченное время ожидания состояния и исполнения и интервал
  опроса. При конкуренции возвращается диагностика владельца, а не удаление или кража живой
  блокировки.

Повтор и восстановление — явные действия оператора. `queue-run-next` запускает только задачи со
статусом `queued`; он не перезапускает автоматически задачи `failed` и `running`. `queue-run-loop`
проверяет зависшие задачи `running` перед началом новой работы, уведомляет заказчика, когда они есть,
и останавливается до тех пор, пока оператор не подтвердит восстановление или другое действие. Порог
зависания по умолчанию — 60 минут, он меняется параметром `--stale-minutes`; значение
`--stale-minutes 0` отключает предполётную проверку.

Политика промышленного отката:

- `rollback-plan --task-id <id> --json` работает только на чтение. Она разрешает последний успешный
  прогон HoH до полного коммита Git, сообщает точные изменённые файлы и транзитивно зависимые задачи
  и перечисляет каждый блокер политики.
- `rollback-apply` требует `--expected-commit`, `--reason` и одну или несколько команд `--check` для
  проверки после отката. Ожидаемый коммит — подтверждение оптимистичной блокировки, а не
  произвольная ревизия.
- Цель обязана быть последним успешным коммитом задачи, находящейся сейчас в `done`, обязана быть
  некорневым и не-merge предком `HEAD`, а канонический репозиторий обязан быть чистым.
- Идущая или неразрешённая работа блокирует откат. Завершённые или активные транзитивно зависимые
  задачи тоже блокируют его; HoH никогда не каскадирует откат молча. Поставленная в очередь
  зависимая работа может остаться в очереди и становится заблокированной зависимостью после того, как
  цель переходит в `rolled_back`.
- HoH создаёт обратный патч через `git revert --no-commit` в изолированном worktree, выполняет там
  все переданные оператором проверки, повторно проверяет политику, применяет точный патч к
  каноническому репозиторию, снова выполняет проверки и создаёт один коммит отката, которым владеет
  Supervisor.
- Аренда исполнения репозитория покрывает планирование отката, изолированную проверку, каноническое
  применение, проверку и коммит. Конкурирующий процесс падает до изменения Git и сообщает живого
  владельца.
- HoH никогда не использует `git reset --hard`. Неудавшаяся изолированная проверка оставляет
  канонический Git нетронутым; неудавшаяся каноническая проверка восстанавливает только пути целевого
  коммита. Каждое попавшее в изолированную фазу исполнение получает дописываемое свидетельство в
  `rollback-records.jsonl`.
- Успешный повтор того же запроса «задача/цель» возвращает существующую запись, а не откатывает
  откат. `rollback-history` раскрывает свидетельства, а `supervisor-status` сообщает об откаченных
  задачах и последнем откате.
- Финальная передача блокируется, пока жизненный цикл любой последней задачи находится в
  `rolled_back`. Прежде чем запускать финальный аудит, поставьте задачу в очередь заново и завершите
  исправленный жизненный цикл для того же идентификатора.

Пример:

```powershell
.\scripts\hoh.ps1 rollback-plan `
  --project-root C:\path\to\repo `
  --task-id task-001 `
  --json

.\scripts\hoh.ps1 rollback-apply `
  --project-root C:\path\to\repo `
  --task-id task-001 `
  --expected-commit <полный-sha-из-плана> `
  --reason "Production regression" `
  --check "python -m unittest discover -s tests" `
  --json
```

Политика уведомления о блокере:

- Блокер — исключение worker'а или адаптера либо результат задачи, отклонённый гейтами верификатора
  или тестов.
- `queue-run-loop` перестаёт отправлять новые пакеты после блокера или после находки предполётной
  проверки зависших `running`. Уже начатые независимые задачи того же пакета доводятся до конца и
  записываются, и лишь затем цикл сообщает о блокере.
- Заблокированная задача остаётся записанной как `failed`, пока оператор явно её не повторит.
- Если Telegram включён через `--config`, HoH отправляет заказчику сообщение с идентификатором
  задачи, причиной блокера и вариантами уточнения.
- Варианты уточнения всегда включают «реши сам» и «свой», а также конкретные варианты вроде повтора
  или остановки для ручного разбора.

Политика уведомления о финальной передаче:

- `--final-audit` включается явно, потому что каждому проекту нужны собственные проверочные команды.
- Финальная передача блокируется до аудита проекта, пока последний жизненный цикл очереди содержит
  состояния queued, running, failed, review-pending, rework-required, escalated, зависшее или
  несверенное. Изучите `queue-list` и `supervisor-status`, затем воспользуйтесь явной операцией
  повтора или восстановления.
- Передайте одну или несколько команд `--final-check`; они попадают в тот же детерминированный гейт
  аудита, что и `llm-harness audit --check`.
- HoH пишет свидетельства аудита в `audit-reports/<метка-времени>-<id>.md` и дописывает запись
  индекса в `audit-reports.jsonl` под корнем состояния очереди.
- Если аудит пройден, HoH вызывает `notify_project_ready` и включает путь к свидетельствам.
- Если аудит не пройден, HoH вызывает `notify_audit_failed`, включает путь к свидетельствам, печатает
  коды находок и завершается ненулевым кодом.

Политика операторских команд:

- `operator-command --text "/status"` возвращает счётчики очереди и число зависших задач.
- `operator-command --text "/queue"` перечисляет состояние очереди.
- `operator-command --text "/stale"` перечисляет зависшие задачи `running`.
- `operator-command --text "/retry <task-id>"` возвращает в очередь неудавшуюся задачу.
- `operator-command --text "/recover <task-id> [причина]"` возвращает в очередь подтверждённую
  прерванную задачу `running`.
- `operator-command --text "/continue"` подтверждает, что оператор хочет продолжения работы; чтобы
  действительно возобновить исполнение, запустите `queue-run-loop`.
- `operator-command --text "/stop"` записывает решение и оставляет состояние очереди без изменений.
- Каждая операторская команда дописывается в `operator-events.jsonl` под корнем состояния очереди.
- Обработчик команд не зависит от транспорта; опрос Telegram должен вызывать тот же обработчик после
  аутентификации отправителя.

Политика рантайма Python:

- Промышленные прогоны обязаны использовать `runtime/python/python.exe`, а не системный Python.
- Зависимости рантайма закреплены в `requirements.lock`.
- Колёса и зависимости обязаны быть вложены в `vendor/wheels`.
- Используйте
  `scripts/bootstrap-runtime.ps1 -PythonSource <путь-к-каталогу-python> -BuildWheelhouse -InstallOffline`,
  чтобы скопировать подготовленный рантайм, собрать колёса и установить из локального wheelhouse.
- Используйте `scripts/build-wheelhouse.ps1`, чтобы наполнить `vendor/wheels`.
- Используйте `scripts/install-offline.ps1`, чтобы установить из `vendor/wheels` в
  `runtime/site-packages`.
- Используйте `scripts/hoh.ps1` как лаунчер по дереву исходников во время разработки и проверки
  релиза.
- Используйте `scripts/package-windows-installer.ps1`, чтобы обновить и проверить встроенный рантайм
  и скомпилировать пользовательское приложение Windows Setup.
- Проверка рантайма запускает встроенный Python и требует хотя бы одного файла `.whl` в wheelhouse.
- Артефакты рантайма и колёс порождаются локально и исключены из git; артефакт для пользователя —
  `dist/HoH-Setup-<версия>.exe`.

Примеры лаунчера для разработки:

```powershell
.\scripts\hoh.ps1 doctor
.\scripts\hoh.ps1 scan
```

Поток сборки Windows Setup:

```powershell
.\scripts\bootstrap-runtime.ps1 `
  -PythonSource "C:\path\to\python" `
  -BuildWheelhouse `
  -InstallOffline

.\scripts\package-windows-installer.ps1
```

Политика уведомлений Telegram:

- Telegram отключён по умолчанию.
- Токены ботов, идентификаторы чатов и разрешённые идентификаторы пользователей никогда не хранятся
  прямо в конфигурации проекта.
- Конфигурация хранит только имена переменных окружения.
- Исходящие сообщения используют метод `sendMessage` Telegram Bot API.
- Входящие операторские команды используют `telegram-poll`: она один раз вызывает `getUpdates`,
  аутентифицирует и `chat_id`, и `from.id`, передаёт разрешённые текстовые команды детерминированному
  обработчику операторских команд, отвечает результатом и сохраняет смещение следующего обновления в
  состоянии очереди.
- Используйте `telegram-watch`, когда планировщику, обёртке службы или ручной операторской сессии
  нужен повторяющийся опрос. `--iterations 0` работает до прерывания; положительное значение
  `--iterations` даёт ограниченный прогон, удобный для планировщика.
- Входящие команды Telegram используют ограниченный опрос; аутентифицированные push-обратные вызовы
  A2A реализованы отдельно от Telegram.
- Чтобы проверить доставку после создания бота и начала переписки с ним:

```powershell
$env:HOH_TELEGRAM_BOT_TOKEN = "<токен-бота>"
$env:HOH_TELEGRAM_CHAT_ID = "<ваш-идентификатор-пользователя-или-чата>"
$env:HOH_TELEGRAM_USER_ID = "<ваш-идентификатор-пользователя-telegram>"
python -m llm_harness notify --config harness.toml --message "HoH Telegram check"
python -m llm_harness telegram-poll --config harness.toml --project-root C:\path\to\repo
python -m llm_harness telegram-watch --config harness.toml --project-root C:\path\to\repo --iterations 3
```

Обёртка ежедневных операций:

```powershell
.\scripts\hoh.ps1 init-project
. .\hoh-env.local.ps1

.\scripts\run-daily-ops.ps1 `
  -Config .\harness.toml `
  -FinalCheck "set PYTHONPATH=src&& python -m unittest discover -s tests" `
  -FinalCheck "python -m compileall -q src tests"
```

Обёртка планировщика заданий Windows:

```powershell
.\scripts\register-telegram-watch-task.ps1 `
  -Config .\harness.toml `
  -EveryMinutes 1 `
  -Iterations 1 `
  -RunNow

.\scripts\register-telegram-watch-task.ps1 -Unregister
```

Запланированное задание запускает установленный лаунчер `scripts\hoh.ps1` в текущей пользовательской
сессии и не хранит секретов Telegram. Задайте токен бота, идентификатор чата и разрешённый
идентификатор пользователя переменными окружения до регистрации или запуска задания.

Формальную архитектуру и правила поставки см. в [ARCHITECTURE.md](ARCHITECTURE.md) /
[ARCHITECTURE.ru.md](ARCHITECTURE.ru.md) и [docs/architecture.md](docs/architecture.md) /
[docs/architecture.ru.md](docs/architecture.ru.md). Операторский рунбук, включая настройку окружения,
опрос Telegram, исполнение очереди и передачу по финальному аудиту, — в
[docs/daily-ops.md](docs/daily-ops.md) / [docs/daily-ops.ru.md](docs/daily-ops.ru.md). API
аутентифицированного сервера и настройку пользовательской службы под Linux и macOS — в
[docs/headless-server.md](docs/headless-server.md) /
[docs/headless-server.ru.md](docs/headless-server.ru.md).

## Поддержать автора

<p align="center">
  <img src="donate-qr.png" alt="Donate QR" width="200"/>
</p>

BTC: bc1q3frrup5neh7nhfg944etu2agd4j9u0vg3jyee6

ETH(Arbitrum): 0x43B349d8Cea83215D707EBa3bc35e9917f746b0a

TRX: THSzvy49KNeqRjXsGkurh2A5G4avV4RgN4

XRP: rLWZjS3DMupC4ZdXCX3BVYn4dEtC3iNhgy

SOL: 3xwfybxJ6Tz5t6pjBBkL5yYQCZo6wfbv932UNA4ThdP8

ADA: addr1q926ys75jp5wn2pv32a3t8r8pdhr7w02v0t9j4a8pmg0ruww5rlkctu4lnz2hfcwa5qfn3zhsd0s23r22uqwzx9gu6cq5c4e76

TON: UQC4qlAOD9Nly4K_66GJ_yCsSM3x2sB0vZ2GrBQbc--gZUui

DOGE: DTjNYmbtymzcjUiV4MsZY8MP4dM7MJ6qLC

XMR: 44qRqM6YtnxXUhkgCFqDDrKMPjWriu69FLBoop8Kwp7e1VQsBUJoVQ8JYQjfMV5C6uidTUgSSyoJ65mq8aYG2esZ1rrqfwt
