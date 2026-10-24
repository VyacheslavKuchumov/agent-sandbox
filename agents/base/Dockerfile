FROM node:24.21.0-trixie-slim

ARG CLAUDE_CODE_VERSION=2.1.296
ARG CODEX_VERSION=0.162.1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git curl less procps jq unzip tini \
      ripgrep fd-find python3 python3-venv build-essential \
      docker-cli docker-compose \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} @openai/codex@${CODEX_VERSION} \
 && npm cache clean --force

# Broker CA (public cert only): lets the broker swap placeholder tokens.
COPY sbx-ca.crt /usr/local/share/ca-certificates/sbx-ca.crt
RUN update-ca-certificates

COPY gitconfig /etc/gitconfig

# The node image's uid 1000 user becomes "agent" (same uid as the host user).
RUN usermod -l agent -d /home/agent -m node && groupmod -n agent node

ENV HOME=/home/agent \
    PATH=/home/agent/.local/bin:$PATH \
    NPM_CONFIG_PREFIX=/home/agent/.local \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    DISABLE_AUTOUPDATER=1

USER agent
WORKDIR /home/agent
ENTRYPOINT ["tini", "--"]
CMD ["sleep", "infinity"]
