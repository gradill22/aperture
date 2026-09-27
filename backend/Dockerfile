ARG PY_DEPS=aperture/py-deps:latest
FROM ${PY_DEPS}
COPY backend/aperture_api /app/aperture_api
ENV PYTHONPATH=/app
USER app
EXPOSE 8000
CMD ["uvicorn", "aperture_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
