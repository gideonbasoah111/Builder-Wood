"""
Distance service for Builder Wood.

Uses:
- Nominatim (OpenStreetMap) for geocoding the buyer's address → (lat, lon)
- OpenRouteService (ORS) for driving distance from Sokoban → buyer

Rate limits:
- Nominatim: 1 request/second max, requires a descriptive User-Agent
- ORS: depends on plan; free tier is ~2000 requests/day
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

import httpx

from builder_wood.app.config import settings


# Sokoban Wood Village, Kumasi — the delivery origin.
SOKOBAN_LAT = 6.6833
SOKOBAN_LON = -1.6167

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"

# Nominatim policy: max 1 request per second. We keep a simple throttle.
_last_nominatim_call: float = 0.0
_NOMINATIM_MIN_INTERVAL = 1.0  # seconds


@dataclass
class DistanceResult:
    distance_km: Decimal
    geocoded_address: str
    lat: float
    lon: float


class DistanceError(Exception):
    """Raised when geocoding or routing fails for a user-facing reason."""


def _throttle_nominatim() -> None:
    """Ensure at least 1 second between Nominatim calls."""
    global _last_nominatim_call
    elapsed = time.monotonic() - _last_nominatim_call
    if elapsed < _NOMINATIM_MIN_INTERVAL:
        time.sleep(_NOMINATIM_MIN_INTERVAL - elapsed)
    _last_nominatim_call = time.monotonic()


def geocode_address(address: str) -> Tuple[float, float, str]:
    """Convert an address string to (lat, lon, display_name).

    Raises DistanceError if the address cannot be resolved.
    """
    _throttle_nominatim()

    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
    }
    headers = {
        # Nominatim requires a descriptive User-Agent.
        # See: https://operations.osmfoundation.org/policies/nominatim/
        "User-Agent": settings.NOMINATIM_USER_AGENT,
    }

    try:
        resp = httpx.get(
            NOMINATIM_URL,
            params=params,
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise DistanceError(f"Could not reach geocoding service: {exc}") from exc

    if not data:
        raise DistanceError(
            f"Could not find the address '{address}'. "
            f"Try adding the town or a nearby landmark."
        )

    hit = data[0]
    return float(hit["lat"]), float(hit["lon"]), hit.get("display_name", address)


def route_distance_km(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> Decimal:
    """Compute driving distance in km between two coordinates via ORS."""
    # ORS takes coordinates in [lon, lat] order.
    body = {
        "coordinates": [
            [origin_lon, origin_lat],
            [dest_lon, dest_lat],
        ],
    }
    headers = {
        "Authorization": settings.ORS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json, application/geo+json",
    }

    try:
        resp = httpx.post(
            ORS_DIRECTIONS_URL,
            json=body,
            headers=headers,
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        # 403 = bad key, 404 = no route, 429 = rate limited
        if exc.response.status_code == 403:
            raise DistanceError("Routing service rejected the API key.") from exc
        if exc.response.status_code == 404:
            raise DistanceError(
                "No driving route found to this address. "
                "Try a more specific address."
            ) from exc
        raise DistanceError(
            f"Routing service error (HTTP {exc.response.status_code})."
        ) from exc
    except httpx.HTTPError as exc:
        raise DistanceError(f"Could not reach routing service: {exc}") from exc

    try:
        meters = data["routes"][0]["summary"]["distance"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DistanceError("Routing service returned an unexpected response.") from exc

    km = Decimal(str(round(meters / 1000.0, 2)))
    return km


def estimate_distance_from_address(address: str) -> DistanceResult:
    """Full pipeline: geocode the buyer's address, then route from Sokoban."""
    if not address or len(address.strip()) < 3:
        raise DistanceError("Please enter a more complete delivery address.")

    lat, lon, display = geocode_address(address)
    km = route_distance_km(SOKOBAN_LAT, SOKOBAN_LON, lat, lon)

    return DistanceResult(
        distance_km=km,
        geocoded_address=display,
        lat=lat,
        lon=lon,
    )