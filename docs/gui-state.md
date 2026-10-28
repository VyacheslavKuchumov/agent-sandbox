# Electron GUI — состояние разработки

Цель: desktop-панель управления существующей Docker-песочницей. Первая версия:
состояния агентов и инфраструктуры, запуск/остановка/перезапуск, ограниченные логи,
запуск CLI агента в отдельном терминале, проверки туннеля/изоляции, названия
настроенных ключей без значений, ссылки на локальные dashboards. Русский UI.

Ветка: `feat/electron-control-center`. Изменения идут через PR для пользователя;
merge и изменение GitHub-защиты не разрешены. GitHub-доступ сейчас от владельца:
его PR нельзя одобрить тем же аккаунтом. Чужой WIP:
`proposals/vpn-split-routing-adguard-plan.md` — не включать в коммит.

Владение: root (`w1:p1`) — интеграция всех файлов и Git. Исполнитель `sandbox-ui`
(`w1:p5`) завершил renderer, проверил syntax и явно освободил владение. Агент
свободен; активных сборок/пакетов у него нет. Общая feature-ветка использовалась
с непересекающимися файлами; git switch/commit/push выполняет только root.

Контракт renderer:
`window.sandbox.snapshot()` → `{root,demo,updatedAt,error,agents,infrastructure,keys}`.
Agent: `{id,name,description,tools,state,health,container}`; infrastructure:
`{id,name,state,health,container}`. state: running/stopped/unavailable.
keys: `[{agent,services:["anthropic","github",...]}]` — только названия.
`run({action,target})`, action start/stop/restart, target ID агента (кроме web)
или vpn/keys; action tunnel/test без target.
`logs(target)` → `{ok,stdout,stderr,code}`, target ID или vpn/keys/mailgw/drivegw.
`launch({agent,tool})` — bin/agent в отдельном терминале.
`openDashboard("grafana"|"network")` — фиксированные localhost URL.
run/launch/openDashboard → `{ok,stdout,stderr,code}`.
Все системные подтверждения выполняет main через native dialog.

Демо: `npm run demo` / `--demo`, не выполняет Docker и команды управления.
Node integration выключен, context isolation и sandbox включены; CSP и IPC
проверки. Renderer не получает произвольный shell, пути или содержимое секретов.

Проверки root: backend allowlists/command runner failures/timeouts/demo, syntax,
Electron запуск и renderer smoke при доступном окружении. UI-исполнитель
проверяет только собственный JS syntax и передаёт файлы/ограничения.

Результат: `desktop/` — запускаемое Electron 45.1.0 приложение с lockfile,
controller, preload, русским renderer, README и тестами. Добавлены `bin/sandbox gui`
и `gui --demo`, ссылка в USAGE и правила feature/PR в AGENTS.md.

Проверено root: 13/13 controller-тестов (включая реальный child_process, тайм-ауты,
лимит вывода, UTF-8, allowlists и деморежим без Docker); JS syntax; shell syntax;
git diff whitespace. Electron smoke PASS через Xvfb без --no-sandbox: preload/IPC,
отсутствие Node API в renderer, отказ недопустимой операции, навигация логов/ключей,
демо stop/start и окно 900×700 без горизонтального overflow. Скриншоты:
`desktop/artifacts/demo.png`, `demo-compact.png` (не в Git).

Настоящее read-only чтение Docker PASS: контейнеров sbx сейчас нет, все остановлены.
Реальные start/stop/restart, логины, терминалы CLI и проверки сети не выполнялись
ради QA. Открытие terminal client означает отправку запроса, а не успех CLI.

Ограничения v1: Linux + существующий host CLI; без установочного пакета, встроенного
терминала, управления секретами и почтовыми одобрениями. GitHub-права/защита
не изменены. Ревью пользователем и независимая GitHub-идентичность — отдельный шаг.

Текущий пакет: проверен, подготовка commit/PR без merge. Следующий шаг: PR → ревью
пользователя. Usage недоступен, delta не измерена.
