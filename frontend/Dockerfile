# Edge image: the built SPA + fonts/sprites + provenance on nginx, proxying /api, /tiles, /mcp.
# Builds with --network=none: node_modules come from aperture/node-deps (bootstrap).
#
#   docker build --network none --target check -f frontend/Dockerfile .   lint + typecheck + unit tests
ARG NODE_DEPS=aperture/node-deps:latest

FROM ${NODE_DEPS} AS source
COPY data/aoi.json /src/data/aoi.json
COPY frontend/ /src/frontend/

FROM source AS check
RUN npm run check

FROM source AS build
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.5-alpine@sha256:4714e0b1b2577eaa1a6131d07c958b67f0eb68e6d0521e90c6e5287db8cf0bc5
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/frontend/dist /usr/share/nginx/html
COPY data/tiles/fonts /usr/share/nginx/html/fonts
COPY data/tiles/sprites /usr/share/nginx/html/sprites
COPY data/snapshot/MANIFEST.json /usr/share/nginx/html/provenance.json
EXPOSE 8080
