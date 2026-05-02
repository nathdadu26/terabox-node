# bot.py — TeraBox Telegram Bot
# Ek hi file mein sab kuch: config, aria2, downloader, handlers, main

import asyncio
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

import aiohttp
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message

# ─────────────────────────────────────────────────────────────
# 0. ENV
# ─────────────────────────────────────────────────────────────

load_dotenv()

def _req(key: str) -> str:
    v = os.getenv(key)
    if not v:
        raise EnvironmentError(f"Missing required env variable: {key}")
    return v

BOT_TOKEN         = _req("BOT_TOKEN")
API_ID            = int(_req("API_ID"))
API_HASH          = _req("API_HASH")
CHANNEL_ID        = int(_req("CHANNEL_ID"))
TB_ACCOUNT        = os.getenv("TB_ACCOUNT", "")
ARIA2_URL         = os.getenv("ARIA2_URL", "http://localhost:6800/jsonrpc")
ARIA2_SECRET      = os.getenv("ARIA2_SECRET", "")
PROGRESS_INTERVAL = int(os.getenv("PROGRESS_INTERVAL", "5"))

# ─────────────────────────────────────────────────────────────
# 1. LOGGING
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

# ─────────────────────────────────────────────────────────────
# 2. ARIA2 RPC CLIENT
# ─────────────────────────────────────────────────────────────

_rpc_id = 0

async def _rpc(method: str, params: list = None):
    global _rpc_id
    _rpc_id += 1
    params = params or []
    rpc_params = ([f"token:{ARIA2_SECRET}"] + params) if ARIA2_SECRET else params

    body = {
        "jsonrpc": "2.0",
        "id": str(_rpc_id),
        "method": f"aria2.{method}",
        "params": rpc_params,
    }

    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                ARIA2_URL,
                json=body,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json(content_type=None)
    except aiohttp.ClientConnectorError:
        raise ConnectionError(
            f"Aria2 se connect nahi ho pa raha: {ARIA2_URL}\n"
            "`aria2c --enable-rpc` run karo."
        )

    if "error" in data:
        raise RuntimeError(f"Aria2 RPC [{method}]: {data['error']['message']}")
    return data["result"]


@dataclass
class Aria2File:
    path: str
    length: int


@dataclass
class Aria2Status:
    gid:              str
    status:           str
    total_length:     int
    completed_length: int
    download_speed:   int
    error_message:    str
    files:            list = field(default_factory=list)

    @property
    def percent(self) -> float:
        return (self.completed_length / self.total_length * 100) if self.total_length else 0.0


def _parse(raw: dict) -> Aria2Status:
    return Aria2Status(
        gid=raw["gid"],
        status=raw.get("status", ""),
        total_length=int(raw.get("totalLength", 0)),
        completed_length=int(raw.get("completedLength", 0)),
        download_speed=int(raw.get("downloadSpeed", 0)),
        error_message=raw.get("errorMessage", ""),
        files=[Aria2File(f.get("path", ""), int(f.get("length", 0))) for f in raw.get("files", [])],
    )


async def aria2_ping():
    return await _rpc("getVersion")

async def aria2_global_stat():
    return await _rpc("getGlobalStat")

async def aria2_all_gids() -> set:
    active, waiting, stopped = await asyncio.gather(
        _rpc("tellActive",  [["gid"]]),
        _rpc("tellWaiting", [0, 1000, ["gid"]]),
        _rpc("tellStopped", [0, 1000, ["gid"]]),  # completed GIDs bhi check karo
    )
    return {d["gid"] for d in (active + waiting + stopped)}

async def aria2_status(gid: str) -> Aria2Status:
    return _parse(await _rpc("tellStatus", [gid]))

# ─────────────────────────────────────────────────────────────
# 3. UTILS
# ─────────────────────────────────────────────────────────────

def fmt_bytes(b: int) -> str:
    b = max(0, int(b))
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"

def fmt_speed(bps: int) -> str:
    return f"{fmt_bytes(bps)}/s"

def progress_bar(pct: float, w: int = 12) -> str:
    f = round(pct / 100 * w)
    return "█" * f + "░" * (w - f)

def eta_str(done: int, total: int, speed: int) -> str:
    if not speed or not total:
        return "calculating..."
    secs = int((total - done) / speed)
    if secs < 60:   return f"{secs}s"
    if secs < 3600: return f"{secs//60}m {secs%60}s"
    return f"{secs//3600}h {(secs%3600)//60}m"

# ─────────────────────────────────────────────────────────────
# 4. DOWNLOADER
# ─────────────────────────────────────────────────────────────

@dataclass
class DownloadResult:
    gid:        str
    file_path:  str
    file_name:  str
    total_size: int


async def _run_tb(share_url: str):
    """tb-getdl-share JS command chalao subprocess mein."""
    cmd = ["tb-getdl-share", "-a", TB_ACCOUNT, "-s", share_url]
    log.info("Running: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if out.strip(): log.info("[tb stdout] %s", out.strip())
    if err.strip(): log.warning("[tb stderr] %s", err.strip())

    if proc.returncode not in (0, None):
        raise RuntimeError(
            f"tb-getdl-share exit code {proc.returncode}"
            + (f"\n{err}" if err else "")
        )


async def _find_new_gid(before: set, timeout: float = 60.0) -> str:
    """Command ke baad Aria2 mein naya GID dhundo."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)  # faster polling
        current = await aria2_all_gids()
        new = current - before
        if new:
            gid = next(iter(new))
            log.info("Naya GID mila: %s", gid)
            return gid
    raise RuntimeError(
        "Aria2 mein naya download nahi mila!\n"
        "• Link invalid ho sakta hai\n"
        "• .config.yaml mein account check karo"
    )


async def download(
    share_url: str,
    on_progress: Optional[Callable[[Aria2Status], Awaitable[None]]] = None,
) -> DownloadResult:
    before = await aria2_all_gids()
    log.info("GIDs before: %d", len(before))

    await _run_tb(share_url)

    gid = await _find_new_gid(before)

    last_cb = 0.0
    while True:
        s = await aria2_status(gid)
        now = asyncio.get_event_loop().time()

        if on_progress and (now - last_cb) >= PROGRESS_INTERVAL:
            try:
                await on_progress(s)
            except Exception as e:
                log.warning("Progress cb error: %s", e)
            last_cb = now

        log.info("GID=%s | %s | %.1f%% | %s | %s",
                 gid, s.status, s.percent,
                 fmt_bytes(s.completed_length), fmt_speed(s.download_speed))

        if s.status == "complete":
            if not s.files:
                raise RuntimeError("Aria2 ne file path nahi diya!")
            path = s.files[0].path
            return DownloadResult(
                gid=gid,
                file_path=path,
                file_name=path.split("/")[-1],
                total_size=s.total_length,
            )
        if s.status == "error":
            raise RuntimeError(f"Aria2 download failed: {s.error_message or 'Unknown error'}")
        if s.status == "removed":
            raise RuntimeError("Download Aria2 se remove kar diya gaya!")

        await asyncio.sleep(2)

# ─────────────────────────────────────────────────────────────
# 5. BOT HANDLERS
# ─────────────────────────────────────────────────────────────

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts"}


async def safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text, disable_web_page_preview=True)
    except Exception as e:
        err = str(e).lower()
        if "not modified" in err:
            return
        if "flood" in err:
            wait = int(re.search(r"\d+", str(e)).group() or 5)
            await asyncio.sleep(wait + 1)
            await safe_edit(msg, text)
        else:
            log.error("edit error: %s", e)


def register(app: Client):

    @app.on_message(filters.command("start") & filters.private)
    async def cmd_start(_, message: Message):
        await message.reply_text(
            "🤖 **TeraBox Downloader Bot**\n\n"
            "Koi bhi shared link bhejo!\n\n"
            "➤ File download hogi\n"
            "➤ Private channel mein save hogi\n"
            "➤ Aapko copy karke dunga 📁\n\n"
            "**Example:**\n`https://terabox.com/s/xxxxxxxxxx`\n\n"
            "/status — Aria2 ka haal poocho"
        )

    @app.on_message(filters.command("status") & filters.private)
    async def cmd_status(_, message: Message):
        try:
            ver  = await aria2_ping()
            stat = await aria2_global_stat()
            await message.reply_text(
                f"✅ **Aria2 Online**\n"
                f"Version: `{ver['version']}`\n\n"
                f"🔄 Active: `{stat['numActive']}`\n"
                f"⏳ Waiting: `{stat['numWaiting']}`\n"
                f"⚡ Speed: `{fmt_speed(int(stat['downloadSpeed']))}`"
            )
        except Exception as e:
            await message.reply_text(f"❌ **Aria2 offline hai!**\n`{e}`")

    @app.on_message(filters.text & filters.private & ~filters.command(["start", "status"]))
    async def on_link(client: Client, message: Message):
        match = URL_RE.search(message.text or "")
        if not match:
            await message.reply_text(
                "❌ Koi valid URL nahi mila.\n"
                "Format: `https://example.com/...`"
            )
            return

        link = match.group(0)
        user = message.from_user
        log.info("User %s (%d): %s", user.username or user.first_name, user.id, link)

        status_msg = await message.reply_text("🔍 **Link mila!** Shuru ho raha hai...")

        # Aria2 check
        try:
            await aria2_ping()
        except Exception as e:
            await safe_edit(status_msg,
                f"❌ **Aria2 se connect nahi ho pa raha!**\n\n`{e}`")
            return

        await safe_edit(status_msg, "📡 **Aria2 connected!**\nLink process ho raha hai...")

        # Progress callback
        async def on_progress(s: Aria2Status):
            bar = progress_bar(s.percent)
            eta = eta_str(s.completed_length, s.total_length, s.download_speed)
            await safe_edit(status_msg,
                f"⬇️ **Download Ho Raha Hai...**\n\n"
                f"`{bar}` {s.percent:.1f}%\n\n"
                f"📦 `{fmt_bytes(s.completed_length)}` / `{fmt_bytes(s.total_length)}`\n"
                f"⚡ Speed: `{fmt_speed(s.download_speed)}`\n"
                f"⏱ ETA: `{eta}`\n\n"
                f"_GID: {s.gid}_"
            )

        # Download
        try:
            result = await download(link, on_progress)
        except Exception as e:
            log.exception("Download failed")
            await safe_edit(status_msg, f"❌ **Download fail ho gaya!**\n\n`{e}`")
            return

        # Upload to channel
        await safe_edit(status_msg,
            f"✅ **Download Complete!**\n\n"
            f"📁 `{result.file_name}`\n"
            f"📦 `{fmt_bytes(result.total_size)}`\n\n"
            f"📤 Channel pe upload ho raha hai..."
        )

        try:
            ext = os.path.splitext(result.file_name)[1].lower()
            caption = (
                f"📁 **{result.file_name}**\n"
                f"📦 `{fmt_bytes(result.total_size)}`"
            )
            if ext in VIDEO_EXTS:
                channel_msg = await client.send_video(
                    CHANNEL_ID, result.file_path,
                    caption=caption, supports_streaming=True,
                )
            else:
                channel_msg = await client.send_document(
                    CHANNEL_ID, result.file_path,
                    caption=caption,
                )
        except Exception as e:
            log.exception("Upload failed")
            await safe_edit(status_msg,
                f"✅ Download hua\n❌ **Upload fail:**\n`{e}`\n\n"
                f"Path: `{result.file_path}`"
            )
            return

        # Copy to user
        await safe_edit(status_msg,
            f"✅ **Sab ho gaya!**\n📁 `{result.file_name}`\n\nFile neeche aa rahi hai 👇"
        )
        try:
            await client.copy_message(message.chat.id, CHANNEL_ID, channel_msg.id)
            log.info("User %d ko copy kar di: %s", user.id, result.file_name)
        except Exception as e:
            log.exception("Copy failed")
            await message.reply_text(f"❌ Copy nahi ho pa raha:\n`{e}`")

# ─────────────────────────────────────────────────────────────
# 6. MAIN
# ─────────────────────────────────────────────────────────────

async def main():
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("🤖 TeraBox Bot starting...")
    log.info("TB_ACCOUNT : %s", TB_ACCOUNT)
    log.info("CHANNEL_ID : %d", CHANNEL_ID)
    log.info("ARIA2_URL  : %s", ARIA2_URL)

    try:
        ver = await aria2_ping()
        log.info("✅ Aria2 online — v%s", ver["version"])
    except Exception as e:
        log.warning("⚠️  Aria2 offline: %s", e)

    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    app = Client(
        name="terabox_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
    )

    register(app)

    await app.start()
    me = await app.get_me()
    log.info("✅ Bot online: @%s", me.username)
    log.info("Waiting for messages... (Ctrl+C to stop)")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot band ho raha hai...")
