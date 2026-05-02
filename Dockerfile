# ─────────────────────────────────────────────────────────────
# Stage 1: Node.js — terabox-node GitHub se clone karke build karo
# ─────────────────────────────────────────────────────────────
FROM node:20-slim AS node_builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# pnpm install karo
RUN npm install -g pnpm

# terabox-node repo clone karo
# GIT_SSL_NO_VERIFY sirf build time ke liye — runtime pe nahi
WORKDIR /terabox-node
RUN GIT_SSL_NO_VERIFY=1 git clone --depth 1 https://github.com/nathdadu26/terabox-node.git .

# Dependencies install karo
RUN pnpm install --frozen-lockfile || pnpm install

# Global link karo
RUN pnpm link --global

# ─────────────────────────────────────────────────────────────
# Stage 2: Final image — Python + Aria2 + Node tools
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    aria2 \
    nodejs \
    npm \
    curl \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# pnpm install karo
RUN npm install -g pnpm

# terabox-node source + node_modules copy karo Stage 1 se
COPY --from=node_builder /terabox-node /opt/terabox-node

# Global binaries copy karo Stage 1 se
COPY --from=node_builder /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node_builder /usr/local/bin /usr/local/bin/node_stage1

# tb-* binaries ko PATH mein dalo
RUN find /usr/local/bin/node_stage1 -name "tb-*" -exec cp {} /usr/local/bin/ \; 2>/dev/null || true \
    && rm -rf /usr/local/bin/node_stage1

# Fallback: pnpm link dobara karo
WORKDIR /opt/terabox-node
RUN pnpm link --global || true

# Download directory
RUN mkdir -p /downloads && chmod 777 /downloads

# tb config directory
RUN mkdir -p /root/.config/tb-getdl-share

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bot files
COPY bot.py .
COPY health_check.py .
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
