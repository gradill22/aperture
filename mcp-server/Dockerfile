ARG PY_DEPS=aperture/py-deps:latest
FROM ${PY_DEPS}
COPY backend/aperture_api /app/aperture_api
COPY mcp-server/aperture_mcp /app/aperture_mcp
ENV PYTHONPATH=/app
USER app
EXPOSE 8001
CMD ["python", "-m", "aperture_mcp.server", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8001"]
