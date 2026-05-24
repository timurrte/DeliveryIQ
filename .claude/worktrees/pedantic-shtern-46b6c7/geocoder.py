"""
geocoder.py
-----------
Converts human-readable addresses into (lat, lon) coordinates using the
Nominatim geocoder bundled with OSMnx / geopy.
"""

import logging
from dataclasses import dataclass

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)

_geolocator = Nominatim(user_agent="delivery_optimizer_v1")


@dataclass
class Location:
    address: str
    lat: float
    lon: float
    node_id: int | None = None          # filled in after snapping to OSM graph


# ─────────────────────────────────────────────────────────────────────────────
def geocode(address: str, retries: int = 3) -> tuple[float, float]:
    """
    Geocode *address* → (latitude, longitude).

    Raises
    ------
    ValueError
        If the address cannot be resolved after *retries* attempts.
    """
    for attempt in range(1, retries + 1):
        try:
            result = _geolocator.geocode(address, timeout=10)
            if result is None:
                raise ValueError(f"Nominatim returned no result for: '{address}'")
            logger.info("  Geocoded '%s' → (%.5f, %.5f)", address, result.latitude, result.longitude)
            return result.latitude, result.longitude
        except GeocoderTimedOut:
            logger.warning("  Geocoder timed-out (attempt %d/%d) for '%s'", attempt, retries, address)
    raise GeocoderServiceError(f"Geocoder failed after {retries} retries for '{address}'")


# ─────────────────────────────────────────────────────────────────────────────
def geocode_all(addresses: list[str]) -> list[Location]:
    """
    Geocode a list of addresses and return a list of :class:`Location` objects.
    """
    locations: list[Location] = []
    for addr in addresses:
        lat, lon = geocode(addr)
        locations.append(Location(address=addr, lat=lat, lon=lon))
    return locations
