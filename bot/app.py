import asyncio
import os
import time
import socket
import json
import psutil
import logging
import aiosqlite
import subprocess
from datetime import datetime, timedelta
import uuid as uuidlib

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler


DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "metrics.sqlite")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "")
METRICS_INTERVAL_SEC = int(os.getenv("METRICS_INTERVAL_SEC", "15"))
GRAPH_DEFAULT_HOURS = int(os.getenv("GRAPH_DEFAULT_HOURS", "3"))
ALERT_CPU_PCT = float(os.getenv("ALERT_CPU_PCT", "85"))
ALERT_MEM_PCT = float(os.getenv("ALERT_MEM_PCT", "85"))
ALERT_NET_MBPS = float(os.getenv("ALERT_NET_MBPS", "200"))
ALERT_COOLDOWN_MIN = int(os.getenv("ALERT_COOLDOWN_MIN", "10"))
WG_CONTAINER = os.getenv("WG_CONTAINER", "wg-easy")

LAST_ALERT_TS = 0.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    await context.bot.send_message(chat_id=chat_id, text=text)


async def reply_html(update: Update, context: ContextTypes.DEFAULT_TYPE, html: str):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    await context.bot.send_message(chat_id=chat_id, text=html, parse_mode="HTML", disable_web_page_preview=True)


async def reply_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, filepath: str, filename: str = "image.png"):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    with open(filepath, 'rb') as f:
        await context.bot.send_photo(chat_id=chat_id, photo=f)


async def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS samples (
                ts INTEGER NOT NULL,
                cpu REAL NOT NULL,
                mem REAL NOT NULL,
                net_in_bps REAL NOT NULL,
                net_out_bps REAL NOT NULL,
                disk_used_pct REAL NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS kv (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,            -- 'xray'
                user_id INTEGER NOT NULL,
                username TEXT,
                status TEXT NOT NULL,          -- 'pending' | 'approved' | 'rejected'
                created_ts INTEGER NOT NULL,
                approved_ts INTEGER,
                approver_chat_id INTEGER,
                client_uuid TEXT,
                note TEXT
            )
            """
        )
        await db.commit()


async def get_kv(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT v FROM kv WHERE k=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_kv(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )
        await db.commit()


def human_bytes_per_sec(bps: float) -> str:
    if bps < 1024:
        return f"{bps:.0f} B/s"
    kbps = bps / 1024
    if kbps < 1024:
        return f"{kbps:.1f} KB/s"
    mbps = kbps / 1024
    if mbps < 1024:
        return f"{mbps:.2f} MB/s"
    gbps = mbps / 1024
    return f"{gbps:.2f} GB/s"


async def sample_metrics():
    # CPU and MEM
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent
    disk_used_pct = psutil.disk_usage("/").percent

    # Network throughput since last call
    net1 = psutil.net_io_counters()
    await asyncio.sleep(1)
    net2 = psutil.net_io_counters()
    in_bps = (net2.bytes_recv - net1.bytes_recv)
    out_bps = (net2.bytes_sent - net1.bytes_sent)

    ts = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO samples(ts, cpu, mem, net_in_bps, net_out_bps, disk_used_pct) VALUES(?,?,?,?,?,?)",
            (ts, float(cpu), float(mem), float(in_bps), float(out_bps), float(disk_used_pct)),
        )
        await db.commit()

    await maybe_alert(cpu, mem, in_bps, out_bps)


async def maybe_alert(cpu: float, mem: float, in_bps: float, out_bps: float):
    global LAST_ALERT_TS
    now = time.time()
    if LAST_ALERT_TS and now - LAST_ALERT_TS < ALERT_COOLDOWN_MIN * 60:
        return

    high_cpu = cpu >= ALERT_CPU_PCT
    high_mem = mem >= ALERT_MEM_PCT
    in_mbps = (in_bps * 8) / 1_000_000
    out_mbps = (out_bps * 8) / 1_000_000
    high_net = in_mbps >= ALERT_NET_MBPS or out_mbps >= ALERT_NET_MBPS

    if high_cpu or high_mem or high_net:
        chat_id = await get_allowed_chat_id()
        if chat_id:
            msg = ["⚠️ Alert thresholds exceeded:"]
            if high_cpu:
                msg.append(f"CPU: {cpu:.1f}% ≥ {ALERT_CPU_PCT}%")
            if high_mem:
                msg.append(f"MEM: {mem:.1f}% ≥ {ALERT_MEM_PCT}%")
            if high_net:
                msg.append(
                    f"NET: IN {in_mbps:.1f} Mbps, OUT {out_mbps:.1f} Mbps ≥ {ALERT_NET_MBPS} Mbps"
                )
            try:
                await app.bot.send_message(chat_id=chat_id, text="\n".join(msg))
                LAST_ALERT_TS = now
            except Exception:
                pass


async def get_allowed_chat_id() -> int | None:
    if ALLOWED_CHAT_ID.strip():
        try:
            return int(ALLOWED_CHAT_ID)
        except ValueError:
            return None
    v = await get_kv("allowed_chat_id")
    return int(v) if v else None


async def set_allowed_chat_id(chat_id: int):
    await set_kv("allowed_chat_id", str(chat_id))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    allowed = await get_allowed_chat_id()
    if allowed is None:
        await set_allowed_chat_id(chat_id)
        await update.message.reply_text("✅ Chat authorized. Use /help")
    elif allowed == chat_id:
        await update.message.reply_text("✅ Already authorized. Use /help")
    else:
        await update.message.reply_text("⛔ This bot is locked to another chat")


def guard(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        allowed = await get_allowed_chat_id()
        if allowed is not None and update.effective_chat.id != allowed:
            return
        return await func(update, context)
    return wrapper


@guard
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📊 Статус", callback_data="status"), InlineKeyboardButton("👥 Пиры", callback_data="peers")],
        [InlineKeyboardButton("📈 График", callback_data="graph_3"), InlineKeyboardButton("⚡ Speedtest", callback_data="speedtest")],
        [InlineKeyboardButton("🔑 Запросить Xray", callback_data="request_xray")],
    ]
    # Works for both message and callback contexts
    if update.message:
        await update.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await reply_text(update, context, "Выберите действие:")


@guard
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    boot = datetime.fromtimestamp(psutil.boot_time())

    net1 = psutil.net_io_counters()
    await asyncio.sleep(1)
    net2 = psutil.net_io_counters()
    in_bps = (net2.bytes_recv - net1.bytes_recv)
    out_bps = (net2.bytes_sent - net1.bytes_sent)

    lines = [
        f"CPU: {cpu:.1f}%",
        f"MEM: {mem:.1f}%",
        f"DISK: {disk:.1f}%",
        f"NET: IN {human_bytes_per_sec(in_bps)}, OUT {human_bytes_per_sec(out_bps)}",
        f"UPTIME: {datetime.now() - boot} (since {boot.strftime('%Y-%m-%d %H:%M:%S')})",
        f"HOST: {socket.gethostname()}"
    ]
    await reply_text(update, context, "\n".join(lines))


def run_host_cmd(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, text=True)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return 1, "", str(e)


def run_host_cmd_input(cmd: list[str], input_text: str, timeout: int = 10) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, input=input_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, text=True)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return 1, "", str(e)


@guard
async def cmd_peers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Query WireGuard peers from wg-easy container
    code, out, err = run_host_cmd(["/usr/bin/env", "bash", "-lc", f"docker exec {WG_CONTAINER} wg show"], timeout=20)
    if code != 0 or not out.strip():
        await reply_text(update, context, ("Failed to fetch peers: " + (err or out))[:1000])
        return
    await reply_html(update, context, "<pre>" + out + "</pre>")


@guard
async def cmd_graph(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        hours = int(context.args[0]) if context.args else GRAPH_DEFAULT_HOURS
    except ValueError:
        hours = GRAPH_DEFAULT_HOURS
    since_ts = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT ts, cpu, mem, net_in_bps, net_out_bps FROM samples WHERE ts >= ? ORDER BY ts ASC",
            (since_ts,),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await reply_text(update, context, "Нет данных для графика")
        return

    # Build simple matplotlib PNG
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    ts = [datetime.fromtimestamp(r[0]) for r in rows]
    cpu = [r[1] for r in rows]
    mem = [r[2] for r in rows]
    in_mbps = [(r[3] * 8) / 1_000_000 for r in rows]
    out_mbps = [(r[4] * 8) / 1_000_000 for r in rows]

    fig, ax1 = plt.subplots(figsize=(10, 5), dpi=140)
    ax1.plot(ts, cpu, label='CPU %', color='tab:red')
    ax1.plot(ts, mem, label='MEM %', color='tab:orange')
    ax1.set_ylabel('%')
    ax1.set_ylim(0, 100)
    ax1.grid(True, linestyle='--', alpha=0.3)

    ax2 = ax1.twinx()
    ax2.plot(ts, in_mbps, label='NET IN Mbps', color='tab:blue')
    ax2.plot(ts, out_mbps, label='NET OUT Mbps', color='tab:green')
    ax2.set_ylabel('Mbps')

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')
    fig.autofmt_xdate()

    out_path = os.path.join(DATA_DIR, "graph.png")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)

    await reply_photo(update, context, out_path, filename="graph.png")


@guard
async def cmd_speedtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    server_id = os.getenv("SPEEDTEST_SERVER_ID", "").strip()
    args = ["speedtest-cli", "--simple", "--timeout", "15"]
    if server_id:
        args.extend(["--server", server_id])
    code, out, err = run_host_cmd(args, timeout=60)
    if code != 0:
        await reply_text(update, context, f"⚠️ speedtest failed: {(err or out)[:900]}")
        return
    # Parse simple output
    dl = up = ping = None
    for line in out.splitlines():
        if line.startswith("Download"):
            try:
                dl = float(line.split(":")[1].strip().split()[0])
            except Exception:
                pass
        if line.startswith("Upload"):
            try:
                up = float(line.split(":")[1].strip().split()[0])
            except Exception:
                pass
        if line.startswith("Ping"):
            try:
                ping = float(line.split(":")[1].strip().split()[0])
            except Exception:
                pass
    now = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    msg = ["🌐 Результаты теста скорости:\n"]
    if dl is not None:
        msg.append(f"📥 Download: {dl:.2f} Mbps")
    if up is not None:
        msg.append(f"📤 Upload: {up:.2f} Mbps")
    if ping is not None:
        msg.append(f"⏱️ Ping: {ping:.1f} ms")
    if dl and dl >= 50:
        msg.append("\n✅ Отличная скорость загрузки!")
    elif dl and dl < 10:
        msg.append("\n⚠️ Низкая скорость загрузки!")
    if up and up >= 20:
        msg.append("✅ Отличная скорость отдачи!")
    elif up and up < 5:
        msg.append("⚠️ Низкая скорость отдачи!")
    msg.append(f"\nВремя теста: {now}")
    await reply_text(update, context, "\n".join(msg))


# ----------------------- XRAY ISSUE FLOW -----------------------

def is_xray_enabled() -> bool:
    return os.getenv("XRAY_ENABLED", "false").lower() == "true"


def _read_xray_config() -> dict | None:
    code, out, err = run_host_cmd(["/usr/bin/env", "bash", "-lc", "docker exec xray sh -c 'cat /etc/xray/config.json'"], timeout=20)
    if code != 0 or not out.strip():
        logging.warning("Failed to read xray config: %s", err or out)
        return None
    try:
        return json.loads(out)
    except Exception as e:
        logging.exception("Invalid xray config json: %s", e)
        return None


def _write_xray_config(cfg: dict) -> bool:
    try:
        tmp_path = os.path.join(DATA_DIR, "tmp_xray_config.json")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.exception("Failed to write tmp xray config: %s", e)
        return False
    code, out, err = run_host_cmd(["/usr/bin/env", "bash", "-lc", f"docker cp {tmp_path} xray:/etc/xray/config.json"], timeout=20)
    if code != 0:
        logging.warning("docker cp failed: %s", err or out)
        return False
    code, out, err = run_host_cmd(["/usr/bin/env", "bash", "-lc", "docker restart xray"], timeout=60)
    if code != 0:
        logging.warning("docker restart xray failed: %s", err or out)
        return False
    return True


def _generate_vless_url(client_uuid: str, label: str) -> str:
    host = os.getenv("WG_HOST", "")
    port = os.getenv("XRAY_PORT", "443")
    sni = os.getenv("REALITY_SNI", "")
    sid = os.getenv("REALITY_SHORT_ID", "")
    pub = os.getenv("REALITY_PUBLIC_KEY", "")
    query = f"type=tcp&security=reality&pbk={pub}&sid={sid}&sni={sni}&flow=xtls-rprx-vision"
    return f"vless://{client_uuid}@{host}:{port}?{query}#{label}"


async def _notify_admin_new_request(app_handle: Application, req_id: int, user_id: int, username: str | None):
    chat_id = await get_allowed_chat_id()
    if not chat_id:
        return
    uname = username or "unknown"
    kb = [[
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_xray_{req_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_xray_{req_id}")
    ]]
    text = f"Новый запрос Xray\nuser_id: {user_id}\nusername: {uname}\nrequest_id: {req_id}"
    try:
        await app_handle.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        pass


async def _create_or_update_request(user_id: int, username: str | None) -> int:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO requests(kind, user_id, username, status, created_ts) VALUES(?,?,?,?,?)",
            ("xray", user_id, username, "pending", now),
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        row = await cur.fetchone()
        return int(row[0])


async def _approve_request(req_id: int, approver_chat_id: int) -> tuple[bool, str]:
    # Load request
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, user_id, username, status FROM requests WHERE id=?", (req_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return False, "Заявка не найдена"
    _, user_id, username, status = row
    if status != "pending":
        return False, "Заявка уже обработана"

    if not is_xray_enabled():
        return False, "XRAY_DISABLED"

    cfg = _read_xray_config()
    if not cfg:
        return False, "Не удалось прочитать конфиг Xray"

    # Find inbound with clients
    inbounds = cfg.get("inbounds", [])
    inbound = None
    for ib in inbounds:
        if ib.get("tag") == "vless-reality":
            inbound = ib
            break
    if inbound is None and inbounds:
        inbound = inbounds[0]
    if inbound is None:
        return False, "Некорректный конфиг Xray (нет inbounds)"

    settings = inbound.setdefault("settings", {})
    clients = settings.setdefault("clients", [])

    new_uuid = str(uuidlib.uuid4())
    email = f"tg_{user_id}@local"
    clients.append({
        "id": new_uuid,
        "email": email,
        "flow": "xtls-rprx-vision"
    })

    if not _write_xray_config(cfg):
        return False, "Не удалось сохранить/перезапустить Xray"

    # Persist approval
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE requests SET status='approved', approved_ts=?, approver_chat_id=?, client_uuid=? WHERE id=?",
            (now, approver_chat_id, new_uuid, req_id),
        )
        await db.commit()

    # Send link to user
    label = (username or "xray").replace(" ", "_")
    url = _generate_vless_url(new_uuid, label)
    try:
        await app.bot.send_message(chat_id=user_id, text=f"Ваш доступ одобрен.\n{url}")
    except Exception:
        pass
    return True, "Одобрено"


async def _reject_request(req_id: int, approver_chat_id: int) -> tuple[bool, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, status FROM requests WHERE id=?", (req_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return False, "Заявка не найдена"
    _, status = row
    if status != "pending":
        return False, "Заявка уже обработана"
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE requests SET status='rejected', approved_ts=?, approver_chat_id=? WHERE id=?",
            (now, approver_chat_id, req_id),
        )
        await db.commit()
    return True, "Отклонено"


async def cmd_request_xray(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Allow any user to request
    if not is_xray_enabled():
        await reply_text(update, context, "Сервис Xray отключён на сервере")
        return
    user = update.effective_user
    if not user:
        return
    req_id = await _create_or_update_request(user.id, user.username)
    await reply_text(update, context, "Запрос отправлен. Ожидайте подтверждения админа.")
    await _notify_admin_new_request(app, req_id, user.id, user.username)


async def scheduler_job():
    try:
        await sample_metrics()
    except Exception:
        pass


async def on_startup(application: Application):
    # Ensure DB exists before starting jobs
    await init_db()
    scheduler = AsyncIOScheduler(timezone=os.getenv("TZ", "UTC"))
    scheduler.add_job(scheduler_job, IntervalTrigger(seconds=METRICS_INTERVAL_SEC))
    scheduler.start()
    application.bot_data["scheduler"] = scheduler


def main():
    global app
    app = (
        Application
        .builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("peers", cmd_peers))
    app.add_handler(CommandHandler("graph", cmd_graph))
    app.add_handler(CommandHandler("speedtest", cmd_speedtest))
    app.add_handler(CommandHandler("request_xray", cmd_request_xray))
    app.add_handler(CallbackQueryHandler(handle_buttons))

    # Single blocking polling; container entrypoint restarts process if needed
    app.run_polling(drop_pending_updates=True)

    # Initialize/start async application then keep polling without exiting
    async def _runner():
        await app.initialize()
        await app.start()
        logging.info("Bot started. Entering polling loop...")
        while True:
            try:
                await app.updater.start_polling(drop_pending_updates=True)
            except Exception as e:
                logging.exception("Polling failed, retry in 5s: %s", e)
                await asyncio.sleep(5)
                continue
            logging.warning("Polling stopped; restarting in 5s")
            await asyncio.sleep(5)

    asyncio.run(_runner())

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "status":
        return await cmd_status(update, context)
    if data == "peers":
        return await cmd_peers(update, context)
    if data.startswith("graph_"):
        context.args = [data.split("_", 1)[1]]
        return await cmd_graph(update, context)
    if data == "speedtest":
        return await cmd_speedtest(update, context)
    if data == "request_xray":
        return await cmd_request_xray(update, context)
    if data.startswith("approve_xray_"):
        try:
            req_id = int(data.split("_")[-1])
        except Exception:
            return
        ok, msg = await _approve_request(req_id, update.effective_chat.id)
        await reply_text(update, context, msg)
        return
    if data.startswith("reject_xray_"):
        try:
            req_id = int(data.split("_")[-1])
        except Exception:
            return
        ok, msg = await _reject_request(req_id, update.effective_chat.id)
        await reply_text(update, context, msg)
        return

    # (removed module-level polling loop)


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    main()


