# ─────────────────────────────────────────────────────────────
# Stage 1: Node.js 22 — terabox-node build karo
# ─────────────────────────────────────────────────────────────
FROM node:22-slim AS node_builder

ENV PNPM_HOME="/root/.local/share/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g pnpm && pnpm setup || true

WORKDIR /terabox-node
RUN GIT_SSL_NO_VERIFY=1 git clone --depth 1 https://github.com/nathdadu26/terabox-node.git .

RUN pnpm install --frozen-lockfile || pnpm install

RUN pnpm link --global

# ─────────────────────────────────────────────────────────────
# Stage 2: Final image
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

ENV PNPM_HOME="/root/.local/share/pnpm"
ENV PATH="$PNPM_HOME:/usr/local/bin:$PATH"

# Node.js 22 install karo (NodeSource se — apt ka Node 20 nahi chalega)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    aria2 \
    && update-ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Node version verify karo
RUN node --version

RUN npm install -g pnpm && pnpm setup || true

# terabox-node copy karo Stage 1 se
COPY --from=node_builder /terabox-node /opt/terabox-node
COPY --from=node_builder /root/.local/share/pnpm /root/.local/share/pnpm

# Fallback link
WORKDIR /opt/terabox-node
RUN pnpm link --global || true

# Verify
RUN which tb-getdl-share && echo "✅ tb-getdl-share found" || echo "❌ tb-getdl-share missing"
RUN tb-getdl-share --version 2>/dev/null || true

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
