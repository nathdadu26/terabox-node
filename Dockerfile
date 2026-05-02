# ─────────────────────────────────────────────────────────────
# Stage 1: Node.js — terabox-node GitHub se clone karke build karo
# ─────────────────────────────────────────────────────────────
FROM node:20-slim AS node_builder

ENV PNPM_HOME="/root/.local/share/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# pnpm install + setup karo
RUN npm install -g pnpm && pnpm setup || true

WORKDIR /terabox-node
RUN GIT_SSL_NO_VERIFY=1 git clone --depth 1 https://github.com/nathdadu26/terabox-node.git .

RUN pnpm install --frozen-lockfile || pnpm install

# Global link karo — PNPM_HOME set hone ke baad kaam karega
RUN pnpm link --global

# ─────────────────────────────────────────────────────────────
# Stage 2: Final image — Python + Aria2 + Node tools
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

ENV PNPM_HOME="/root/.local/share/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    aria2 \
    nodejs \
    npm \
    curl \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g pnpm && pnpm setup || true

# terabox-node copy karo Stage 1 se
COPY --from=node_builder /terabox-node /opt/terabox-node

# Stage 1 ka PNPM_HOME copy karo (global binaries yahaan hote hain)
COPY --from=node_builder /root/.local/share/pnpm /root/.local/share/pnpm

# Fallback: phir se link karo final image mein
WORKDIR /opt/terabox-node
RUN pnpm link --global || true

# tb-* commands verify karo
RUN which tb-getdl-share || echo "WARNING: tb-getdl-share not in PATH"

# Download directory
RUN mkdir -p /downloads && chmod 777 /downloads
RUN mkdir -p /root/.config/tb-getdl-share

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY health_check.py .
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
