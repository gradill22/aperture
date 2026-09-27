# aperture/e2e-deps: Playwright's browsers (from the pinned image) + @playwright/test from
# e2e/package-lock.json. Built once in bootstrap (networked); e2e/Dockerfile is FROM this.
FROM mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27
ENV NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false
WORKDIR /e2e
COPY e2e/package.json e2e/package-lock.json ./
RUN npm ci && npm cache clean --force
ARG LOCK_SHA256
LABEL aperture.lock-sha256=$LOCK_SHA256
