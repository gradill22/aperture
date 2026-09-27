# Vector tiles: the snapshot pmtiles baked into martin. Served at /tiles/{basemap,infrastructure}/{z}/{x}/{y}.
FROM ghcr.io/maplibre/martin:1.16.1@sha256:59902019bf9038926ff0c71174237d6852e64c457830a6349abe7090be8818ca
COPY data/tiles/basemap.pmtiles data/tiles/infrastructure.pmtiles /data/
USER 10001
EXPOSE 3000
ENTRYPOINT ["martin"]
CMD ["--listen-addresses", "0.0.0.0:3000", "--route-prefix", "/tiles", "/data/basemap.pmtiles", "/data/infrastructure.pmtiles"]
