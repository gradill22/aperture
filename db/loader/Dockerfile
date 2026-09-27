# One-shot loader with the snapshot baked in (no bind mounts: same image runs in k8s).
ARG PY_DEPS=aperture/py-deps:latest
FROM ${PY_DEPS}
COPY data/snapshot/adsb/2026-09-24 /data/adsb
COPY data/snapshot/osm /data/osm
COPY db/loader/aperture_loader /app/aperture_loader
ENV PYTHONPATH=/app ADSB_DIR=/data/adsb OSM_DIR=/data/osm
USER app
CMD ["python", "-m", "aperture_loader.load"]
