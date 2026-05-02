#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Starting TeraBox Bot..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Aria2 RPC secret set karo
ARIA2_SECRET=${ARIA2_SECRET:-""}

# Aria2 start karo background mein
echo "▶ Starting Aria2..."
aria2c \
  --enable-rpc \
  --rpc-listen-all=true \
  --rpc-allow-origin-all=true \
  --rpc-secret="${ARIA2_SECRET}" \
  --dir=/downloads \
  --max-concurrent-downloads=5 \
  --continue=true \
  --max-connection-per-server=16 \
  --min-split-size=1M \
  --split=16 \
  --daemon=true \
  --log=/app/aria2.log \
  --log-level=warn

# Aria2 ke start hone ka wait karo
sleep 2
echo "✅ Aria2 started"

# Health check server start karo background mein
echo "▶ Starting Health Check server on port 8000..."
python health_check.py &
HEALTH_PID=$!
echo "✅ Health server PID: $HEALTH_PID"

# tb-getdl-share config check
echo "▶ TB Account: ${TB_ACCOUNT:-not set}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "▶ Starting Telegram Bot..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Bot start karo (foreground — yahi main process hai)
exec python bot.py
