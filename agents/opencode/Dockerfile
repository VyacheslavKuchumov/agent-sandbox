# opencode with its free Zen models only: no broker, no Anthropic/OpenAI keys.
FROM agent-sandbox/agent-base:local

ARG OPENCODE_VERSION=1.18.35

USER root
# --prefix: the base image points npm's global prefix at the (volume-mounted) home.
# HOME=/tmp: keep root-owned npm/opencode files out of the agent's home.
RUN HOME=/tmp/build npm install -g --prefix /usr/local opencode-ai@${OPENCODE_VERSION} \
 && rm -rf /tmp/build
COPY opencode.json /etc/opencode/opencode.json
USER agent

ENV OPENCODE_CONFIG=/etc/opencode/opencode.json
