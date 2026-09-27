# aperture/ci-python: job image for the Gitea CI (egress-check, lint, typecheck, test, build,
# helm). Built once in bootstrap (networked); jobs then run it on the internal aperture-ci network
# with nothing left to download:
#   Python 3.14 + every locked dependency including the dev group (ruff, mypy, pytest) in /opt/venv
#   git (ci-checkout), helm, and the docker CLI + buildx + compose for the offline image builds
FROM ghcr.io/astral-sh/uv@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 AS uv
FROM docker:29.7.2-cli@sha256:3f4743208d2338c934d7b8bcfbe1bb54c0b2355c510ad5e0f31c0c4a54bd704e AS docker

FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
# Static Go binaries: they run on this Debian base as-is.
COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/
ADD --checksum=sha256:86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb \
    https://get.helm.sh/helm-v4.3.0-linux-amd64.tar.gz /tmp/helm.tgz
RUN tar -xzf /tmp/helm.tgz -C /usr/local/bin --strip-components=1 linux-amd64/helm && rm /tmp/helm.tgz

ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY data/pyproject.toml data/
COPY db/loader/pyproject.toml db/loader/
COPY backend/pyproject.toml backend/
COPY mcp-server/pyproject.toml mcp-server/
RUN uv sync --frozen --no-install-workspace --all-packages \
    && rm -rf /root/.cache /src
COPY bootstrap/deps/ci-checkout.sh /usr/local/bin/ci-checkout
# Workspace packages are used from the checkout, like the app images use their copied source.
ENV PYTHONPATH=backend:mcp-server:db/loader:relay UV_OFFLINE=1
ARG LOCK_SHA256
LABEL aperture.lock-sha256=$LOCK_SHA256
WORKDIR /workspace
