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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes


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
    await update.message.reply_text(
        "/status — текущие метрики\n"
        "/peers — активные VPN подключения\n"
        "/graph [часы] — график нагрузки (по умолчанию 3)\n"
        "/speedtest — тест скорости\n"
    )


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
    await update.message.reply_text("\n".join(lines))


def run_host_cmd(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, text=True)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return 1, "", str(e)


@guard
async def cmd_peers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Query WireGuard peers from wg-easy container
    code, out, err = run_host_cmd(["/usr/bin/env", "bash", "-lc", f"docker exec {WG_CONTAINER} wg show"], timeout=20)
    if code != 0 or not out.strip():
        await update.message.reply_text(("Failed to fetch peers: " + (err or out))[:1000])
        return
    # Escape MarkdownV2 special characters
    escaped = out.replace("-", "\\-").replace(".", "\\.").replace("_", "\\_").replace("*", "\\*")\
                 .replace("(", "\\(").replace(")", "\\)").replace("[", "\\[").replace("]", "\\]")\
                 .replace("#", "\\#").replace("+", "\\+").replace("=", "\\=")
    await update.message.reply_text("```\n" + escaped + "\n```", parse_mode="MarkdownV2")


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
        await update.message.reply_text("Нет данных для графика")
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

    with open(out_path, 'rb') as f:
        await update.message.reply_photo(InputFile(f, filename="graph.png"))


@guard
async def cmd_speedtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    server_id = os.getenv("SPEEDTEST_SERVER_ID", "").strip()
    args = ["speedtest-cli", "--simple", "--timeout", "15"]
    if server_id:
        args.extend(["--server", server_id])
    code, out, err = run_host_cmd(args, timeout=60)
    if code != 0:
        await update.message.reply_text(f"speedtest failed: {err or out}"[:1000])
        return
    await update.message.reply_text(out[:3500])


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
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("peers", cmd_peers))
    app.add_handler(CommandHandler("graph", cmd_graph))
    app.add_handler(CommandHandler("speedtest", cmd_speedtest))

    app.post_init(on_startup)

    # Run polling (blocks until termination)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    main()


