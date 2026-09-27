"""Table definitions mirroring db/init/110_schema.sql (the SQL files own the DDL)."""

from geoalchemy2 import Geometry
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

osm_feature = Table(
    "osm_feature",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("layer", Text, nullable=False),
    Column("osm_type", Text, nullable=False),
    Column("osm_id", BigInteger, nullable=False),
    Column("name", Text),
    Column("kind", Text, nullable=False),
    Column("tags", JSONB, nullable=False),
    Column("geom", Geometry(srid=4326, spatial_index=False), nullable=False),
)

aircraft = Table(
    "aircraft",
    metadata,
    Column("icao24", Text, primary_key=True),
    Column("registration", Text),
    Column("type_code", Text),
    Column("description", Text),
    Column("owner_operator", Text),
    Column("year", Text),
    Column("category", Text),
    Column("db_flags", Integer),
    Column("military", Boolean),
    Column("interesting", Boolean),
    Column("pia", Boolean),
    Column("ladd", Boolean),
    Column("n_points", Integer),
    Column("n_points_in_aoi", Integer),
    Column("callsigns", ARRAY(Text)),
)

flight = Table(
    "flight",
    metadata,
    Column("icao24", Text, primary_key=True),
    Column("leg", Integer, primary_key=True),
    Column("start_ts", DateTime(timezone=True)),
    Column("end_ts", DateTime(timezone=True)),
    Column("n_points", Integer),
    Column("callsign", Text),
    Column("geom", Geometry(srid=4326, spatial_index=False)),
)
