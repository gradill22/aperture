# aperture/py-deps: every third-party Python dependency from uv.lock in /opt/venv.
# Built once in bootstrap (networked); app images are FROM this and only copy source,
# so they build with --network=none.
FROM ghcr.io/astral-sh/uv@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 AS uv

FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY data/pyproject.toml data/
COPY db/loader/pyproject.toml db/loader/
COPY backend/pyproject.toml backend/
RUN uv sync --frozen --no-dev --no-install-workspace --all-packages \
    && rm -rf /root/.cache /src
ARG LOCK_SHA256
LABEL aperture.lock-sha256=$LOCK_SHA256
RUN useradd --uid 10001 --no-create-home app
WORKDIR /app
