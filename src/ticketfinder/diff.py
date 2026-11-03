from __future__ import annotations

from datetime import datetime
from typing import Optional

from ticketfinder.models import (
    CinemaVenue,
    DiffReport,
    ExistingCinemaUpdate,
    ShowtimeSlot,
    WatchJob,
)


def compute_diff_report(
    job: WatchJob,
    current_venues: list[CinemaVenue],
    previous_snapshots: dict[str, CinemaVenue],
    is_initial_check: bool = False,
    sites_checked: Optional[list[str]] = None,
) -> DiffReport:
    """
    Compares current open venues against stored venue snapshots and builds a DiffReport
    separating:
      1. Newly opened cinemas
      2. Newly added showtimes on existing cinemas
      3. Previously opened cinemas (with seat occupancy delta vs. prior check)
    """
    now_time = datetime.now().strftime("%I:%M %p").lstrip("0")
    newly_opened: list[CinemaVenue] = []
    new_showtimes_list: list[ExistingCinemaUpdate] = []
    previously_opened_list: list[ExistingCinemaUpdate] = []

    seen_keys: set[str] = set()

    for venue in current_venues:
        key = venue.normalized_key
        seen_keys.add(key)

        if key not in previous_snapshots:
            venue.first_seen_at = venue.first_seen_at or now_time
            newly_opened.append(venue)
        else:
            prev_venue = previous_snapshots[key]
            venue.first_seen_at = prev_venue.first_seen_at or now_time
            prev_slots_map: dict[str, ShowtimeSlot] = {
                s.time.strip().upper(): s for s in prev_venue.showtimes
            }

            added_slots: list[ShowtimeSlot] = []
            continuing_slots: list[ShowtimeSlot] = []

            for slot in venue.showtimes:
                t_key = slot.time.strip().upper()
                if t_key not in prev_slots_map:
                    added_slots.append(slot)
                else:
                    continuing_slots.append(slot)

            if added_slots:
                new_showtimes_list.append(
                    ExistingCinemaUpdate(
                        venue=venue,
                        added_showtimes=added_slots,
                        previous_slots_by_time=prev_slots_map,
                    )
                )

            # Build a venue copy holding the pre-existing showtimes for the "Previously Opened" section
            prev_section_venue = CinemaVenue(
                name=venue.name,
                platform=venue.platform,
                booking_url=venue.booking_url,
                showtimes=continuing_slots if continuing_slots else prev_venue.showtimes,
                address=venue.address,
                latitude=venue.latitude,
                longitude=venue.longitude,
                distance_km=venue.distance_km,
                matched_by=venue.matched_by,
                first_seen_at=venue.first_seen_at,
                extra_platforms=venue.extra_platforms,
            )
            previously_opened_list.append(
                ExistingCinemaUpdate(
                    venue=prev_section_venue,
                    added_showtimes=[],
                    previous_slots_by_time=prev_slots_map,
                )
            )

    # Also include any previously opened cinema that might not have appeared in a partial transient scrape
    for key, prev_venue in previous_snapshots.items():
        if key not in seen_keys:
            prev_slots_map = {s.time.strip().upper(): s for s in prev_venue.showtimes}
            previously_opened_list.append(
                ExistingCinemaUpdate(
                    venue=prev_venue,
                    added_showtimes=[],
                    previous_slots_by_time=prev_slots_map,
                )
            )

    update_num = job.alert_count + 1
    return DiffReport(
        job=job,
        update_number=update_num,
        newly_opened_cinemas=newly_opened,
        new_showtimes_cinemas=new_showtimes_list,
        previously_opened_cinemas=previously_opened_list,
        is_initial_check=is_initial_check,
        sites_checked=sites_checked or ["BookMyShow", "District (Paytm)", "TicketNew"],
        checked_at=now_time,
    )
