# Как запускать агентов

Все команды выполняются из `~/agent-sandbox`. Подробности устройства описаны в `ARCHITECTURE.md`.

## 1. Один раз: подготовка

```sh
bin/sandbox init      # секреты, CA брокера, пароль Grafana
bin/sandbox vless     # вставить ключ vless:// (ввод скрыт); поднимет vpn и проверит kill switch
bin/sandbox tunnel    # живая проверка туннеля: маршрут до сервера, exit IP, агенты
```

Если `tunnel` пишет `route … goes via tun0`, значит хостовой VPN (Happ) работает в режиме TUN
и заворачивает туннель песочницы в себя. Выключите режим TUN или добавьте в исключения IP
сервера и `172.16.0.0/12`, затем выполните `bin/sandbox restart vpn` и `bin/sandbox tunnel`.

## 2. Один раз: логины

Реальные токены хранятся только в брокере (`sbx-keys`). В контейнеры агентов они не попадают.

| Что | Команда | Кому нужно |
|---|---|---|
| Claude (подписка) | `bin/sandbox login <agent> claude` | для каждого агента, где нужен Claude |
| Codex (ChatGPT) | `bin/sandbox login coder codex` | один логин сразу на coder, infra, cad |
| GitHub PAT | `bin/sandbox key coder github` | coder, infra |
| Gmail + Calendar | `bin/sandbox login assistant google` | assistant (нужен `secrets/google-client.json`) |
| Google Drive | `bin/sandbox login cad google` | cad |

Чтобы не логинить Claude отдельно в каждом агенте, можно скопировать один токен:
```sh
for a in infra cad web admin assistant; do mkdir -p secrets/keys/$a; cp secrets/keys/coder/anthropic secrets/keys/$a/; done
bin/sandbox restart keys
```
Посмотреть, что настроено: `bin/sandbox keys`.

**GitHub:** нужен fine-grained PAT (Settings → Developer settings → Fine-grained tokens). Для coder дайте доступ
только к нужным репозиториям, для infra — только к `agent-sandbox`.

**Google (почта, календарь, Drive):**
1. https://console.cloud.google.com → создайте проект → APIs & Services → включите **Gmail API**,
   **Google Calendar API** и **Google Drive API**.
2. OAuth consent screen → External → добавьте себя в Test users.
3. Credentials → Create credentials → OAuth client ID → **Desktop app** → скачайте JSON и
   положите его как `secrets/google-client.json` (`chmod 600`).
4. Выполните `bin/sandbox login assistant google` и `bin/sandbox login cad google`. После согласия браузер
   откроет `http://localhost:8085/?code=…` с ошибкой. Это ожидаемо: скопируйте этот URL в терминал.

## 3. Запуск

```sh
bin/agent <agent> [claude|codex|opencode|shell] [аргументы инструмента]
```
Контейнер агента поднимается сам, если ещё не запущен. По умолчанию запускается `claude`
(у opencode по умолчанию `opencode`). Аргументы после инструмента передаются ему как есть:
`bin/agent coder claude --resume`.

| Агент | Для чего | Рабочая папка | Инструменты |
|---|---|---|---|
| `coder` | код, пет-проекты | текущая папка, если она внутри `~/Projects`, иначе `~/Projects` | claude, codex, shell |
| `opencode` | то же, бесплатные модели Zen, без ключей | как у coder | opencode, shell |
| `infra` | правки этой песочницы | `~/agent-sandbox` | claude, codex, shell |
| `admin` | смотрит хост read-only, пишет предложения | `/proposals` | claude, shell |
| `cad` | FreeCAD / Blender headless | `~/3d-models` | claude, codex, shell |
| `assistant` | почта и календарь, интернета нет | — | claude |
| `web` | браузинг и ресёрч, новый контейнер на каждую сессию | — | claude, shell |

Примеры:
```sh
cd ~/Projects/myapp && ~/agent-sandbox/bin/agent coder        # Claude в myapp
bin/agent coder codex
bin/agent opencode
bin/agent web                                                 # отчёты → web-outbox/ (недоверенные)
```

## 4. После работы агентов

- **infra**: агент только коммитит. Вы смотрите изменения и применяете их сами: `bin/sandbox apply` (`git diff` → пересборка и перезапуск).
- **admin**: предложения появляются в `proposals/NNN-*.sh`. Прочитайте их и выполните сами, если согласны.
- **assistant**: отправка писем и приглашений ждёт одобрения: `bin/sandbox approvals`, затем `show <id>` и `approve` или `deny`.

## 5. Управление и наблюдение

```sh
bin/sandbox status           # контейнеры, health, ключи
bin/sandbox up coder monitor # поднять заранее (+ Grafana)
bin/sandbox logs vpn         # соединения; logs keys — API-запросы через брокер
bin/sandbox dashboard        # живые соединения (zashboard)
bin/sandbox grafana          # история запросов
bin/sandbox test             # изоляция и политика (работает и без туннеля)
bin/sandbox down             # остановить всё
```

**Инструменты агентов:** в базовом образе есть git, `gh`, node и npm, python3 и pip, Go, Rust (cargo), Lua 5.4,
cmake, build-essential, sqlite3, ripgrep, fd, jq. Если чего-то не хватает, попросите `bin/agent infra` добавить
это в `agents/base/Dockerfile`, проверьте `git diff` и выполните `bin/sandbox apply`. admin и cad собраны
на базовом образе, поэтому пересобираются вместе с ним (cad весит около 5 ГБ, сборка долгая).

## 6. Хост (необязательно)

**Хост через VPN песочницы** (сначала выключите свой VPN с `tun0`):
```sh
bin/sandbox host-vpn install          # профиль: secrets/host/agent-vpn.nmconnection
sudo install -m 600 -o root -g root secrets/host/agent-vpn.nmconnection /etc/NetworkManager/system-connections/
sudo nmcli connection reload
bin/sandbox host-vpn watch-install    # watcher: при падении туннеля хост идёт напрямую
bin/sandbox host-vpn on
```
Если через VPN нужен только браузер, подключите PAC-файл `host/proxy.pac`.

- **OpenSnitch:** `sudo sh host/opensnitch/install.sh` (ставит пакет, включает режим мониторинга, отправляет логи в Grafana).
- **buildx**, чтобы сборки образов шли через VPN: `sudo pacman -S docker-buildx`.
