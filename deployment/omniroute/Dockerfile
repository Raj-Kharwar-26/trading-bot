# OmniRoute LLM gateway — container image
# Runs the OmniRoute AI gateway (npm package) so the trading bot has an
# always-on LLM router on the same host platform as the bot + DBs.
#
# Persistence:
#   - OmniRoute stores providers/credentials + call logs under DATA_DIR.
#   - Mount a volume at /root/.omniroute (or set DATA_DIR) and set
#     STORAGE_ENCRYPTION_KEY to the same key used locally so your existing
#     provider configuration (OpenRouter) is preserved across deploys.

FROM node:20-alpine

# OmniRoute needs a few common tools; keep it minimal.
RUN apk add --no-cache curl tzdata

# Install OmniRoute globally from npm.
ARG OMNROUTE_VERSION=3.8.49
RUN npm install -g omniroute@${OMNROUTE_VERSION} && npm cache clean --force

# Use a non-root runtime user to match PaaS best practices.
RUN addgroup -S omniroute && adduser -S -G omniroute omniroute

# Persistent data location (providers, storage.sqlite, call logs).
ENV DATA_DIR=/root/.omniroute \
    PORT=20128 \
    NODE_ENV=production

# Expose the gateway port.
EXPOSE 20128

WORKDIR /home/omniroute

# OmniRoute must be able to read/write its data dir.
RUN mkdir -p ${DATA_DIR} && chown -R omniroute:omniroute /home/omniroute ${DATA_DIR}

USER omniroute

# Serve in the foreground (no browser). Port comes from PORT/--port.
# Do NOT use --daemon here — the platform manages the process lifecycle.
CMD ["omniroute", "serve", "--no-open", "--port", "20128"]