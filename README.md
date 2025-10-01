### VPN Template (Ubuntu 22.04) — Amnezia-compatible WireGuard server + Telegram мониторинг

Этот репозиторий-шаблон позволяет быстро поднять VPN-сервер (WireGuard, совместим с Amnezia-клиентом) и Telegram-бота для мониторинга: статус, активные подключения, графики нагрузки (CPU/MEM/NET), алерты по порогам, speedtest.

### Быстрый старт

1) Подготовьте `.env` на сервере (скопируйте из `env.example` и заполните):

```
cp env.example .env
vi .env
```

Обязательные переменные: публичный IP сервера (`WG_HOST`), токен Telegram-бота (`TELEGRAM_BOT_TOKEN`).

2) Выполните установку (Ubuntu 22.04):

```
sudo bash scripts/setup.sh
```

Скрипт:
- установит Docker и зависимости;
- применит sysctl и UFW (откроет 22/tcp, 51820/udp, 51821/tcp (UI wg-easy));
- сгенерирует bcrypt-хэш пароля UI для `wg-easy` (если задан `WG_EASY_PASSWORD`);
- поднимет `docker compose up -d`.

3) В Telegram напишите боту `/start`. Бот запомнит ваш chat_id (или укажите `TELEGRAM_ALLOWED_CHAT_ID` в `.env`).

### Xray VLESS-Reality (опционально)

- В `.env` установите `XRAY_ENABLED=true`. Скрипт `scripts/setup.sh` при первом запуске сгенерирует ключи Reality, UUID и конфиг `data/xray/config.json`.
- После запуска выполните:

```
bash scripts/print_vless.sh
```

Команда выведет готовую ссылку формата VLESS Reality, которую можно импортировать в поддерживаемые клиенты (например, v2rayNG, Nekoray и т.д.). Параметры: flow `xtls-rprx-vision`, TCP + Reality.

### Что разворачивается

- `wg-easy` — удобный WireGuard-сервер с UI, совместим с Amnezia-клиентом (можно импортировать WG-конфиги в Amnezia).
- `vpn-bot` — Telegram-бот с фоновым сбором метрик в SQLite и командами:
  - `/status` — текущее CPU/MEM/NET, диски, аптайм;
  - `/peers` — активные WG-пиры (по последнему рукопожатию);
  - `/graph [часы]` — PNG-график за последние N часов (по умолчанию 3);
  - `/speedtest` — тест скорости с сервера;
  - `/help` — справка.

Алерты: при превышении порогов CPU/MEM/NET бот отправляет уведомления (с тайм-аутом `ALERT_COOLDOWN_MIN`).

Где хранится:
- конфиги WireGuard: `./data/wg-easy`
- база метрик SQLite: `./data/bot/metrics.sqlite`

### Переменные окружения (.env)

См. `.env.example` для полного списка. Ключевые:
- `WG_HOST` — публичный IP/домен сервера
- `WG_PORT` — порт WireGuard (UDP), по умолчанию 51820
- `WG_EASY_PASSWORD` — простой пароль для UI; хэш будет сгенерирован в `WG_EASY_PASSWORD_HASH`
- `TELEGRAM_BOT_TOKEN` — токен вашего Telegram-бота
- `TELEGRAM_ALLOWED_CHAT_ID` — (опционально) разрешённый chat_id; иначе первый `/start` сохранит его

Пороги алертов и интервалы можно настроить через переменные `ALERT_*` и `METRICS_INTERVAL_SEC`.

### Команды управления

```
./scripts/run.sh up       # поднять сервисы
./scripts/run.sh down     # остановить
./scripts/run.sh restart  # перезапуск
./scripts/run.sh logs     # логи бота
```

### Обновление

```
git pull
./scripts/run.sh pull && ./scripts/run.sh up
```

### Публикация в GitHub (локально на сервере)

Замените `REPO_URL` на свой (например, `https://github.com/aeshef/vpn-template.git`) и запустите:

```
bash scripts/publish_github.sh "https://github.com/aeshef/vpn-template.git"
```

Или вручную:

```
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <REPO_URL>
git push -u origin main
```

### Примечания по Amnezia

Amnezia-клиент поддерживает импорт стандартных WireGuard-конфигов. Сгенерируйте peer в UI `wg-easy` или через CLI и импортируйте в Amnezia.

### Безопасность

- Обязательно установите сильный `WG_EASY_PASSWORD` (UI) — хэш генерируется автоматически.
- Ограничьте доступ к боту через `TELEGRAM_ALLOWED_CHAT_ID`.
- Храните `.env` вне публичного репозитория.


