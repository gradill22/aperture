-- Runs after the image's own 000_install_timescaledb.sh / 001_timescaledb_tune.sh.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
