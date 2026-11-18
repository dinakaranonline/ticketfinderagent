from __future__ import annotations

import re
import urllib.parse

from ticketfinder.checkers.base import PlatformChecker, fetch_page_content
from ticketfinder.checkers.bookmyshow import parse_bms_html
from ticketfinder.checkers.district import parse_district_html
from ticketfinder.models import CinemaVenue, WatchJob


class WebDiscoveryChecker(PlatformChecker):
    """
    Searches the web for active booking links (BookMyShow, District, PVR INOX, TicketNew)
    for the movie + city + date and inspects discovered showtime URLs.
    """

    platform_name = "Web Discovery"

    def check(self, job: WatchJob) -> list[CinemaVenue]:
        query = f'"{job.movie}" "{job.city}" movie tickets buytickets'
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        search_html = fetch_page_content(search_url, timeout=8.0, use_browser_fallback=False)
        if not search_html:
            return []

        is_any = job.target_date.upper() == "ANY"
        date_compact = "" if is_any else job.target_date.replace("-", "")
        venues: list[CinemaVenue] = []

        # Look for BookMyShow buytickets URLs in search results
        m_bms = re.search(
            r"(https://in\.bookmyshow\.com/buytickets/[^\"'&<>]+?/ET\d+-MT/)\d{8}",
            search_html,
        )
        if m_bms:
            target_bms_url = (
                f"{m_bms.group(1)}{date_compact}" if date_compact else m_bms.group(1).rstrip("/")
            )
            bms_html = fetch_page_content(target_bms_url, timeout=10.0, use_browser_fallback=False)
            venues.extend(parse_bms_html(bms_html, job.target_date, target_bms_url))

        # Look for District movie ticket URLs in search results
        m_dist = re.search(
            r"(https://www\.district\.in/movies/[^\"'&<>?]+)",
            search_html,
        )
        if m_dist:
            target_dist_url = (
                m_dist.group(1) if is_any else f"{m_dist.group(1)}?fromdate={job.target_date}"
            )
            dist_html = fetch_page_content(target_dist_url, timeout=10.0, use_browser_fallback=False)
            venues.extend(parse_district_html(dist_html, job.target_date, target_dist_url))

        return venues
