# The reviewer image of an agent review (container.py, beside this folder): the two agent CLIs at pinned
# versions, Python for the debugger (`python3 -m qccd.debug`, standard library only: the website
# runs the same code in the browser), `script` for a sign-in's terminal, and the egress proxy.
# Built on the person's own computer the first time a review or a sign-in needs it; its tag is
# the digest of this folder, so a change here is a new image.
FROM node:22-bookworm-slim

ARG CLAUDE_CODE_VERSION=2.1.322
ARG CODEX_VERSION=0.167.2

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 ca-certificates ripgrep bsdutils \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" "@openai/codex@${CODEX_VERSION}" \
 && npm cache clean --force

COPY egress_proxy.py /opt/qccd-review/egress_proxy.py

ENV DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /job
