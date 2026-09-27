-- Static OSM infrastructure overlays (PostGIS).
CREATE TABLE osm_feature (
    id        bigserial PRIMARY KEY,
    layer     text NOT NULL CHECK (layer IN ('airports', 'ports', 'government', 'military')),
    osm_type  text NOT NULL CHECK (osm_type IN ('node', 'way', 'relation')),
    osm_id    bigint NOT NULL,
    name      text,
    kind      text NOT NULL,          -- primary tag, e.g. 'aeroway=aerodrome', 'military=bunker'
    tags      jsonb NOT NULL,
    geom      geometry(Geometry, 4326) NOT NULL,
    UNIQUE (layer, osm_type, osm_id)
);
CREATE INDEX osm_feature_geom_idx ON osm_feature USING gist (geom);
CREATE INDEX osm_feature_name_trgm_idx ON osm_feature USING gin (name gin_trgm_ops);

-- One row per aircraft seen in the snapshot (adsb.lol/readsb aircraft database fields).
CREATE TABLE aircraft (
    icao24           text PRIMARY KEY,
    registration     text,
    type_code        text,
    description      text,
    owner_operator   text,
    year             text,
    category         text,             -- ADS-B emitter category (A1..A7, B*, C*)
    db_flags         int NOT NULL DEFAULT 0,
    military         boolean NOT NULL DEFAULT false,
    interesting      boolean NOT NULL DEFAULT false,
    pia              boolean NOT NULL DEFAULT false,
    ladd             boolean NOT NULL DEFAULT false,
    n_points         int NOT NULL,
    n_points_in_aoi  int NOT NULL,
    callsigns        text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX aircraft_registration_idx ON aircraft (upper(registration));

-- ADS-B positions (Timescale hypertable).
CREATE TABLE adsb_position (
    ts           timestamptz NOT NULL,
    icao24       text NOT NULL,
    geom         geometry(Point, 4326) NOT NULL,
    alt_baro_ft  int,
    on_ground    boolean NOT NULL,
    gs_kt        real,
    track_deg    real,
    vrate_fpm    int,
    geom_alt_ft  int,
    callsign     text,
    squawk       text,
    new_leg      boolean NOT NULL,
    source       text
);
SELECT create_hypertable('adsb_position', by_range('ts', INTERVAL '1 hour'));
CREATE INDEX adsb_position_icao_ts_idx ON adsb_position (icao24, ts DESC);
CREATE INDEX adsb_position_geom_idx ON adsb_position USING gist (geom);

-- Contiguous flight legs: split on the readsb new-leg flag or a gap > 10 min.
-- LineStringM with M = epoch seconds; used to prefilter geofence/track queries.
CREATE MATERIALIZED VIEW flight AS
WITH marked AS (
    SELECT icao24, ts, geom, callsign,
           CASE WHEN new_leg
                  OR lag(ts) OVER w IS NULL
                  OR ts - lag(ts) OVER w > INTERVAL '10 minutes'
                THEN 1 ELSE 0 END AS brk
    FROM adsb_position
    WINDOW w AS (PARTITION BY icao24 ORDER BY ts)
), numbered AS (
    SELECT *, sum(brk) OVER (PARTITION BY icao24 ORDER BY ts) AS leg FROM marked
)
SELECT icao24,
       leg::int AS leg,
       min(ts) AS start_ts,
       max(ts) AS end_ts,
       count(*)::int AS n_points,
       mode() WITHIN GROUP (ORDER BY callsign) AS callsign,
       ST_SetSRID(ST_MakeLine(ST_MakePointM(ST_X(geom), ST_Y(geom), extract(epoch FROM ts)) ORDER BY ts), 4326)
           AS geom
FROM numbered
GROUP BY icao24, leg
HAVING count(*) >= 2
WITH NO DATA;
CREATE UNIQUE INDEX flight_pk ON flight (icao24, leg);
CREATE INDEX flight_geom_idx ON flight USING gist (geom);
CREATE INDEX flight_time_idx ON flight (start_ts, end_ts);

-- Written by the loader when it finishes; /health reports readiness from it.
CREATE TABLE load_info (
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    source      text NOT NULL,
    fingerprint text NOT NULL,        -- sha256 over the loaded input files
    counts      jsonb NOT NULL
);
