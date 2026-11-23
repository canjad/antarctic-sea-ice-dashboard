"""Canonical NSIDC southern 25 km grid on the Hughes 1980 ellipsoid."""

from __future__ import annotations

from pyproj import CRS, Geod, Transformer

MAJOR_AXIS = 6378273.0
MINOR_AXIS = 6356889.449
CELL_SIZE = 25000.0
GRID_COLUMNS = 316
GRID_ROWS = 332
FIRST_X = -3937500.0
FIRST_Y = 4337500.0
PROJECTED_CRS = CRS.from_proj4(
    f"+proj=stere +lat_0=-90 +lat_ts=-70 +lon_0=0 "
    f"+a={MAJOR_AXIS} +b={MINOR_AXIS} +units=m +no_defs"
)
GEOGRAPHIC_CRS = CRS.from_proj4(
    f"+proj=longlat +a={MAJOR_AXIS} +b={MINOR_AXIS} +no_defs"
)
FORWARD = Transformer.from_crs(GEOGRAPHIC_CRS, PROJECTED_CRS, always_xy=True)
INVERSE = Transformer.from_crs(PROJECTED_CRS, GEOGRAPHIC_CRS, always_xy=True)
GEOD = Geod(a=MAJOR_AXIS, b=MINOR_AXIS)
