# Playwright runner. Builds with --network=none on top of aperture/e2e-deps (bootstrap).
ARG E2E_DEPS=aperture/e2e-deps:latest
FROM ${E2E_DEPS}
COPY e2e/playwright.config.ts /e2e/
COPY e2e/tests /e2e/tests
ENV E2E_OUT=/out CI=1
USER pwuser
CMD ["npx", "playwright", "test"]
