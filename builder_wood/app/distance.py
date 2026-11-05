"""
Distance service for Builder Wood.

Pipeline:
1. Normalize address → check geocode_cache
2. On miss: geocode via Nominatim → route from Sokoban via OSRM → store in cache
3. Return distance_km + display_name

Rate limits respected:
- Nominatim: 1 req/sec globally, descriptive User-Agent required
- OSRM public demo: no hard limit but "be reasonable"
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Tuple

import httpx
from sqlalchemy.orm import Session

from builder_wood.app.config import settings
from builder_wood.app import models


# Sokoban Wood Village, Kumasi — delivery origin
SOKOBAN_LAT = 6.6833
SOKOBAN_LON = -1.6167

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_BASE_URL = "https://router.project-osrm.org"

# Thread-safe Nominatim throttle.
# Render runs multiple threads even on a single worker, so we need a lock.
_nominatim_lock = threading.Lock()
_last_nominatim_call: float = 0.0
_NOMINATIM_MIN_INTERVAL = 1.0  # seconds
_NOMINATIM_MAX_RETRIES = 2
_NOMINATIM_RETRY_DELAY = 3.0  # seconds, if no Retry-After header


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
    """Thread-safe throttle: at most 1 call/sec to Nominatim globally."""
    global _last_nominatim_call
    with _nominatim_lock:
        elapsed = time.monotonic() - _last_nominatim_call
        if elapsed < _NOMINATIM_MIN_INTERVAL:
            time.sleep(_NOMINATIM_MIN_INTERVAL - elapsed)
        _last_nominatim_call = time.monotonic()


def _normalize_address(address: str) -> str:
    return " ".join(address.strip().lower().split())


def _nominatim_request(address: str) -> dict:
    """One Nominatim HTTP call with proper headers. Returns parsed JSON list."""
    params = {"q": address, "format": "json", "limit": 1, "addressdetails": 1}
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}

    resp = httpx.get(
        NOMINATIM_URL, params=params, headers=headers, timeout=10.0
    )
    return resp  # caller handles status


def _geocode(address: str) -> Tuple[float, float, str]:
    """Nominatim geocoding with retries on 429 and proper 1 req/sec throttle."""
    last_error: str | None = None

    for attempt in range(_NOMINATIM_MAX_RETRIES + 1):
        _throttle_nominatim()

        try:
            resp = _nominatim_request(address)
        except httpx.HTTPError as exc:
            last_error = f"Network error: {exc}"
            if attempt < _NOMINATIM_MAX_RETRIES:
                time.sleep(_NOMINATIM_RETRY_DELAY)
                continue
            raise DistanceError(f"Geocoding service unavailable: {exc}") from exc

        # Rate limited — respect Retry-After if present
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait = (
                float(retry_after) if retry_after else _NOMINATIM_RETRY_DELAY
            )
            # Cap the wait so a request doesn't hang forever
            wait = min(wait, 10.0)
            if attempt < _NOMINATIM_MAX_RETRIES:
                time.sleep(wait)
                continue
            raise DistanceError(
                "Geocoding service is rate-limiting requests. "
                "Please try again in a few seconds."
            )

        if resp.status_code >= 500:
            last_error = f"Server error {resp.status_code}"
            if attempt < _NOMINATIM_MAX_RETRIES:
                time.sleep(_NOMINATIM_RETRY_DELAY)
                continue
            raise DistanceError(
                f"Geocoding service returned HTTP {resp.status_code}."
            )

        if resp.status_code != 200:
            raise DistanceError(
                f"Geocoding service returned HTTP {resp.status_code}."
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise DistanceError(
                "Geocoding service returned invalid JSON."
            ) from exc

        if not data:
            raise DistanceError(
                f"Could not find '{address}'. "
                f"Try adding the town or a nearby landmark."
            )

        hit = data[0]
        return (
            float(hit["lat"]),
            float(hit["lon"]),
            hit.get("display_name", address),
        )

    # Unreachable but keeps type checkers happy
    raise DistanceError(f"Geocoding failed: {last_error or 'unknown error'}")


def _route_km(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float
) -> Decimal:
    url = (
        f"{OSRM_BASE_URL}/route/v1/driving/"
        f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    )
    params = {"overview": "false"}

    try:
        resp = httpx.get(url, params=params, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise DistanceError(
            f"Routing service error (HTTP {exc.response.status_code})."
        ) from exc
    except httpx.HTTPError as exc:
        raise DistanceError(
            f"Routing service unavailable: {exc}"
        ) from exc

    if data.get("code") != "Ok":
        raise DistanceError(
            f"No driving route found. (OSRM: {data.get('code', 'unknown')})"
        )

    try:
        meters = data["routes"][0]["distance"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DistanceError(
            "Routing service returned an unexpected response."
        ) from exc

    return Decimal(str(round(meters / 1000.0, 2)))


def estimate_distance_from_address(
    address: str, db: Session
) -> DistanceResult:
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

    # Persist to cache so repeat lookups never hit Nominatim again
    try:
        db.add(
            models.GeocodeCache(
                address_key=key,
                lat=Decimal(str(lat)),
                lon=Decimal(str(lon)),
                display_name=display[:255],
                distance_km=km,
            )
        )
        db.commit()
    except Exception as exc:
        # Concurrent insert (unique constraint) — harmless, just roll back.
        db.rollback()
        print(f"[distance] cache write failed (ignored): {exc}")

    return DistanceResult(
        distance_km=km,
        geocoded_address=display,
        lat=lat,
        lon=lon,
        from_cache=False,
    )