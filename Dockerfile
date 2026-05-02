# ─────────────────────────────────────────────────────────────
# Stage 1: Node.js — tb-getdl-share install karo
# ─────────────────────────────────────────────────────────────
FROM node:20-slim AS node_builder

RUN npm install -g tb-getdl-share

# ─────────────────────────────────────────────────────────────
# Stage 2: Final image — Python + Aria2 + Node tools
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    nodejs \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node global binaries copy karo Stage 1 se
COPY --from=node_builder /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node_builder /usr/local/bin/tb-getdl-share /usr/local/bin/tb-getdl-share

# Download directory banao
RUN mkdir -p /downloads && chmod 777 /downloads

# Working directory
WORKDIR /app

# Python dependencies pehle install karo (cache ke liye)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bot files copy karo
COPY bot.py .
COPY health_check.py .

# tb-getdl-share config directory
RUN mkdir -p /root/.config/tb-getdl-share

# Aria2 config
RUN mkdir -p /root/.config/aria2
RUN echo "enable-rpc=true" > /root/.config/aria2/aria2.conf && \
    echo "rpc-listen-all=true" >> /root/.config/aria2/aria2.conf && \
    echo "rpc-allow-origin-all=true" >> /root/.config/aria2/aria2.conf && \
    echo "dir=/downloads" >> /root/.config/aria2/aria2.conf && \
    echo "max-concurrent-downloads=5" >> /root/.config/aria2/aria2.conf && \
    echo "continue=true" >> /root/.config/aria2/aria2.conf && \
    echo "max-connection-per-server=16" >> /root/.config/aria2/aria2.conf && \
    echo "min-split-size=1M" >> /root/.config/aria2/aria2.conf && \
    echo "split=16" >> /root/.config/aria2/aria2.conf

# Startup script
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
