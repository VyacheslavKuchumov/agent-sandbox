# Отчёт: что сделано, какие решения принял, что осталось тебе

Дата: 2026-10-24. Всё запушено в приватный репозиторий
https://github.com/VyacheslavKuchumov/agent-sandbox. Секреты в git не попадают
(`secrets/` в `.gitignore`, перед каждым коммитом проверял `git status` и искал токены в диффе).

## 1. Итог

| Компонент | Статус | Проверено |
|---|---|---|
| `vpn`: sing-box 1.14.3 + nftables kill switch + zashboard | ✅ | `tests/killswitch.sh` (с фейковым ключом) |
| Изоляция сетей: своя internal-сеть на каждый тип агента | ✅ | killswitch.sh |
| Правила sing-box по агентам (web/cad только 80/443, mailgw только googleapis, брокер assistant только anthropic) | ✅ | `tests/routing.sh` |
| Брокер `keys`: Claude (setup-token), GitHub PAT, **Codex по подписке ChatGPT** | ✅ | `tests/broker.sh` (офлайн) |
| agent-coder | ✅ | killswitch.sh |
| agent-infra (+ socket-proxy GET, `bin/sandbox apply`) | ✅ | killswitch.sh |
| agent-admin (uid 1500, хост read-only, `proposals/`) | ✅ | killswitch.sh |
| agent-web (одноразовый, без Bash, 80/443) | ✅ | killswitch.sh + routing.sh |
| agent-assistant + `mailgw` (Gmail/Calendar MCP, очередь одобрений) | ✅ | `tests/mailgw.sh` (фейковый Google) |
| agent-cad (FreeCAD 1.0, Blender 4.3) + `drivegw` (Google Drive) | ✅ | killswitch.sh + mailgw.sh |
| Мониторинг: Vector + Loki + Grafana, дашборд «Запросы» | ✅ | данные coder видны в Loki |
| Хост через VPN: прокси + PAC, WireGuard + профиль NM, watcher | ✅ файлы | WG-handshake проверен тестовым клиентом |
| OpenSnitch: конфиг и установщик | ✅ файлы | **не проверено** (на хосте не установлен) |
| Хелперы `bin/sandbox`, `bin/agent`, `bin/sandbox update` | ✅ | вручную |
| `WINDOWS.md` (инструкция для ИИ-агента: WSL2 без Docker Desktop) | ✅ | — |

Последний прогон `bin/sandbox test` — см. §6.

## 2. Решения, которые я принял сам

1. **Брокер — прозрачный HTTPS-прокси на mitmproxy, а не подмена `BASE_URL`.** Codex по подписке
   ходит на `chatgpt.com` и сам обновляет токены, через base URL это не решить. Брокер
   расшифровывает **только** хосты из `keys/policy.json`, остальной трафик идёт TLS-туннелем без расшифровки.
   В агентах доверенным добавлен CA брокера (только публичный сертификат).
2. **Токен подставляется только в заголовок `Authorization`/`x-api-key`**, никогда в URL или тело
   запроса. Иначе агент мог бы попросить брокер записать токен, скажем, в gist и прочитать его оттуда.
   Исключение одно: тело запроса обновления токенов Codex, ответ на который переписывается обратно в подделки.
3. **Codex: «производные подделки» токенов.** У JWT сохранены claims (CLI читает план и аккаунт
   локально), подпись заменена на `sbxfake…`. Настоящий `auth.json` лежит только у брокера, который
   сохраняет обновлённые токены (поэтому `secrets/keys` смонтирован в брокер на запись).
4. **infra не может перезапускать контейнеры.** В docker-socket-proxy v0.5.0 `ALLOW_RESTARTS`
   работает только вместе с `POST=1`, а это открывает и `containers/create`, то есть root на хосте.
   Поэтому docker у infra только на чтение, перезапуски идут через `bin/sandbox apply`.
5. **admin добавлен в группу твоего пользователя**, иначе он не может писать в `proposals/`
   (папка `2775`). Побочный эффект описан в LIMITATIONS 3.8a.
6. **Google-токены хранят шлюзы `mailgw`/`drivegw`, а не брокер.** Это детерминированный код без LLM.
   Так проще: refresh, Drive upload. Модель токенов не видит. Выход шлюзов в сеть ограничен `googleapis.com`.
7. **Одобрения почты через CLI** (`bin/sandbox approvals`), а не веб-страницей на `127.0.0.1:8090`,
   как было в плане. Docker не публикует порты из internal-сетей, а обычная сеть выпустила бы mailgw мимо VPN.
8. **У assistant совсем нет интернета.** В его сети нет vpn. LLM доступна через брокер, чей upstream
   (сеть `sbx-keys`) пропускает только `api.anthropic.com`. Инструменты Claude Code: только `mcp__mail`
   (managed-settings, read-only).
9. **Drive: scope `drive.file`.** Шлюз видит только свои файлы. Загрузка только из `~/3d-models`,
   только в папку «Agent Sandbox — 3D models» (и один уровень подпапок), только файлы моделей,
   картинок и документов, ≤ 200 МБ.
10. **mcp 1.30.0, а не 2.3.0.** В 2.x API `FastMCP` переименован и переделан. Взял проверенный 1.x.
11. **Grafana на порту 3300**: 3000 у тебя занят (видимо, проектом sima).
12. **Сетевые настройки хоста я не применял** (NM-профиль, OpenSnitch, systemd-юнит): пока тебя нет,
    это могло оборвать связь, и твой текущий `tun0` будет конфликтовать с полным туннелем.
    Всё подготовлено до одной команды (§4).
13. **Версии:** везде зафиксированы стабильные релизы старше 14 дней. `bin/sandbox update` поддерживает это правило.

## 3. Баги, которые нашёл и исправил

- **Правило «web только 80/443» резало трафик всех агентов.** `invert` в sing-box инвертирует
  всё правило целиком, включая условие на вход. Исправил на логическое AND
  (`vpn/gen-config.py: only_for`). С фейковым ключом это было незаметно, с настоящим остальные
  агенты остались бы без интернета. Добавил `tests/routing.sh`, чтобы это не вернулось.
- Тест «нет токенов» срабатывал на нашу же заглушку; тест «assistant не видит другие сети»
  ошибался из-за ответа 502 от брокера. Оба теста исправлены.
- `pacman-host` в admin падал на mirrorlist, сделал минимальный конфиг.

## 4. Твои шаги (по порядку)

```sh
cd ~/agent-sandbox

# 1. Ключ VPN: после этого тест должен показать IP выхода VPN ≠ твой
bin/sandbox vless

# 2. Логины (одноразовые контейнеры, только через VPN; токены уходят в брокер)
bin/sandbox login coder claude
bin/sandbox login coder codex
bin/sandbox login infra claude        # и/или codex
bin/sandbox login admin claude
bin/sandbox login web claude
bin/sandbox login assistant claude
bin/sandbox login cad claude          # и/или codex
bin/sandbox keys                      # таблица: что настроено

# 3. GitHub для coder/infra: fine-grained PAT (Settings → Developer settings →
#    Fine-grained tokens), coder — только нужные репозитории, infra — только agent-sandbox
bin/sandbox key coder github
bin/sandbox key infra github

# 4. Запуск агентов в herdr
bin/agent coder            # или: bin/agent coder codex, bin/agent infra, bin/agent admin,
bin/agent web              # bin/agent assistant, bin/agent cad
```

**Google (почта, календарь, Drive):**
1. https://console.cloud.google.com → создать проект → APIs & Services → включить **Gmail API**,
   **Google Calendar API**, **Google Drive API**.
2. OAuth consent screen → External → добавь себя в Test users.
3. Credentials → Create credentials → OAuth client ID → **Desktop app** → скачай JSON →
   положи как `secrets/google-client.json` (`chmod 600`).
4. ```sh
   bin/sandbox login assistant google   # Gmail + Calendar (для mailgw)
   bin/sandbox login cad google         # Drive (для drivegw)
   bin/sandbox up assistant cad
   bin/sandbox approvals                # очередь того, что assistant хочет отправить
   ```
   После согласия браузер откроет `http://localhost:8085/?code=…` с ошибкой. Скопируй этот URL в терминал.

**Мониторинг:** `bin/sandbox up monitor`, затем `bin/sandbox grafana` (URL и пароль) и
`bin/sandbox dashboard` (живые соединения).

**Хост через VPN** (когда отключишь свой текущий VPN `tun0`):
```sh
bin/sandbox host-vpn install          # ключи уже сгенерированы, профиль: secrets/host/agent-vpn.nmconnection
sudo install -m 600 -o root -g root secrets/host/agent-vpn.nmconnection /etc/NetworkManager/system-connections/
sudo nmcli connection reload
bin/sandbox host-vpn watch-install    # watcher: при падении туннеля хост идёт напрямую
bin/sandbox host-vpn on
bin/sandbox host-vpn status
```
Только браузер: PAC-файл `file:///home/kuchumov/agent-sandbox/host/proxy.pac`.

**OpenSnitch:** `sudo sh host/opensnitch/install.sh` (ставит пакет, режим мониторинга, логи в Grafana).

**buildx** (чтобы сборки шли через VPN, см. LIMITATIONS 5.4b): `sudo pacman -S docker-buildx`.

**Инструменты агентов:** в базовом образе есть git, `gh`, node и npm, python3 и pip, Go, Rust (cargo),
Lua 5.4, cmake, build-essential, sqlite3, ripgrep, fd, jq. Не хватает чего-то — попроси `bin/agent infra`
добавить это в `agents/base/Dockerfile`, проверь `git diff` и запусти `bin/sandbox apply`.
admin и cad собраны на базовом образе, поэтому `apply`/`rebuild` пересобирает и их (cad ~5 ГБ, это долго).

**Когда всё заработает:** удали claude/codex с хоста (`npm rm -g @anthropic-ai/claude-code @openai/codex`
или как ставил). Изменения в среде дальше делает `bin/agent infra`, применяешь их через `bin/sandbox apply`.

## 5. Что не проверено (нужны настоящие ключи и аккаунты)

- Реальный туннель VLESS и IP выхода. Твой ключ должен быть tcp + reality (поддерживается).
- **Подписка Claude через брокер.** Должно работать: Claude Code шлёт `Authorization: Bearer <oauth>`,
  брокер подменяет значение. Если что-то сломается, смотри `bin/sandbox logs keys`
  (`injected: false` или 401). Запасной вариант: API-ключ в `secrets/keys/<agent>/anthropic`
  и `ANTHROPIC_API_KEY` вместо `CLAUDE_CODE_OAUTH_TOKEN` в compose (брокер подменяет и `x-api-key`).
- **Codex через брокер.** Логика покрыта офлайн-тестами, но реальный протокол (websocket,
  точные поля ответа обновления токенов) не проверен. Симптом проблемы: Codex просит войти заново.
  Диагностика: `bin/sandbox logs keys` (запросы к `chatgpt.com`/`auth.openai.com`).
- Gmail, Calendar, Drive вживую (политика проверена на фейковом API).
- Схема конфига OpenSnitch (`Loggers`, формат `rfc5424`) взята по памяти. После установки проверь,
  что в Grafana появляются события `{source="host", app=~"opensnitch.*"}`.
- Полный туннель хоста через NM: проверены только WG-handshake и маршрутизация в sing-box.
  Сам профиль NM и исключения от петли на живом хосте не применялись.

## 6. Тесты

Последний прогон `bin/sandbox test`:

```
[coder] network sbx-coder, proxy 10.88.1.2:7890
  · via proxy: no answer (tunnel down → requests blocked, fail-closed)
  ✓ direct HTTPS blocked (no route)
  ✓ direct DNS blocked
  ✓ dashboard API not reachable from agent
  ✓ egress network not reachable
[infra] network sbx-infra, proxy 10.88.2.2:7890
  · via proxy: no answer (tunnel down → requests blocked, fail-closed)
  ✓ direct HTTPS blocked (no route)
  ✓ direct DNS blocked
  ✓ dashboard API not reachable from agent
  ✓ egress network not reachable
[admin] network sbx-admin, proxy 10.88.3.2:7890
  · via proxy: no answer (tunnel down → requests blocked, fail-closed)
  ✓ direct HTTPS blocked (no route)
  ✓ direct DNS blocked
  ✓ dashboard API not reachable from agent
  ✓ egress network not reachable
[web] network sbx-web, proxy 10.88.5.2:7890
  · via proxy: no answer (tunnel down → requests blocked, fail-closed)
  ✓ direct HTTPS blocked (no route)
  ✓ direct DNS blocked
  ✓ dashboard API not reachable from agent
  ✓ egress network not reachable
[cad] network sbx-cad, proxy 10.88.8.2:7890
  · via proxy: no answer (tunnel down → requests blocked, fail-closed)
  ✓ direct HTTPS blocked (no route)
  ✓ direct DNS blocked
  ✓ dashboard API not reachable from agent
  ✓ egress network not reachable
[vpn] container netns
  ✓ direct traffic from vpn netns dropped (only VLESS server allowed)
  ✓ nft table sbx_killswitch present
[host] dashboard
  ✓ http://127.0.0.1:9090/ui/ → 200
  ✓ API without secret → 401
[coder] agent container
  ✓ no real tokens inside coder
  ✓ host ~/.ssh not visible
  ✓ rootfs read-only
  ✓ coder: direct route blocked
[infra] agent container
  ✓ no real tokens inside infra
  ✓ secrets/ hidden
  ✓ docker ps via socket-proxy
  ✓ docker run denied
  ✓ docker exec denied
  ✓ infra: direct route blocked
[admin] agent container
  ✓ no real tokens inside admin
  ✓ reads host /etc
  ✓ host fs read-only (incl. submounts)
  ✓ host home not readable
  ✓ root-only files not readable
  ✓ cannot signal host processes
  ✓ raw docker.sock denied
  ✓ docker read-only
  ✓ no setuid binaries
  ✓ admin: direct route blocked
[assistant] agent container
  ✓ no real tokens inside assistant
  ✓ assistant: direct route blocked
  ✓ assistant: no web (broker upstream allows only api.anthropic.com)
  ✓ assistant: other networks unreachable
  ✓ assistant: managed MCP config present
[mailgw] gateway container
  ✓ mailgw: Google creds mount present
  ✓ assistant: Google creds not mounted
[cad] agent container
  ✓ no real tokens inside cad
  ✓ blender runs headless
  ✓ freecadcmd runs
  ✓ cad: only /models visible
  ✓ cad: Google creds not mounted
  ✓ cad: direct route blocked
passed: 57, failed: 0
[routing] sing-box per-agent policy
  ✓ coder: any https → out
  ✓ coder: non-web port allowed → out
  ✓ web: https → out
  ✓ web: port 8080 rejected → rejected
  ✓ cad: https → out
  ✓ cad: port 8080 rejected → rejected
  ✓ mailgw: googleapis allowed → out
  ✓ mailgw: other domain rejected → rejected
  ✓ keys: anthropic allowed → out
  ✓ keys: other domain rejected → rejected
passed: 10, failed: 0
broker: OK
mailgw: OK
drive: OK
```
