from __future__ import annotations

import re
from typing import Optional

from ticketfinder.geo import geocode_location, haversine_km
from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob
from ticketfinder.storage import Storage


def _normalize_tokens(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def is_cinema_name_match(venue_name: str, requested_cinemas: list[str]) -> bool:
    """Checks if venue_name fuzzy-matches any of the user's requested cinema keywords."""
    if not requested_cinemas:
        return False
    norm_venue = _normalize_tokens(venue_name)
    venue_words = set(norm_venue.split())

    for req in requested_cinemas:
        norm_req = _normalize_tokens(req)
        if not norm_req:
            continue
        if norm_req in ("any", "all", "*"):
            return True
        if norm_req in norm_venue or norm_venue in norm_req:
            return True
        req_words = [w for w in norm_req.split() if w not in ("cinemas", "cinema", "theatre", "theatres", "multiplex", "mall")]
        if req_words and all(w in venue_words for w in req_words):
            return True
    return False


def merge_venues_across_platforms(venues: list[CinemaVenue]) -> list[CinemaVenue]:
    """Merges the same cinema discovered across multiple platforms into a unified CinemaVenue."""
    by_key: dict[str, CinemaVenue] = {}

    def _slot_key(s: ShowtimeSlot) -> str:
        return f"{s.date or ''}_{s.time.strip().upper()}"

    for v in venues:
        key = v.normalized_key
        if not key:
            continue
        if key not in by_key:
            by_key[key] = CinemaVenue(
                name=v.name,
                platform=v.platform,
                booking_url=v.booking_url,
                showtimes=list(v.showtimes),
                address=v.address,
                latitude=v.latitude,
                longitude=v.longitude,
                distance_km=v.distance_km,
                matched_by=v.matched_by,
                first_seen_at=v.first_seen_at,
                extra_platforms=list(v.extra_platforms),
                show_date=v.show_date,
            )
        else:
            existing = by_key[key]
            if v.platform.lower() != existing.platform.lower():
                existing.extra_platforms.append((v.platform, v.booking_url))
            if existing.latitude is None and v.latitude is not None:
                existing.latitude = v.latitude
                existing.longitude = v.longitude
            if existing.address is None and v.address is not None:
                existing.address = v.address
            if existing.show_date is None and v.show_date is not None:
                existing.show_date = v.show_date

            # Merge showtimes by (date, time) key, preferring the slot with exact seat counts
            slots_by_key: dict[str, ShowtimeSlot] = {_slot_key(s): s for s in existing.showtimes}
            for s in v.showtimes:
                s_key = _slot_key(s)
                if s_key not in slots_by_key:
                    slots_by_key[s_key] = s
                    existing.showtimes.append(s)
                else:
                    curr = slots_by_key[s_key]
                    if not curr.date and s.date:
                        curr.date = s.date
                    if curr.available_seats is None and s.available_seats is not None:
                        curr.available_seats = s.available_seats
                        curr.booked_seats = s.booked_seats
                        curr.total_seats = s.total_seats
                        curr.status = s.status
                    if not curr.category_info and s.category_info:
                        curr.category_info = s.category_info

    return list(by_key.values())


def filter_and_rank_venues(
    venues: list[CinemaVenue],
    job: WatchJob,
    storage: Optional[Storage] = None,
) -> list[CinemaVenue]:
    """
    Merges multi-platform venues, computes distance from job.area (if provided),
    and filters by:
      - requested cinema name match, OR
      - within job.radius_km (default 15 km) of job.area
    """
    merged = merge_venues_across_platforms(venues)

    # Ensure job area coordinates are resolved if job.area is set
    if job.area and (job.area_lat is None or job.area_lon is None):
        coords = geocode_location(job.area, job.city, storage=storage)
        if coords:
            job.area_lat, job.area_lon = coords

    matched: list[CinemaVenue] = []
    match_all = not job.cinemas and not job.area
    strict_cinema_only = bool(
        job.cinemas and re.search(r"\b(restrict|restricted|only)\b", job.raw_query or "", re.IGNORECASE)
    )

    for venue in merged:
        # Resolve venue coordinates if we have a reference area
        if job.area_lat is not None and job.area_lon is not None:
            if venue.latitude is None or venue.longitude is None:
                query_str = venue.address or venue.name
                v_coords = geocode_location(query_str, job.city, storage=storage)
                if not v_coords and venue.address:
                    v_coords = geocode_location(venue.name, job.city, storage=storage)
                if v_coords:
                    venue.latitude, venue.longitude = v_coords

            if venue.latitude is not None and venue.longitude is not None:
                venue.distance_km = haversine_km(
                    job.area_lat, job.area_lon, venue.latitude, venue.longitude
                )

        name_matched = is_cinema_name_match(venue.name, job.cinemas)
        radius_matched = False
        if job.area:
            if venue.distance_km is not None and venue.distance_km <= job.radius_km:
                radius_matched = True
            elif (
                venue.distance_km is None
                and job.area.lower() in f"{venue.name} {venue.address or ''}".lower()
            ):
                radius_matched = True
                venue.distance_km = 0.0

        if strict_cinema_only:
            if name_matched and (radius_matched or not job.area):
                venue.matched_by = "both" if radius_matched else "requested_cinema"
                matched.append(venue)
        elif match_all or name_matched or radius_matched:
            if name_matched and radius_matched:
                venue.matched_by = "both"
            elif radius_matched:
                venue.matched_by = "nearby_locality"
            else:
                venue.matched_by = "requested_cinema"
            matched.append(venue)

    # Sort: nearest distance first, then requested cinemas, then name
    matched.sort(
        key=lambda v: (
            v.distance_km if v.distance_km is not None else 9999.0,
            0 if v.matched_by in ("both", "requested_cinema") else 1,
            v.name.lower(),
        )
    )
    return matched
