# aperture/node-deps: frontend/node_modules from package-lock.json. Built once in bootstrap
# (networked); frontend/Dockerfile is FROM this and builds with --network=none.
FROM node:24-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci && npm cache clean --force
ARG LOCK_SHA256
LABEL aperture.lock-sha256=$LOCK_SHA256
