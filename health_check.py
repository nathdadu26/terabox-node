# health_check.py
# Kaam:
#   1. Port 8000 pe HTTP server chalao (Koyeb health check ke liye)
#   2. Har 20 minute mein apne aap ko ping karo (sleep mode se bachne ke liye)

import asyncio
import logging
import os
from aiohttp import web, ClientSession, ClientTimeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | [HealthCheck] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

PORT          = int(os.getenv("PORT", "8000"))
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "1200"))  # 20 minutes = 1200 seconds
# Koyeb app URL — env se lo, warna localhost
APP_URL       = os.getenv("APP_URL", f"http://localhost:{PORT}")

# ─────────────────────────────────────────────────────────────
# HTTP Handlers
# ─────────────────────────────────────────────────────────────

async def handle_health(request: web.Request) -> web.Response:
    """Koyeb is endpoint ko ping karta hai yeh check karne ke liye ki app zinda hai."""
    return web.json_response({
        "status": "ok",
        "service": "TeraBox Bot",
        "message": "Bot is running! 🤖"
    })

async def handle_root(request: web.Request) -> web.Response:
    return web.Response(
        text="🤖 TeraBox Bot is alive!",
        content_type="text/plain"
    )

# ─────────────────────────────────────────────────────────────
# Self-Ping Loop (Sleep Mode se bachao)
# ─────────────────────────────────────────────────────────────

async def self_ping_loop():
    """Har 20 minute mein apne aap ko ping karo taaki Koyeb sleep na kare."""
    # Pehle thoda wait karo server ke start hone ke liye
    await asyncio.sleep(30)

    ping_url = f"{APP_URL}/health"
    log.info("Self-ping loop shuru: har %d seconds mein %s ko ping karunga", PING_INTERVAL, ping_url)

    while True:
        try:
            async with ClientSession() as session:
                async with session.get(
                    ping_url,
                    timeout=ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        log.info("✅ Self-ping successful (status: %d)", resp.status)
                    else:
                        log.warning("⚠️ Self-ping unexpected status: %d", resp.status)
        except Exception as e:
            log.error("❌ Self-ping failed: %s", e)

        await asyncio.sleep(PING_INTERVAL)

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

async def main():
    # Web app setup
    app = web.Application()
    app.router.add_get("/",        handle_root)
    app.router.add_get("/health",  handle_health)
    app.router.add_get("/ping",    handle_health)  # extra alias

    # Runner
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info("✅ Health server chalu: http://0.0.0.0:%d", PORT)
    log.info("   Endpoints: /health, /ping, /")

    # Self-ping loop parallel mein chalao
    await self_ping_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Health server band ho raha hai...")
