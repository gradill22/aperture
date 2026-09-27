# aperture/llm-relay: stdlib-only TCP forwarder to LM Studio (no third-party packages).
FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
RUN useradd --uid 10001 --no-create-home app
COPY relay/llm_relay.py /app/llm_relay.py
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 1234
CMD ["python", "/app/llm_relay.py"]
