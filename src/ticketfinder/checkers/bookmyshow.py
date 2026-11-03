from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Optional

from ticketfinder.checkers.base import PlatformChecker, fetch_page_content
from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob

BMS_CITY_SLUGS: dict[str, str] = {
    "chennai": "chennai",
    "bengaluru": "bengaluru",
    "bangalore": "bengaluru",
    "mumbai": "mumbai",
    "hyderabad": "hyderabad",
    "delhi": "national-capital-region-ncr",
    "kochi": "kochi",
    "coimbatore": "coimbatore",
    "madurai": "madurai",
    "pune": "pune",
    "kolkata": "kolkata",
}


def _map_bms_status(code_or_text: str) -> str:
    val = str(code_or_text).strip().upper()
    if val in ("1", "A", "AVAILABLE", "GREEN"):
        return "AVAILABLE"
    if val in ("2", "F", "FAST FILLING", "FAST_FILLING", "ORANGE", "YELLOW"):
        return "FAST_FILLING"
    if val in ("3", "ALMOST FULL", "ALMOST_FULL", "RED"):
        return "ALMOST_FULL"
    if val in ("0", "S", "SOLD OUT", "SOLD_OUT", "GREY", "GRAY"):
        return "SOLD_OUT"
    return "AVAILABLE"


def parse_bms_initial_state(
    state_data: dict[str, Any],
    target_date_compact: str,
    booking_url: str,
) -> list[CinemaVenue]:
    """
    Extracts CinemaVenue and ShowtimeSlot objects (including seat counts & category status)
    from BookMyShow's embedded __INITIAL_STATE__ JSON structure.
    """
    venues: list[CinemaVenue] = []
    # Check if active date matches target_date_compact (YYYYMMDD) when date info is present
    # Note: we keep != target_date_compact for exact dates so auto-redirects to earlier dates are rejected,
    # and reject any date earlier than target_date_compact.
    is_any_date = target_date_compact.upper() == "ANY"
    showtimes_by_event = state_data.get("showtimesByEvent") or {}
    current_date = showtimes_by_event.get("currentDateCode")
    if not is_any_date and current_date and str(current_date) != target_date_compact:
        return []

    venue_list = (
        showtimes_by_event.get("venues")
        or state_data.get("venues")
        or state_data.get("BookMyShow", {}).get("arrVenues")
        or []
    )
    if isinstance(venue_list, dict):
        venue_list = list(venue_list.values())

    for v_obj in venue_list:
        if not isinstance(v_obj, dict):
            continue
        v_name = (
            v_obj.get("VenueName")
            or v_obj.get("venueName")
            or v_obj.get("additionalData", {}).get("venueName")
            or ""
        ).strip()
        if not v_name:
            continue

        add_data = v_obj.get("additionalData") or {}
        lat_raw = v_obj.get("VenueLatitude") or add_data.get("latitude")
        lon_raw = v_obj.get("VenueLongitude") or add_data.get("longitude")
        lat: Optional[float] = None
        lon: Optional[float] = None
        try:
            if lat_raw and lon_raw:
                lat = float(lat_raw)
                lon = float(lon_raw)
        except (ValueError, TypeError):
            pass

        address = (
            v_obj.get("VenueAddress")
            or add_data.get("address")
            or v_obj.get("VenueSubRegionName")
        )

        raw_shows = v_obj.get("showtimes") or v_obj.get("arrShows") or []
        slots: list[ShowtimeSlot] = []
        for s_obj in raw_shows:
            if not isinstance(s_obj, dict):
                continue
            show_date = str(s_obj.get("ShowDateCode") or s_obj.get("showDateCode") or "")
            if show_date and show_date != target_date_compact:
                continue

            time_str = (
                s_obj.get("ShowTime")
                or s_obj.get("showTime")
                or s_obj.get("title")
                or ""
            ).strip()
            if not time_str:
                continue

            s_add = s_obj.get("additionalData") or {}
            fmt = (
                s_obj.get("Dimension")
                or s_add.get("attributes")
                or s_obj.get("ScreenFormat")
                or "2D"
            )
            status = _map_bms_status(
                s_obj.get("AvailabilityStatus")
                or s_add.get("availStatus")
                or s_obj.get("status")
                or "AVAILABLE"
            )

            avail_seats: Optional[int] = None
            total_seats: Optional[int] = None
            cat_parts: list[str] = []

            categories = s_obj.get("Categories") or s_add.get("categories") or []
            if isinstance(categories, list) and categories:
                sum_avail = 0
                sum_total = 0
                has_seat_numbers = False
                for cat in categories:
                    if not isinstance(cat, dict):
                        continue
                    c_name = cat.get("PriceDesc") or cat.get("categoryName") or "Standard"
                    c_price = cat.get("CurPrice") or cat.get("price")
                    c_status = _map_bms_status(
                        cat.get("CurStatus") or cat.get("availStatus") or status
                    )
                    c_avail = cat.get("SeatsAvail") or cat.get("availableSeats")
                    c_max = cat.get("MaxSeats") or cat.get("totalSeats")
                    if c_avail is not None and c_max is not None:
                        try:
                            sum_avail += int(c_avail)
                            sum_total += int(c_max)
                            has_seat_numbers = True
                        except (ValueError, TypeError):
                            pass
                    label = ShowtimeSlot(time=time_str, status=c_status).status_label
                    if c_price:
                        cat_parts.append(f"{c_name} ₹{c_price}: {label}")
                    else:
                        cat_parts.append(f"{c_name}: {label}")

                if has_seat_numbers and sum_total > 0:
                    avail_seats = sum_avail
                    total_seats = sum_total

            if avail_seats is None and s_obj.get("availableSeats") is not None:
                try:
                    avail_seats = int(s_obj["availableSeats"])
                    total_seats = int(s_obj.get("totalSeats") or 0) or None
                except (ValueError, TypeError):
                    pass

            slots.append(
                ShowtimeSlot(
                    time=time_str,
                    screen_format=str(fmt),
                    status=status,
                    available_seats=avail_seats,
                    total_seats=total_seats,
                    category_info=", ".join(cat_parts) if cat_parts else None,
                )
            )

        if slots:
            venues.append(
                CinemaVenue(
                    name=v_name,
                    platform="BookMyShow",
                    booking_url=booking_url,
                    showtimes=slots,
                    address=address,
                    latitude=lat,
                    longitude=lon,
                )
            )

    return venues


def parse_bms_html(
    html: str,
    target_date: str,
    booking_url: str,
) -> list[CinemaVenue]:
    """Parses BookMyShow HTML page (both __INITIAL_STATE__ JSON and DOM fallback)."""
    if not html:
        return []

    target_compact = target_date.replace("-", "")

    # 1. Try __INITIAL_STATE__ JSON extraction
    m_state = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*</script>",
        html,
        flags=re.DOTALL,
    )
    if m_state:
        try:
            state = json.loads(m_state.group(1))
            venues = parse_bms_initial_state(state, target_compact, booking_url)
            if venues:
                return venues
        except Exception:
            pass

    # 2. Validate that target_compact date appears as an active date link/pill in the HTML
    if target_compact not in html and target_date not in html:
        return []

    # 3. DOM pattern fallback for venue blocks & showtime pills
    venues: list[CinemaVenue] = []
    venue_blocks = re.findall(
        r'data-name="([^"]+)"[^>]*data-lat="([^"]*)"[^>]*data-lng="([^"]*)"(.*?)(?=data-name="|$)',
        html,
        flags=re.DOTALL,
    )
    for v_name, lat_s, lon_s, block in venue_blocks:
        times = re.findall(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b", block, flags=re.IGNORECASE)
        if not times:
            continue
        lat = float(lat_s) if lat_s else None
        lon = float(lon_s) if lon_s else None
        slots = [ShowtimeSlot(time=t.upper()) for t in dict.fromkeys(times)]
        venues.append(
            CinemaVenue(
                name=v_name.strip(),
                platform="BookMyShow",
                booking_url=booking_url,
                showtimes=slots,
                latitude=lat,
                longitude=lon,
            )
        )

    return venues


class BookMyShowChecker(PlatformChecker):
    platform_name = "BookMyShow"

    def check(self, job: WatchJob) -> list[CinemaVenue]:
        city_slug = BMS_CITY_SLUGS.get(job.city.lower(), job.city.lower().replace(" ", "-"))
        movie_slug = re.sub(r"[^a-z0-9]+", "-", job.movie.lower()).strip("-")
        date_compact = job.target_date.replace("-", "")

        # Discover BMS event code via search or direct movie page
        search_url = (
            f"https://in.bookmyshow.com/explore/movies-{city_slug}?q="
            f"{urllib.parse.quote(job.movie)}"
        )
        html = fetch_page_content(search_url, timeout=10.0, use_browser_fallback=False)

        m_event = re.search(
            rf"/movies/{re.escape(city_slug)}/[^\"'/]*{re.escape(movie_slug)}[^\"'/]*/(ET\d+)",
            html,
            flags=re.IGNORECASE,
        )
        if m_event:
            event_code = m_event.group(1)
            buytickets_url = (
                f"https://in.bookmyshow.com/buytickets/{movie_slug}-{city_slug}/"
                f"movie-{city_slug[:4]}-{event_code}-MT/{date_compact}"
            )
            st_html = fetch_page_content(buytickets_url, timeout=12.0, use_browser_fallback=True)
            return parse_bms_html(st_html, job.target_date, buytickets_url)

        return []
