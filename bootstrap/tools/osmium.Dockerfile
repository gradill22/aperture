# Bootstrap-only tool image (networked build): osmium-tool for clipping/filtering OSM extracts.
FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends osmium-tool \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
ENTRYPOINT ["osmium"]
