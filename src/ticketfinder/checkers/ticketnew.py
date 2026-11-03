from __future__ import annotations

import json
import re

from ticketfinder.checkers.base import PlatformChecker, fetch_page_content
from ticketfinder.checkers.district import (
    parse_district_next_data,
    resolve_district_movie_candidates,
)
from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob


def parse_ticketnew_html(html: str, target_date: str, booking_url: str) -> list[CinemaVenue]:
    if not html:
        return []

    m_next = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
        html,
        flags=re.DOTALL,
    )
    if m_next:
        try:
            data = json.loads(m_next.group(1))
            venues = parse_district_next_data(
                data, target_date, booking_url, platform_label="TicketNew"
            )
            if venues:
                return venues
        except Exception:
            pass

    if target_date.upper() != "ANY" and target_date not in html and target_date.replace("-", "") not in html:
        return []

    venues: list[CinemaVenue] = []
    blocks = re.findall(
        r'class="[^"]*cinema-name[^"]*"[^>]*>([^<]+)</.*?class="[^"]*showtimes[^"]*"(.*?)(?=class="[^"]*cinema-name|$)',
        html,
        flags=re.DOTALL,
    )
    for c_name, show_block in blocks:
        times = re.findall(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b", show_block, flags=re.IGNORECASE)
        if not times:
            continue
        slots = [ShowtimeSlot(time=t.upper()) for t in dict.fromkeys(times)]
        venues.append(
            CinemaVenue(
                name=c_name.strip(),
                platform="TicketNew",
                booking_url=booking_url,
                showtimes=slots,
            )
        )
    return venues


class TicketNewChecker(PlatformChecker):
    platform_name = "TicketNew"

    def check(self, job: WatchJob) -> list[CinemaVenue]:
        city_slug = job.city.lower().strip().replace(" ", "-")
        candidates = resolve_district_movie_candidates(job)
        date_param = "" if job.target_date.upper() == "ANY" else f"&fromdate={job.target_date}"

        all_venues: list[CinemaVenue] = []
        for content_id, frmt_id, m_slug in candidates:
            # Fetch city-scoped showtimes and attach TicketNew's direct movie detail URL
            dist_url = (
                f"https://www.district.in/movies/{m_slug}-movie-tickets-in-{city_slug}-MV{content_id}"
                f"?frmtid={frmt_id}{date_param}"
            )
            tn_booking_url = (
                f"https://ticketnew.com/movies/{m_slug}-movie-detail-{content_id}"
                f"?frmtid={frmt_id}{date_param}"
            )
            html = fetch_page_content(dist_url, timeout=12.0, use_browser_fallback=False)
            venues = parse_ticketnew_html(html, job.target_date, tn_booking_url)
            all_venues.extend(venues)

        return all_venues
