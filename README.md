### VPN Template (Ubuntu 22.04) — AmneziaWG/WireGuard + Xray + Telegram мониторинг

Этот репозиторий-шаблон позволяет быстро поднять VPN-сервер (AmneziaWG или стандартный WireGuard через wg-easy) и Telegram-бота для мониторинга: статус, активные подключения, графики нагрузки (CPU/MEM/NET), алерты, speedtest. Дополнительно можно включить Xray (VLESS Reality) на 443/tcp.

### Быстрый старт

1) Подготовьте `.env` на сервере (скопируйте из `env.example` и заполните):

```
cp env.example .env
vi .env
```

Обязательные переменные: публичный IP сервера (`WG_HOST`), токен Telegram-бота (`TELEGRAM_BOT_TOKEN`).

- Если используете AmneziaWG: выставьте `AWG_ENABLED=true`. При необходимости измените `AWG_PORT` (по умолчанию 443/udp) и параметры джиттера (`AWG_JC`, `AWG_JMIN`, `AWG_JMAX`, `AWG_S1`, `AWG_S2`).
- Если используете стандартный WireGuard: оставьте `AWG_ENABLED=false`, настройте `WG_*` и UI пароль для `wg-easy`.

2) Выполните установку (Ubuntu 22.04):

```
sudo bash scripts/setup.sh
```

Скрипт:
- установит Docker и зависимости;
- применит sysctl и UFW (откроет 22/tcp, 51820/udp, 51821/tcp; если `AWG_ENABLED=true` — откроет `AWG_PORT`/udp);
- подготовит Xray-конфиг (если `XRAY_ENABLED=true`) и поднимет сервисы.

3) В Telegram напишите боту `/start`. Бот запомнит ваш chat_id (или укажите `TELEGRAM_ALLOWED_CHAT_ID` в `.env`).

### AmneziaWG (AWG)

- Этот шаблон не запускает сам AWG-контейнер, а оставляет его установку гибкой (вне compose). Включите `AWG_ENABLED=true`, скрипт не будет стартовать `wg-easy` и не будет делать редирект 443→51820/udp.
- Откройте в фаерволе `AWG_PORT` (делает `setup.sh`).
- Бот команда `/peers` поддерживает AWG: если `AWG_ENABLED=true`, сначала пытается `docker exec $AWG_CONTAINER wg show`, затем fallback на `wg show` в хосте.

Рекомендованные параметры (если соединение нестабильно):
- Jc от 3 до 5; Jmin=40; Jmax=70; при необходимости S1/S2 от 2 до 10.

### Xray VLESS-Reality (опционально)

- В `.env` установите `XRAY_ENABLED=true`. `scripts/setup.sh` сгенерирует ключи Reality, UUID и конфиг `data/xray/config.json` (TCP + Reality, flow `xtls-rprx-vision`).
- После запуска выведите ссылку:

```
bash scripts/print_vless.sh
```

### Что разворачивается

- `wg-easy` — если `AWG_ENABLED=false` (WG + UI);
- `vpn-bot` — Telegram-бот: `/status`, `/peers`, `/graph [часы]`, `/speedtest`, `/help` и заявка на Xray;
- `xray` — при `XRAY_ENABLED=true` (VLESS Reality на `XRAY_PORT`).

Данные:
- конфиги WireGuard: `./data/wg-easy`
- конфиг Xray: `./data/xray/config.json`
- база метрик SQLite: `./data/bot/metrics.sqlite`

### Переменные окружения (.env)

См. `.env.example` для полного списка. Ключевые:
- `WG_HOST` — публичный IP/домен сервера
- `AWG_ENABLED` — включить режим AmneziaWG (по умолчанию false)
- `AWG_PORT` — порт UDP для AWG (по умолчанию 443)
- `AWG_JC`, `AWG_JMIN`, `AWG_JMAX`, `AWG_S1`, `AWG_S2` — параметры джиттера
- `XRAY_ENABLED`, `XRAY_PORT`, `REALITY_*`, `XRAY_UUID` — параметры Xray (Reality)
- `TELEGRAM_BOT_TOKEN` — токен бота; `TELEGRAM_ALLOWED_CHAT_ID` — (опционально) разрешённый chat_id

### Команды управления

```
./scripts/run.sh up       # поднять сервисы (wg-easy+bot или только bot при AWG)
./scripts/run.sh down     # остановить
./scripts/run.sh restart  # перезапуск
./scripts/run.sh logs     # логи бота
```

### Обновление

```
git pull
./scripts/run.sh pull && ./scripts/run.sh up
```

### Примечания по безопасности

- Установите сильный `WG_EASY_PASSWORD` (если используете UI);
- Ограничьте доступ к боту `TELEGRAM_ALLOWED_CHAT_ID`;
- Держите `.env` приватным.


