# bot.py — TeraBox Telegram Bot (English, Full Featured)

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
ARIA2_URL         = os.getenv("ARIA2_URL", "http://localhost:6800/jsonrpc")
ARIA2_SECRET      = os.getenv("ARIA2_SECRET", "")
PROGRESS_INTERVAL = int(os.getenv("PROGRESS_INTERVAL", "5"))

# ── Account rotation ──────────────────────────────────────────
# TB_ACCOUNTS = "main,second,third,fourth,fifth"  (comma separated)
# Fallback: TB_ACCOUNT = "main"  (old single-account env)
_accounts_raw = os.getenv("TB_ACCOUNTS", "") or os.getenv("TB_ACCOUNT", "main")
TB_ACCOUNTS: list[str] = [a.strip() for a in _accounts_raw.split(",") if a.strip()]
log_placeholder = None  # set after logging init

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
# 2. UPLOAD LOCK  (one upload at a time — Telegram limit)
# ─────────────────────────────────────────────────────────────

_upload_lock = asyncio.Lock()

# ─────────────────────────────────────────────────────────────
# 3. ARIA2 RPC CLIENT
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
            f"Cannot connect to Aria2: {ARIA2_URL}\n"
            "Make sure `aria2c --enable-rpc` is running."
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
        _rpc("tellStopped", [0, 1000, ["gid"]]),
    )
    return {d["gid"] for d in (active + waiting + stopped)}

async def aria2_status(gid: str) -> Aria2Status:
    return _parse(await _rpc("tellStatus", [gid]))

# ─────────────────────────────────────────────────────────────
# 4. UTILS
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

def delete_file(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
            log.info("🗑️  Deleted temp file: %s", path)
    except Exception as e:
        log.warning("Could not delete file %s: %s", path, e)

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Error #400141 — account rate limited / needs rotation
ERROR_400141_RE = re.compile(r"Error\s*#?400141", re.IGNORECASE)

def extract_links(text: str) -> list[str]:
    if not text:
        return []
    return URL_RE.findall(text)

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts"}

# ─────────────────────────────────────────────────────────────
# 5. DOWNLOADER  (with account rotation on 400141)
# ─────────────────────────────────────────────────────────────

@dataclass
class DownloadResult:
    gid:        str
    file_path:  str
    file_name:  str
    total_size: int


class AccountRotationError(Exception):
    """Raised when all accounts fail with error 400141."""
    pass


async def _run_tb(share_url: str, account: str) -> str:
    """
    Run tb-getdl-share for a given account.
    Returns combined stderr output.
    Raises RuntimeError on non-zero exit.
    Special: raises ValueError with '400141' if that specific error is detected,
             so the caller can rotate accounts.
    """
    cmd = ["tb-getdl-share", "-a", account, "-s", share_url]
    log.info("Running: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    combined = (out + "\n" + err).strip()

    if out.strip(): log.info("[tb stdout] %s", out.strip())
    if err.strip(): log.warning("[tb stderr] %s", err.strip())

    if proc.returncode not in (0, None):
        # Check for error 400141 specifically
        if ERROR_400141_RE.search(combined):
            raise ValueError(f"400141: Account '{account}' hit error #400141")
        raise RuntimeError(
            f"tb-getdl-share failed (exit {proc.returncode}) with account '{account}'"
            + (f"\n{err.strip()}" if err.strip() else "")
        )

    return combined


async def _find_new_gid(before: set, timeout: float = 60.0) -> str:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        current = await aria2_all_gids()
        new = current - before
        if new:
            gid = next(iter(new))
            log.info("New GID found: %s", gid)
            return gid
    raise RuntimeError(
        "No new download appeared in Aria2!\n"
        "• The link may be invalid or expired\n"
        "• Check your account config"
    )


async def download_with_rotation(
    share_url: str,
    on_progress: Optional[Callable[[Aria2Status], Awaitable[None]]] = None,
    on_account_switch: Optional[Callable[[str, str, int, int], Awaitable[None]]] = None,
) -> DownloadResult:
    """
    Try each TB account in order.
    Only rotates on error #400141 — other errors raise immediately.
    on_account_switch(old_account, new_account, attempt, total) callback for UI updates.
    """
    accounts = TB_ACCOUNTS
    if not accounts:
        raise RuntimeError("No TB accounts configured! Set TB_ACCOUNTS env variable.")

    last_error = None

    for attempt, account in enumerate(accounts, 1):
        before = await aria2_all_gids()
        log.info("Trying account '%s' (%d/%d) for: %s", account, attempt, len(accounts), share_url)

        try:
            await _run_tb(share_url, account)
        except ValueError as e:
            # Error 400141 — rotate to next account
            last_error = str(e)
            log.warning("Account '%s' got error 400141, rotating... (%d/%d)", account, attempt, len(accounts))

            if attempt < len(accounts):
                next_account = accounts[attempt]  # attempt is 1-based, so this is next index
                if on_account_switch:
                    try:
                        await on_account_switch(account, next_account, attempt, len(accounts))
                    except Exception:
                        pass
                continue  # try next account
            else:
                raise AccountRotationError(
                    f"All {len(accounts)} account(s) failed with error #400141.\n"
                    f"Please try again later or add more accounts."
                )
        except RuntimeError:
            # Any other error — don't rotate, raise immediately
            raise

        # tb-getdl-share succeeded — now wait for GID and download
        gid = await _find_new_gid(before)

        last_cb = 0.0
        while True:
            s = await aria2_status(gid)
            now = asyncio.get_event_loop().time()

            if on_progress and (now - last_cb) >= PROGRESS_INTERVAL:
                try:
                    await on_progress(s)
                except Exception as e:
                    log.warning("Progress callback error: %s", e)
                last_cb = now

            log.info("GID=%s | %s | %.1f%% | %s | %s",
                     gid, s.status, s.percent,
                     fmt_bytes(s.completed_length), fmt_speed(s.download_speed))

            if s.status == "complete":
                if not s.files:
                    raise RuntimeError("Aria2 did not return a file path!")
                path = s.files[0].path
                log.info("Download complete via account '%s': %s", account, path)
                return DownloadResult(
                    gid=gid,
                    file_path=path,
                    file_name=path.split("/")[-1],
                    total_size=s.total_length,
                )
            if s.status == "error":
                raise RuntimeError(f"Aria2 download error: {s.error_message or 'Unknown error'}")
            if s.status == "removed":
                raise RuntimeError("Download was removed from Aria2!")

            await asyncio.sleep(2)

    raise AccountRotationError(f"All accounts exhausted. Last error: {last_error}")

# ─────────────────────────────────────────────────────────────
# 6. CORE PROCESSOR
# ─────────────────────────────────────────────────────────────

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
            log.error("Edit error: %s", e)


async def process_single_link(
    client: Client,
    original_message: Message,
    status_msg: Message,
    link: str,
    link_index: int,
    total_links: int,
) -> bool:
    prefix = f"**[{link_index}/{total_links}]** " if total_links > 1 else ""

    # Check Aria2
    try:
        await aria2_ping()
    except Exception as e:
        await safe_edit(status_msg,
            f"{prefix}❌ **Cannot connect to Aria2!**\n\n"
            f"`{e}`\n\n"
            f"Please try again later."
        )
        return False

    await safe_edit(status_msg,
        f"{prefix}📡 **Processing link...**\n"
        f"`{link[:60]}{'...' if len(link) > 60 else ''}`"
    )

    # Progress callback
    async def on_progress(s: Aria2Status):
        bar = progress_bar(s.percent)
        eta = eta_str(s.completed_length, s.total_length, s.download_speed)
        await safe_edit(status_msg,
            f"{prefix}⬇️ **Downloading...**\n\n"
            f"`{bar}` {s.percent:.1f}%\n\n"
            f"📦 `{fmt_bytes(s.completed_length)}` / `{fmt_bytes(s.total_length)}`\n"
            f"⚡ Speed: `{fmt_speed(s.download_speed)}`\n"
            f"⏱ ETA: `{eta}`\n\n"
            f"_GID: {s.gid}_"
        )

    # Account switch callback
    async def on_account_switch(old: str, new: str, attempt: int, total: int):
        await safe_edit(status_msg,
            f"{prefix}🔄 **Switching account...**\n\n"
            f"Account `{old}` hit error #400141\n"
            f"Trying account `{new}` ({attempt + 1}/{total})..."
        )

    # Download (with account rotation)
    result = None
    try:
        result = await download_with_rotation(link, on_progress, on_account_switch)
    except AccountRotationError as e:
        log.error("All accounts failed for %s: %s", link, e)
        await safe_edit(status_msg,
            f"{prefix}❌ **All Accounts Failed**\n\n"
            f"`{e}`\n\n"
            f"Please try again later."
        )
        return False
    except RuntimeError as e:
        log.error("Download failed for %s: %s", link, e)
        await safe_edit(status_msg,
            f"{prefix}❌ **Download Failed**\n\n"
            f"**Reason:** `{e}`\n\n"
            f"**Link:** `{link[:80]}`\n\n"
            f"Please check if the link is valid and try again."
        )
        return False
    except Exception as e:
        log.exception("Unexpected error downloading %s", link)
        await safe_edit(status_msg,
            f"{prefix}❌ **Unexpected Error**\n\n"
            f"`{type(e).__name__}: {e}`"
        )
        return False

    # Upload to channel — sequential lock (one at a time)
    await safe_edit(status_msg,
        f"{prefix}✅ **Download Complete!**\n\n"
        f"📁 `{result.file_name}`\n"
        f"📦 `{fmt_bytes(result.total_size)}`\n\n"
        f"⏳ Waiting for upload slot..."
    )

    channel_msg = None
    async with _upload_lock:
        await safe_edit(status_msg,
            f"{prefix}📤 **Uploading to channel...**\n\n"
            f"📁 `{result.file_name}`\n"
            f"📦 `{fmt_bytes(result.total_size)}`"
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
            log.exception("Upload failed for %s", result.file_name)
            await safe_edit(status_msg,
                f"{prefix}✅ Downloaded\n"
                f"❌ **Upload Failed**\n\n"
                f"**Error:** `{e}`\n\n"
                f"**File:** `{result.file_name}`"
            )
            delete_file(result.file_path)
            return False
        finally:
            delete_file(result.file_path)

    # Copy to user
    await safe_edit(status_msg,
        f"{prefix}✅ **Done!**\n"
        f"📁 `{result.file_name}`\n\n"
        f"File incoming 👇"
    )
    try:
        await client.copy_message(
            original_message.chat.id,
            CHANNEL_ID,
            channel_msg.id,
            reply_to_message_id=original_message.id,
        )
        log.info("Delivered to user %d: %s", original_message.from_user.id, result.file_name)
    except Exception as e:
        log.exception("Copy failed for %s", result.file_name)
        await original_message.reply_text(
            f"❌ **Could not deliver file**\n\n`{e}`",
            reply_to_message_id=original_message.id,
        )
        return False

    return True


async def handle_links(client: Client, message: Message, links: list[str]):
    user = message.from_user
    log.info("User %s (%d) sent %d link(s)", user.username or user.first_name, user.id, len(links))

    if not links:
        return

    status_msg = await message.reply_text(
        f"🔍 Found **{len(links)}** link(s). Starting...",
        reply_to_message_id=message.id,
        disable_web_page_preview=True,
    )

    success = 0
    failed  = 0

    for i, link in enumerate(links, 1):
        ok = await process_single_link(client, message, status_msg, link, i, len(links))
        if ok:
            success += 1
        else:
            failed += 1

        if i < len(links):
            await asyncio.sleep(2)

    if len(links) > 1:
        summary  = f"✅ **All done!**\n\n"
        summary += f"✅ Success: **{success}**\n"
        if failed:
            summary += f"❌ Failed: **{failed}**\n"
        summary += f"📊 Total: **{len(links)}**"
        await safe_edit(status_msg, summary)
    else:
        try:
            await status_msg.delete()
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────
# 7. BOT HANDLERS
# ─────────────────────────────────────────────────────────────

def register(app: Client):

    @app.on_message(filters.command("start") & filters.private)
    async def cmd_start(_, message: Message):
        accounts_info = f"`{'`, `'.join(TB_ACCOUNTS)}`" if TB_ACCOUNTS else "_none configured_"
        await message.reply_text(
            "🤖 **TeraBox Downloader Bot**\n\n"
            "Send any TeraBox / cloud share link!\n\n"
            "**Supported:**\n"
            "➤ Text messages with links\n"
            "➤ Media with links in caption\n"
            "➤ Multiple links — all processed!\n"
            "➤ Auto account rotation on errors\n\n"
            f"**Active accounts:** {accounts_info}\n\n"
            "**Example:**\n"
            "`https://terabox.com/s/xxxxxxxxxx`\n\n"
            "/status — Check Aria2 & accounts",
            reply_to_message_id=message.id,
        )

    @app.on_message(filters.command("status") & filters.private)
    async def cmd_status(_, message: Message):
        try:
            ver  = await aria2_ping()
            stat = await aria2_global_stat()
            accounts_info = "\n".join(
                f"  `{i+1}.` `{a}`" for i, a in enumerate(TB_ACCOUNTS)
            ) or "  _none configured_"
            await message.reply_text(
                f"✅ **Aria2 Online**\n"
                f"Version: `{ver['version']}`\n\n"
                f"🔄 Active: `{stat['numActive']}`\n"
                f"⏳ Waiting: `{stat['numWaiting']}`\n"
                f"⚡ Speed: `{fmt_speed(int(stat['downloadSpeed']))}`\n\n"
                f"**Configured Accounts ({len(TB_ACCOUNTS)}):**\n{accounts_info}",
                reply_to_message_id=message.id,
            )
        except Exception as e:
            await message.reply_text(
                f"❌ **Aria2 is offline!**\n\n`{e}`",
                reply_to_message_id=message.id,
            )

    @app.on_message(filters.text & filters.private & ~filters.command(["start", "status"]))
    async def on_text(client: Client, message: Message):
        links = extract_links(message.text or "")
        if not links:
            await message.reply_text(
                "❌ **No valid URL found.**\n\n"
                "Please send a direct download link.\n"
                "Example: `https://terabox.com/s/...`",
                reply_to_message_id=message.id,
            )
            return
        await handle_links(client, message, links)

    @app.on_message(
        filters.private &
        (filters.photo | filters.video | filters.document | filters.audio) &
        ~filters.command(["start", "status"])
    )
    async def on_media(client: Client, message: Message):
        caption = message.caption or ""
        links = extract_links(caption)
        if not links:
            await message.reply_text(
                "❌ **No URL found in caption.**\n\n"
                "Please include a download link in the media caption.",
                reply_to_message_id=message.id,
            )
            return
        await handle_links(client, message, links)

# ─────────────────────────────────────────────────────────────
# 8. MAIN
# ─────────────────────────────────────────────────────────────

async def main():
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("🤖 TeraBox Bot starting...")
    log.info("TB_ACCOUNTS : %s", TB_ACCOUNTS)
    log.info("CHANNEL_ID  : %d", CHANNEL_ID)
    log.info("ARIA2_URL   : %s", ARIA2_URL)

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
        log.info("Bot shutting down...")
