# Agent Sandbox — архитектура

Цель: AI-агенты (Claude Code, Codex) не запускаются на хосте напрямую. Каждый работает
в Docker-контейнере с явно заданным уровнем прав. Весь их интернет идёт только через
VLESS-VPN с kill switch. **Секретов (токенов, ключей) внутри агентов нет вообще.**
Управление агентами идёт через herdr на хосте.

Статус: ✅ всё из §9 реализовано: агенты coder, infra, admin, web, assistant, cad, брокер (Claude и Codex по
подписке), шлюзы mailgw и drivegw, мониторинг, подключение хоста. `bin/sandbox test` проходит.
Сквозная проверка с реальным VLESS-ключом, логинами и Google ещё не делалась: см. REPORT.md.
Развёртывание на Windows/WSL: [WINDOWS.md](WINDOWS.md). Известные ограничения — в [LIMITATIONS.md](LIMITATIONS.md).

---

## 1. Общая схема

```
 Хост (Arch, docker, herdr)
 ┌────────────────────────────────────────────────────────────────────────────────┐
 │ herdr: панели  bin/agent coder | infra | admin | assistant                     │
 │ браузер: 127.0.0.1:9090/ui (соединения) · 127.0.0.1:3300 (Grafana)            │
 │                                                                                │
 │  sbx-coder      sbx-infra      sbx-admin        sbx-assistant                  │
 │  10.88.1.0/24   10.88.2.0/24   10.88.3.0/24     10.88.4.0/24                   │
 │  ┌─────────┐    ┌─────────┐    ┌─────────┐      ┌───────────┐  ┌────────┐      │
 │  │ coder   │    │ infra   │    │ admin   │      │ assistant │─▶│ mailgw │      │
 │  │ .11     │    │ .11     │    │ .11     │      │ .11       │  │ .4     │      │
 │  └─┬────┬──┘    └─┬────┬──┘    └─┬────┬──┘      └─────┬─────┘  └───┬────┘      │
 │    │    │LLM/git  │    │         │    │               │ только LLM │ Google API │
 │    │    ▼         │    ▼         │    ▼               ▼            ▼            │
 │    │  ┌──────────────────────────────────────────────────────────────┐         │
 │    │  │ keys (брокер секретов) .3 в каждой сети · держит ВСЕ токены  │         │
 │    │  └───────────────────────────────┬──────────────────────────────┘         │
 │    │ прочий интернет (npm, pip, docs) │ sbx-keys 10.88.9.0/24                  │
 │    ▼ HTTP(S)_PROXY = .2:7890          ▼                                        │
 │  ┌──────────────────────────────────────────────┐                              │
 │  │ vpn: sing-box + nftables kill switch          │  .2 в сетях coder/infra/     │
 │  │ inbounds in-coder/in-infra/in-admin/in-keys   │  admin/keys; в sbx-assistant │
 │  └───────────────────────┬──────────────────────┘  его НЕТ                    │
 │      sbx-egress 10.88.0.0/24 (единственная сеть с выходом) │ только VLESS_IP:PORT │
 └────────────────────────────────────────────────────────────┼───────────────────┘
                                                              ▼
                                                     VLESS-сервер → интернет
```

Три принципа:
1. **Нет маршрута — нет утечки.** Все сети агентов `internal: true`: без NAT и шлюза.
   Выход в интернет есть только у `vpn`, и только на IP:порт VLESS-сервера.
2. **Нет секретов — нечего красть.** Токены лежат только в брокере `keys`. Агент видит
   заглушки, а настоящий токен подставляется в запрос уже за пределами агента.
3. **Каждый тип агента в своей сети.** Агенты не видят друг друга. По входу sing-box
   (`in-coder`, …) в дашборде видно, кто сделал запрос.

---

## 2. VPN-контейнер (`vpn`) — ✅ реализован

Файлы: `vpn/Dockerfile`, `vpn/entrypoint.sh`, `vpn/gen-config.py`, `vpn/killswitch.nft.tmpl`.

**Ядро: sing-box 1.14.3** (VLESS + Reality / XTLS-Vision / WS / gRPC / HTTPUpgrade / H2).
Дашборд: **zashboard 3.29.1** через Clash API.

### Как стартует
1. `gen-config.py` (без привилегий, uid 1000) читает `secrets/vless.url`, резолвит
   сервер в IPv4, генерирует `config.json`: по одному входу `mixed` на каждую сеть
   агентов, единственный выход `vless`, DoH через туннель.
2. `entrypoint.sh` (root, только с `NET_ADMIN`) находит egress-интерфейс по
   default-маршруту и применяет kill switch `nftables`.
3. sing-box запускается от uid 1000 (`su-exec`).

### Kill switch: три уровня
| Уровень | Механизм | Что защищает |
|---|---|---|
| 1. Сеть | сети агентов `internal: true` | У агентов нет маршрута наружу вообще, даже если игнорировать прокси |
| 2. Маршрутизация | в sing-box нет `direct`-outbound, `route.final = proxy` | Если туннель упал, соединения рвутся, а не идут напрямую |
| 3. Firewall | nft `output` в netns vpn: только `SERVER_IP:PORT/tcp` через egress; `input` от агентов: только `:7890`; `forward`: drop | Даже ошибка в конфиге sing-box ничего, кроме VLESS-сервера, не выпустит |

После старта даже встроенный DNS Docker не может ходить наружу из netns vpn,
поэтому утечек DNS нет. DNS работает только через DoH внутри туннеля.

### Права контейнера
`read_only`, `cap_drop: ALL` + `NET_ADMIN, SETUID, SETGID`, `no-new-privileges`,
IPv6 и `ip_forward` выключены, лимиты памяти и процессов, секреты через Docker secrets.

---

## 3. Брокер секретов (`keys`)

Задача: агент, захваченный prompt injection, **не может украсть** ни токен Claude/Codex,
ни GitHub-токен, ни API-ключи. Их просто нет в его контейнере.

### Как работает (реализовано: mitmproxy, `keys/inject.py`)
```
agent (HTTPS_PROXY=http://10.88.N.3:8080, в env только заглушки, CA брокера в trust store)
  └▶ keys: отдельный mitmdump на каждого агента. Расшифровывает ТОЛЬКО хосты из policy.json
     (api.anthropic.com, github.com, chatgpt.com, auth.openai.com, …), подменяет заглушку
     на ЕГО токен в заголовке Authorization / x-api-key. Остальной трафик идёт TLS-туннелем без расшифровки
        └▶ upstream: вход sing-box этого же агента (10.88.N.2:7890) → VLESS
```
- Заглушки в агенте:
  - Claude Code: `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-sbx-placeholder`;
  - git: credential helper отдаёт пароль `ghp_sbxplaceholder…`, брокер подменяет его внутри Basic-auth;
  - `gh`: `GH_TOKEN` с той же заглушкой;
  - Codex (подписка ChatGPT): в `~/.codex/auth.json` агента лежат *производные подделки*.
    У JWT сохранены заголовок и claims (CLI читает план и аккаунт локально), а подпись
    заменена. Брокер маппит подделку → настоящий токен. На `auth.openai.com/oauth/token` он
    подставляет настоящий refresh-токен, новые токены из ответа сохраняет у себя, а агенту
    возвращает новые подделки (`keys/codexauth.py`).
- Заглушка **никогда** не подменяется в URL или теле запроса (единственное исключение — тело
  запроса обновления токенов Codex, ответ на который переписывается обратно в подделки).
  Иначе агент мог бы попросить брокер записать токен, например, в gist и прочитать его оттуда.
- Хранилище: `secrets/keys/<agent>/<service>` (`chmod 600`), монтируется **только** в `keys`
  (rw: брокер сохраняет обновлённые токены Codex). `docker inspect` у infra их не покажет.
- **Свой набор токенов на каждый тип агента** (`keys/policy.json`): coder и infra — anthropic,
  github, codex; admin и web — только anthropic; cad — anthropic, codex.
- У assistant в сети нет vpn, поэтому его брокер ходит наружу через отдельную сеть `sbx-keys`, где sing-box
  пропускает **только** `api.anthropic.com`.
- Логин (одноразовые контейнеры без монтирований, только через VPN):
  `bin/sandbox login <agent> claude` (`claude setup-token`) и `bin/sandbox login <agent> codex`
  (`codex login --device-auth`). Токены сразу уходят в брокер.

Что остаётся: пока агент работает, он может **пользоваться** доступом через брокер.
Унести токен он не может. Поэтому нужны минимальные права у токенов (fine-grained PAT).

⚠️ С настоящими аккаунтами ещё не проверено (REPORT.md). Логика подмены покрыта
`tests/broker.sh`.

---

## 4. Типы агентов

Общая база: один образ `agent-base` (Debian trixie + Node 24 + `claude` + `codex` + git, `gh`, python3/pip,
Go, Rust/cargo, Lua 5.4, cmake, build-essential, sqlite3, ripgrep, fd, jq, docker CLI). Агенты различаются compose-сервисом.
Общее для всех: своя `internal`-сеть, `HTTP(S)_PROXY=http://10.88.N.2:7890` (кроме assistant),
LLM и git через брокер, **ноль секретов в контейнере**, `init: true`, без IPv6.
В volume `<agent>_home` лежат только история и настройки, токенов там нет.

### Тип 1 — `agent-coder` (кодинг и пет-проекты)
| Параметр | Значение |
|---|---|
| Пользователь | `1000:1000` (как ты, чтобы файлы на хосте были твоими) |
| Файловая система | `read_only` rootfs; `tmpfs: /tmp`; rw только `~/Projects` (тот же путь) и `$HOME` агента (volume) |
| Capabilities | `cap_drop: ALL`, `no-new-privileges` |
| Docker | нет |
| Интернет | через VPN (`in-coder`) |
| Лимиты | `mem_limit`, `pids_limit`, `cpus` |

Пакеты ставятся в `$HOME` агента или в проект. `.env` внутри `~/Projects` агенту видны,
поэтому ключи проектов лучше перенести в брокер (`/svc/<name>`).

### Тип 2 — `agent-infra` (настройка этой контейнерной среды)
| Параметр | Значение |
|---|---|
| Пользователь | `1000:1000` |
| Файловая система | rw: `~/agent-sandbox`; `secrets/` перекрыт пустым tmpfs |
| Docker | **docker-socket-proxy только GET** (list/inspect/logs). В socket-proxy v0.5.0 `ALLOW_RESTARTS` работает только вместе с `POST=1`, а это открыло бы `containers/create`. Поэтому перезапуски идут только через твой `bin/sandbox apply` |
| Сборка образов | только через `bin/sandbox apply` (запускаешь ты), buildx-builder в сети через VPN, так что `RUN` в Dockerfile тоже идёт через прокси. Нужен пакет `docker-buildx` (сейчас не установлен) |
| Применение изменений | агент правит файлы → коммит → **ты** смотришь `git diff` и запускаешь `bin/sandbox apply` |

### Тип 3 — `agent-admin` (сисадмин-наблюдатель: смотрит и предлагает)
| Параметр | Значение |
|---|---|
| Пользователь | uid `1500` (не твой): `~` (права `700`) и твои процессы ему недоступны |
| Группы | `systemd-journal`, `adm` хоста: чтобы читать журнал и логи |
| Файловая система | `/` хоста → `/host` **read-only**; rootfs `read_only`; `tmpfs: /tmp` |
| Запись | только `~/agent-sandbox/proposals/` → `/proposals` |
| Права | `cap_drop: ALL`, `no-new-privileges`, без sudo и setuid-бинарей |
| Процессы | `pid: host`: видит, но не трогает |
| Docker | socket-proxy **только GET** |

Агент пишет предложение в чат или в `/proposals/NNN-*.sh`, ты читаешь и выполняешь сам.

### Тип 4 — `agent-assistant` (почта и календарь) — максимально ограничен
Каждое входящее письмо пишет посторонний человек, поэтому **каждое письмо — потенциальный
prompt injection**. Значит, защита строится так: даже полностью захваченный агент
не может ни вынести данные, ни сделать что-то необратимое.

**Изоляция (жёсткие слои, от модели не зависят):**
| Слой | Как |
|---|---|
| Сеть | `sbx-assistant`: в сети только assistant, брокер (только LLM) и `mailgw`. **vpn в этой сети нет**: интернета нет совсем, ни веба, ни картинок, ни ссылок |
| Секреты | никаких. OAuth-токен Google лежит в брокере, `mailgw` ходит через него |
| Файлы | никаких монтирований, кроме `$HOME`-volume; rootfs read-only |
| Инструменты | только MCP-инструменты `mailgw`. Bash, Write, Edit, WebFetch, WebSearch запрещены в `managed-settings.json` (read-only, агент не может это изменить) |

**`mailgw`: узкий шлюз почты и календаря.** Агент работает только через него, в Gmail
напрямую не ходит:
| Действие | Режим |
|---|---|
| поиск и чтение писем | ✅ сразу. Текст без HTML, ссылки показываются, но не открываются, вложения только как метаданные |
| ярлыки, архив, «прочитано» | ✅ сразу (обратимо). **Удаление — нельзя** |
| черновик ответа | ✅ сразу. Черновик никуда не уходит, ты видишь его в почте |
| **отправка письма** | ⏸ только через очередь одобрения: `bin/sandbox approvals` → `show <id>` (получатели и полный текст) → `approve`/`deny`. Это CLI, а не веб-страница: Docker не публикует порты из internal-сетей, а давать mailgw обычную сеть значит выпустить его мимо VPN |
| событие в календаре без участников | ✅ сразу |
| событие с участниками, изменение или отмена встреч с другими людьми | ⏸ одобрение (приглашения отправляют письма) |
| фильтры, пересылка, делегирование, настройки, экспорт контактов | ❌ инструментов нет вообще: это главные каналы утечки |
| массовые операции | лимит (например ≤ 50 писем в минуту на чтение, ≤ 10 изменений в минуту) |

Ещё два слоя:
- **Scope токена Google минимальный**: `gmail.modify` (без `mail.google.com/`)
  + `calendar.events`. Отправка технически входит в scope, поэтому её блокирует `mailgw`,
  а не Google: у агента инструмента отправки нет, есть только «запросить отправку».
- Опционально: **режим карантина** (паттерн dual-LLM). Письма сначала читает изолированная
  модель без инструментов и возвращает структурированную сводку. Агент с инструментами
  видит сводку, а не сырой текст. Это сильнее защищает от инъекций, но ценой точности.

Все вызовы `mailgw` логируются: кто, что, когда, какое письмо. Лог доступен в Grafana.

### Тип 5 — `agent-web` (браузинг и ресёрч) — ✅ реализован
Исходим из того, что он **поймает prompt injection** на какой-нибудь странице. Поэтому у него
почти ничего нет:
| Слой | Как |
|---|---|
| Файлы | никаких твоих. Пишет только в `~/agent-sandbox/web-outbox/` (отчёты, **недоверенные**) |
| Персистентность | **новый контейнер на каждую сессию** (`docker compose run --rm`), `$HOME` в tmpfs: инъекция не переживёт выход |
| Секреты | только заглушка LLM-токена. Свой токен в брокере (`login web claude`), отзывается отдельно |
| Инструменты | `managed-settings.json` (read-only): WebFetch, WebSearch, Read, Write только в `/outbox`; Bash и Edit запрещены, bypass-режим выключен |
| Сеть | своя `sbx-web`, интернет через VPN, **только порты 80/443** (правило sing-box для `in-web`), локальной сети нет |

Что остаётся: всё, что ты пишешь ему в промпт, может утечь на страницу злоумышленника.
Не давай ему секретов и личных данных. Отчёты из `web-outbox` читай сам, прежде чем
отдавать другим агентам, иначе инъекция поедет дальше.

### Сводка
| | coder | infra | admin | assistant | web |
|---|---|---|---|---|---|
| Видит | `~/Projects` | `~/agent-sandbox` | хост read-only | почту и календарь через `mailgw` | ничего |
| Пишет | `~/Projects` | `~/agent-sandbox` (без secrets) | `proposals/` | черновики, ярлыки, свои события | `web-outbox/` |
| Docker | ✗ | ограниченный proxy | только чтение | ✗ | ✗ |
| Root / sudo | ✗ | ✗ | ✗ | ✗ | ✗ |
| Интернет | VPN | VPN | VPN | **нет** (только LLM через брокер) | VPN, только 80/443 |
| Секреты внутри | ✗ | ✗ | ✗ | ✗ | ✗ |
| Живёт | постоянно | по требованию | по требованию | по требованию | одна сессия |

`agent-cad`: видит только `~/3d-models`, интернет VPN 80/443, внешнее действие — загрузка на Drive через `drivegw`.

---

### Тип 6 — `agent-cad` (3D-модели: FreeCAD / Blender) — ✅ реализован
| Слой | Как |
|---|---|
| Файлы | только `~/3d-models` → `/models` (rw) |
| Инструменты | `freecadcmd` (FreeCAD 1.0) и `blender -b` (Blender 4.3) headless, Python, Claude/Codex |
| Сеть | своя `sbx-cad`, интернет через VPN, только 80/443 |
| Google Drive | через шлюз **`drivegw`** (MCP: `drive_upload`, `drive_list`). Токен со scope **`drive.file`**: видны только файлы, созданные самим шлюзом, остальной диск нет. Загрузка только из `/models`, только в папку «Agent Sandbox — 3D models» (+ один уровень подпапок), только модели, картинки и документы, ≤ 200 МБ, ≤ 30 в час. Удаления и скачивания нет |
| Секреты | ноль. Google-токен лежит в `drivegw`, LLM-токен в брокере |

## 5. Взаимодействие через herdr

herdr остаётся **на хосте** (claude/codex с хоста удаляются). Контейнеры работают
в фоне, а агент запускается в панели:

```sh
bin/agent coder            # = HERDR_AGENT=claude docker exec -it -w "$PWD" sbx-coder claude
bin/agent coder codex      # = HERDR_AGENT=codex  docker exec -it ... sbx-coder codex
bin/agent infra | admin | assistant
bin/agent shell coder      # обычный shell внутри
```
- `HERDR_AGENT=<agent>` нужен, потому что в панели виден процесс `docker`. Статус herdr берёт с экрана.
- `~/Projects` внутри по тому же пути, поэтому `-w "$PWD"` открывает агента в нужной папке.
- Сокет herdr в контейнеры **не монтируется**: через него можно управлять всеми панелями.
- Удобно завести workspace herdr на каждый тип агента.

---

## 6. Хост через этот же VPN + мониторинг

### Подключение хоста (без kill switch)
- **Прокси:** вход `127.0.0.1:7891`, браузер через PAC `PROXY 127.0.0.1:7891; DIRECT`.
- **Весь компьютер:** WireGuard-вход в sing-box (`10.99.0.0/24`) + профиль NetworkManager.
  Исключения против петли: `VLESS_IP`, сети Docker, LAN.
  Watcher `sandbox-host-watch` (systemd user unit) при сбое VPN выполняет `nmcli con down`
  (хост идёт напрямую), а после восстановления `up`.
- На агентов это не влияет: у них прямого маршрута нет.

### Кто видит что
| | sing-box (`vpn`) | OpenSnitch (хост) | брокер `keys` | `mailgw` |
|---|---|---|---|---|
| Чей трафик | агенты + хост через VPN | процессы хоста, всегда | API-вызовы агентов | действия assistant |
| Что знает | домен, вход (агент/хост), объём | **программа**, домен, IP | агент, сервис, путь, метод | инструмент, письмо или событие, одобрения |

OpenSnitch работает в режиме мониторинга: `DefaultAction: allow`, без всплывающих окон.
Правила лежат в `host/opensnitch/` в git.

### Grafana (профиль `monitor`)
```
OpenSnitch ─┐
sing-box ───┤
keys ───────┼──▶ Vector ──▶ Loki ──▶ Grafana  http://127.0.0.1:3300
mailgw ─────┤
host-watch ─┘
```
Дашборд «Запросы»: хост по программам, каждый агент по доменам, режим VPN/direct
во времени, поиск по домену по всем источникам. zashboard остаётся для живого просмотра.

---

## 7. Структура репозитория

```
agent-sandbox/
├── ARCHITECTURE.md LIMITATIONS.md REPORT.md WINDOWS.md
├── compose.yaml              # все сервисы; агенты и мониторинг в профилях
├── .env                      # пути и gid хоста (bin/sandbox init обновляет gid)
├── secrets/                  # НЕ в git, chmod 600: vless.url, dashboard_secret,
│                             #   grafana_admin_password, host-wg.json, google-client.json,
│                             #   keys/<agent>/{anthropic,github,codex-auth.json,google*.json}, keys/ca
├── vpn/                      # sing-box + nftables kill switch + zashboard
├── keys/                     # брокер секретов (mitmproxy addon, codexauth, policy.json)
├── mailgw/                   # шлюзы: mailgw (Gmail/Calendar) и drivegw (Drive), MCP
├── agents/base|admin|web|assistant|cad/
├── monitor/                  # vector, loki, grafana (+ дашборд «Запросы»)
├── host/                     # proxy.pac, nm/ (WG-профиль), systemd/ (watcher), opensnitch/
├── proposals/                # предложения agent-admin
├── web-outbox/               # отчёты agent-web (недоверенные)
├── bin/sandbox bin/agent bin/host-watch bin/versions.py
└── tests/                    # killswitch.sh, routing.sh, broker.sh, mailgw.sh (+ *_test.py)
```

---

## 8. Проверки (`bin/sandbox test`)
- **killswitch.sh**: для каждой сети агентов нет прямого HTTPS и DNS, API дашборда недоступен,
  egress-сеть недоступна. netns vpn режет прямой трафик. По каждому запущенному агенту:
  нет реальных токенов, правильные монтирования (secrets скрыты, хост read-only, `~/.ssh` не виден,
  нет setuid), docker только на чтение. assistant без интернета, cad видит только `/models`.
- **routing.sh**: решения sing-box по каждому входу: web и cad только 80/443, mailgw только
  googleapis.com, сеть брокера только api.anthropic.com, остальные агенты без ограничений.
- **broker.sh**: подмена токенов Claude и Codex (включая refresh), заглушки в теле запроса и URL не трогаются.
- **mailgw.sh**: политика почты, календаря и Drive (одобрения, запрещённые ярлыки, path traversal,
  лимиты, набор MCP-инструментов).

---

## 9. План внедрения
1. ✅ VPN + kill switch + дашборд.
2. ✅ Брокер `keys` + `bin/sandbox login` (Claude setup-token, Codex device-auth, GitHub PAT).
3. ✅ agent-coder + `bin/agent` для herdr.
4. ✅ agent-infra + socket-proxy (GET) + `bin/sandbox apply`.
5. ✅ agent-admin (read-only хост + `proposals/`).
6. ⏳ Удаление claude/codex с хоста: делаешь ты после проверки логинов (REPORT.md).
7. ✅ agent-assistant + mailgw + очередь одобрения (CLI).
8. ✅ Хост через VPN (PAC + WG/NM + watcher): файлы готовы, ставишь ты (REPORT.md).
9. ✅ Мониторинг (Vector/Loki/Grafana). OpenSnitch: установщик готов, ставишь ты.
10. ✅ agent-web, ✅ agent-cad + drivegw.
11. *(Позже)* ограничения: sing-box по агентам, OpenSnitch по программам хоста.

## 10. Открытые вопросы
- Нужен ли агентам доступ к локальной сети (NAS, роутер)? Сейчас закрыт.
- Ограничивать ли домены для coder/infra (allowlist в sing-box)? Механизм уже есть (`DOMAIN_ALLOW`).
