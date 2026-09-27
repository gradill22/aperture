# aperture/ci-node: job image for the Gitea CI `web` job (eslint, tsc, vitest). The frontend's
# node_modules come from aperture/node-deps; this adds git for ci-checkout. Built in bootstrap.
ARG NODE_DEPS=aperture/node-deps:latest
FROM ${NODE_DEPS}
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY bootstrap/deps/ci-checkout.sh /usr/local/bin/ci-checkout
ARG LOCK_SHA256
LABEL aperture.lock-sha256=$LOCK_SHA256
WORKDIR /workspace
