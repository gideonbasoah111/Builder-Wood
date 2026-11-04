"""
Distance service for Builder Wood.

Pipeline:
1. Normalize address → check geocode_cache
2. On miss: geocode via Nominatim → route from Sokoban via OSRM → store in cache
3. Return distance_km + display_name

Rate limits respected:
- Nominatim: 1 req/sec, descriptive User-Agent required [citation:4][citation:11]
- OSRM demo: no hard limit but be reasonable [citation:11]

Note: OSRM public demo server is for development/demo use [citation:2].
For production scale, self-host OSRM and change OSRM_BASE_URL.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple

import httpx
from sqlalchemy.orm import Session

from builder_wood.app.config import settings
from builder_wood.app import models


# Sokoban Wood Village, Kumasi — delivery origin.
SOKOBAN_LAT = 6.6833
SOKOBAN_LON = -1.6167

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# OSRM public demo server. For production, self-host OSRM:
#   docker run -p 5000:5000 osrm/osrm-backend ...
# Then change this to your server URL.
OSRM_BASE_URL = "https://router.project-osrm.org"

_last_nominatim_call: float = 0.0
_NOMINATIM_MIN_INTERVAL = 1.0  # seconds


@dataclass
class DistanceResult:
    distance_km: Decimal
    geocoded_address: str
    lat: float
    lon: float
    from_cache: bool = False


class DistanceError(Exception):
    """User-facing failure from geocoding or routing."""


def _throttle_nominatim() -> None:
    global _last_nominatim_call
    elapsed = time.monotonic() - _last_nominatim_call
    if elapsed < _NOMINATIM_MIN_INTERVAL:
        time.sleep(_NOMINATIM_MIN_INTERVAL - elapsed)
    _last_nominatim_call = time.monotonic()


def _normalize_address(address: str) -> str:
    return " ".join(address.strip().lower().split())


def _geocode(address: str) -> Tuple[float, float, str]:
    """Nominatim geocoding with 1 req/sec throttle and required User-Agent."""
    _throttle_nominatim()

    params = {"q": address, "format": "json", "limit": 1, "addressdetails": 1}
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}

    try:
        resp = httpx.get(NOMINATIM_URL, params=params, headers=headers, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise DistanceError(f"Geocoding service unavailable: {exc}") from exc

    if not data:
        raise DistanceError(
            f"Could not find '{address}'. Try adding a town or landmark."
        )

    hit = data[0]
    return float(hit["lat"]), float(hit["lon"]), hit.get("display_name", address)


def _route_km(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> Decimal:
    """OSRM routing. Coordinates in OSRM URL are lon,lat order."""
    url = (
        f"{OSRM_BASE_URL}/route/v1/driving/"
        f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    )
    params = {"overview": "false"}  # we only need distance, not geometry

    try:
        resp = httpx.get(url, params=params, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise DistanceError(
            f"Routing service error (HTTP {exc.response.status_code})."
        ) from exc
    except httpx.HTTPError as exc:
        raise DistanceError(f"Routing service unavailable: {exc}") from exc

    # OSRM response: {"code": "Ok", "routes": [{"distance": 12345.6, ...}]}
    if data.get("code") != "Ok":
        raise DistanceError(
            f"No driving route found to this address. "
            f"(OSRM: {data.get('code', 'unknown error')})"
        )

    try:
        meters = data["routes"][0]["distance"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DistanceError("Routing service returned an unexpected response.") from exc

    return Decimal(str(round(meters / 1000.0, 2)))


def estimate_distance_from_address(address: str, db: Session) -> DistanceResult:
    """Geocode + route. Uses the DB cache when possible."""
    if not address or len(address.strip()) < 3:
        raise DistanceError("Please enter a more complete delivery address.")

    key = _normalize_address(address)

    # Cache hit?
    cached = (
        db.query(models.GeocodeCache)
        .filter(models.GeocodeCache.address_key == key)
        .first()
    )
    if cached:
        return DistanceResult(
            distance_km=cached.distance_km,
            geocoded_address=cached.display_name,
            lat=float(cached.lat),
            lon=float(cached.lon),
            from_cache=True,
        )

    # Cache miss — call external APIs
    lat, lon, display = _geocode(address)
    km = _route_km(SOKOBAN_LAT, SOKOBAN_LON, lat, lon)

    # Store in cache (best-effort; don't fail the request if cache write fails)
    try:
        db.add(models.GeocodeCache(
            address_key=key,
            lat=Decimal(str(lat)),
            lon=Decimal(str(lon)),
            display_name=display[:255],
            distance_km=km,
        ))
        db.commit()
    except Exception:
        db.rollback()  # concurrent insert — harmless

    return DistanceResult(
        distance_km=km,
        geocoded_address=display,
        lat=lat,
        lon=lon,
        from_cache=False,
    )