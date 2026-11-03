from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from typing import Optional

from ticketfinder.storage import Storage

# Built-in coordinate reference for instant resolution and offline fallback
KNOWN_LOCATIONS: dict[str, tuple[float, float]] = {
    # Chennai Localities & Cinemas
    "velachery, chennai": (12.9755, 80.2207),
    "velachery": (12.9755, 80.2207),
    "anna nagar, chennai": (13.0850, 80.2101),
    "anna nagar": (13.0850, 80.2101),
    "t nagar, chennai": (13.0418, 80.2341),
    "t nagar": (13.0418, 80.2341),
    "adyar, chennai": (13.0012, 80.2565),
    "adyar": (13.0012, 80.2565),
    "royapettah, chennai": (13.0553, 80.2642),
    "royapettah": (13.0553, 80.2642),
    "vadapalani, chennai": (13.0500, 80.2121),
    "vadapalani": (13.0500, 80.2121),
    "omr, chennai": (12.9121, 80.2279),
    "thoraipakkam, chennai": (12.9416, 80.2362),
    "navalur, chennai": (12.8459, 80.2265),
    "tambaram, chennai": (12.9249, 80.1000),
    "chromepet, chennai": (12.9516, 80.1462),
    "guindy, chennai": (13.0067, 80.2206),
    "mylapore, chennai": (13.0368, 80.2676),
    "nungambakkam, chennai": (13.0569, 80.2425),
    "egmore, chennai": (13.0732, 80.2609),
    "porur, chennai": (13.0382, 80.1565),
    "sholinganallur, chennai": (12.9010, 80.2279),
    "perungudi, chennai": (12.9654, 80.2461),
    "perumbakkam, chennai": (12.9056, 80.1972),
    "perumbakkam": (12.9056, 80.1972),
    "medavakkam, chennai": (12.9171, 80.1923),
    "medavakkam": (12.9171, 80.1923),
    "pallikaranai, chennai": (12.9349, 80.2137),
    "siruseri, chennai": (12.8350, 80.2200),
    "kelambakkam, chennai": (12.7897, 80.2214),
    "chennai": (13.0827, 80.2707),
    # Well-known Chennai Cinemas
    "pvr grand mall velachery": (12.9719, 80.2185),
    "grand mall velachery": (12.9719, 80.2185),
    "phoenix marketcity velachery": (12.9915, 80.2170),
    "luxe cinemas velachery": (12.9915, 80.2170),
    "jazz cinemas luxe": (12.9915, 80.2170),
    "spi sathyam royapettah": (13.0556, 80.2580),
    "sathyam cinemas": (13.0556, 80.2580),
    "spi palazzo": (13.0499, 80.2091),
    "palazzo nexus vijaya mall": (13.0499, 80.2091),
    "escape express avenue": (13.0587, 80.2641),
    "cinepolis bsr mall": (12.9416, 80.2362),
    "bsr mall omr": (12.9416, 80.2362),
    "ags cinemas navalur": (12.8459, 80.2265),
    "ags cinemas t nagar": (13.0428, 80.2337),
    "ags cinemas villivakkam": (13.1086, 80.2096),
    "ags cinemas maduravoyal": (13.0658, 80.1656),
    "devi cineplex": (13.0680, 80.2720),
    "vetri theatres chromepet": (12.9516, 80.1462),
    "rohini silver screens": (13.0766, 80.1945),
    "kasi talkies": (13.0285, 80.2089),
    "kamala cinemas": (13.0505, 80.2135),
    "mayajaal multiplex": (12.8439, 80.2408),
    # Bengaluru Localities
    "koramangala, bengaluru": (12.9352, 77.6245),
    "koramangala": (12.9352, 77.6245),
    "indiranagar, bengaluru": (12.9784, 77.6408),
    "whitefield, bengaluru": (12.9698, 77.7500),
    "jayanagar, bengaluru": (12.9308, 77.5838),
    "marathahalli, bengaluru": (12.9591, 77.6974),
    "mg road, bengaluru": (12.9756, 77.6066),
    "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946),
    # Mumbai, Hyderabad, Delhi, Kochi
    "andheri, mumbai": (19.1136, 72.8697),
    "bandra, mumbai": (19.0596, 72.8295),
    "lower parel, mumbai": (18.9953, 72.8300),
    "mumbai": (19.0760, 72.8777),
    "gachibowli, hyderabad": (17.4401, 78.3489),
    "banjara hills, hyderabad": (17.4156, 78.4347),
    "hyderabad": (17.3850, 78.4867),
    "kochi": (9.9312, 76.2673),
    "edappally, kochi": (10.0261, 76.3125),
    "coimbatore": (11.0168, 76.9558),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes the great-circle distance in kilometers between two coordinates."""
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(r * c, 2)


def _match_known_location(query: str) -> Optional[tuple[float, float]]:
    norm = query.strip().lower()
    if norm in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[norm]
    for key, coords in KNOWN_LOCATIONS.items():
        if key in norm or norm in key:
            return coords
    return None


def geocode_location(
    area_or_venue: str,
    city: Optional[str] = None,
    storage: Optional[Storage] = None,
    timeout: float = 5.0,
) -> Optional[tuple[float, float]]:
    """
    Resolves an area/locality or cinema name to (latitude, longitude).
    Checks:
      1. Built-in KNOWN_LOCATIONS dictionary
      2. Local SQLite geo_cache
      3. OpenStreetMap Nominatim API (free, no key needed)
    """
    if not area_or_venue or not area_or_venue.strip():
        return None

    full_query = (
        f"{area_or_venue.strip()}, {city.strip()}"
        if city and city.lower() not in area_or_venue.lower()
        else area_or_venue.strip()
    )

    # 1. Check built-in known locations
    known = _match_known_location(full_query) or _match_known_location(area_or_venue)
    if known:
        if storage:
            storage.save_cached_coords(full_query, known[0], known[1])
        return known

    # 2. Check SQLite cache
    if storage:
        cached = storage.get_cached_coords(full_query)
        if cached:
            return cached

    # 3. Query OpenStreetMap Nominatim API
    try:
        params = urllib.parse.urlencode({"q": full_query, "format": "json", "limit": 1})
        url = f"https://nominatim.openstreetmap.org/search?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TicketFinderAgent/0.1 (macOS Movie Ticket Watcher)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                if storage:
                    storage.save_cached_coords(full_query, lat, lon)
                return lat, lon
    except Exception:
        pass

    return None
