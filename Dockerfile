# ─────────────────────────────────────────────────────────────
# Stage 1: Node.js — terabox-node GitHub se clone karke build karo
# ─────────────────────────────────────────────────────────────
FROM node:20-slim AS node_builder

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# pnpm install karo
RUN npm install -g pnpm

# terabox-node repo clone karo (aapka fork)
WORKDIR /terabox-node
RUN git clone --depth 1 https://github.com/nathdadu26/terabox-node.git .

# Dependencies install karo
RUN pnpm install --frozen-lockfile || pnpm install

# Global link karo taaki tb-* commands available ho jaaye
RUN pnpm link --global

# ─────────────────────────────────────────────────────────────
# Stage 2: Final image — Python + Aria2 + Node tools
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    nodejs \
    npm \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# pnpm install karo
RUN npm install -g pnpm

# terabox-node source copy karo Stage 1 se
COPY --from=node_builder /terabox-node /opt/terabox-node

# node_modules bhi copy karo (Stage 1 mein install kiye the)
COPY --from=node_builder /terabox-node/node_modules /opt/terabox-node/node_modules

# Global link Stage 1 se copy karo
COPY --from=node_builder /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node_builder /usr/local/bin /usr/local/bin/node_bins

# tb-* binaries ko PATH mein add karo
RUN cp /usr/local/bin/node_bins/tb-* /usr/local/bin/ 2>/dev/null || true
RUN rm -rf /usr/local/bin/node_bins

# Fallback: agar pnpm link se nahi aaya to manually link karo
WORKDIR /opt/terabox-node
RUN pnpm link --global || true

# Download directory banao
RUN mkdir -p /downloads && chmod 777 /downloads

# tb config directory — .config.yaml yahaan hona chahiye
RUN mkdir -p /root/.config/tb-getdl-share

# Working directory
WORKDIR /app

# Python dependencies install karo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bot files copy karo
COPY bot.py .
COPY health_check.py .
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
