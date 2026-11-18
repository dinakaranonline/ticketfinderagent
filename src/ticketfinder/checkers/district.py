from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from ticketfinder.checkers.base import PlatformChecker, fetch_page_content
from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob

KNOWN_LANGUAGES = ("tamil", "telugu", "hindi", "english", "malayalam", "kannada")


def _map_district_status(raw: str, status_color: str = "") -> str:
    val = str(raw).strip().upper()
    col = str(status_color).strip().upper()
    if "SOLD" in val or val == "0" or col in ("D", "R_SOLD"):
        return "SOLD_OUT"
    if "ALMOST" in val or "RED" in val or col == "O":
        return "ALMOST_FULL"
    if "FAST" in val or "FILLING" in val or "ORANGE" in val or "YELLOW" in val or col == "Y":
        return "FAST_FILLING"
    return "AVAILABLE"


def _utc_iso_to_ist_label(iso_str: str) -> tuple[str, str]:
    """
    Converts District/TicketNew UTC showTime ('2026-11-05T04:40') to IST (+05:30)
    returning (ist_date_yyyy_mm_dd, formatted_12h_time '10:10 AM').
    """
    if "T" not in iso_str:
        return "", iso_str.strip()
    try:
        dt_utc = datetime.strptime(iso_str[:16], "%Y-%m-%dT%H:%M")
        dt_ist = dt_utc + timedelta(hours=5, minutes=30)
        return dt_ist.strftime("%Y-%m-%d"), dt_ist.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return iso_str[:10], iso_str


def parse_district_next_data(
    next_data: dict[str, Any],
    target_date: str,
    booking_url: str,
    platform_label: str = "District (Paytm)",
) -> list[CinemaVenue]:
    """
    Extracts CinemaVenue and ShowtimeSlot objects (including exact avail & total seats,
    audi/screen format, and category breakdown) from District / TicketNew __NEXT_DATA__.
    Supports both serverState.movieSessions (live SSR schema) and legacy cinemas list.
    """
    venues: list[CinemaVenue] = []
    page_props = next_data.get("props", {}).get("pageProps", {})
    data_root = page_props.get("data") or page_props.get("initialState") or page_props
    is_any_date = target_date.upper() == "ANY"

    # 1. Live SSR schema: data.serverState.movieSessions
    server_state = data_root.get("serverState") if isinstance(data_root, dict) else None
    movie_sessions = (
        server_state.get("movieSessions") if isinstance(server_state, dict) else None
    )
    if isinstance(movie_sessions, dict) and movie_sessions:
        for ms_key, session_bundle in movie_sessions.items():
            if not isinstance(session_bundle, dict):
                continue
            search_date = str(session_bundle.get("searchDate") or "")[:10]
            if not is_any_date and search_date and search_date != target_date:
                continue

            page_data = session_bundle.get("pageData") or {}
            cinema_entries: list[dict[str, Any]] = []
            if isinstance(page_data, dict):
                cinema_entries.extend(page_data.get("nearbyCinemas") or [])
                cinema_entries.extend(page_data.get("farCinemas") or [])

            for entry in cinema_entries:
                if not isinstance(entry, dict):
                    continue
                c_info = entry.get("cinemaInfo") or entry
                c_name = str(c_info.get("name") or c_info.get("label") or "").strip()
                if not c_name:
                    continue

                lat: Optional[float] = None
                lon: Optional[float] = None
                try:
                    if c_info.get("latitude") is not None and c_info.get("longitude") is not None:
                        lat = float(c_info["latitude"])
                        lon = float(c_info["longitude"])
                    elif c_info.get("lat") is not None and c_info.get("lon") is not None:
                        lat = float(c_info["lat"])
                        lon = float(c_info["lon"])
                except (ValueError, TypeError):
                    pass

                address = c_info.get("address")
                raw_sessions = entry.get("sessions") or []
                slots: list[ShowtimeSlot] = []

                for s_obj in raw_sessions:
                    if not isinstance(s_obj, dict):
                        continue
                    raw_time = str(s_obj.get("showTime") or "")
                    ist_date, time_label = _utc_iso_to_ist_label(raw_time)
                    if not time_label:
                        continue
                    if not is_any_date and ist_date and ist_date != target_date and search_date != target_date:
                        continue

                    slot_date = ist_date or search_date or (target_date if not is_any_date else None)
                    scrn_fmt = str(s_obj.get("scrnFmt") or "2D").strip()
                    audi = str(s_obj.get("premiumLabel") or s_obj.get("audi") or "").strip()
                    fmt_display = f"{scrn_fmt} • {audi}" if audi and audi not in scrn_fmt else scrn_fmt

                    avail_seats: Optional[int] = None
                    total_seats: Optional[int] = None
                    try:
                        if s_obj.get("avail") is not None:
                            avail_seats = int(s_obj["avail"])
                        if s_obj.get("total") is not None:
                            total_seats = int(s_obj["total"])
                    except (ValueError, TypeError):
                        pass

                    status = _map_district_status(
                        s_obj.get("seatStatus") or "AVAILABLE",
                        s_obj.get("statusColor") or "",
                    )

                    # Category breakdown from areas
                    cat_parts: list[str] = []
                    for area in s_obj.get("areas") or []:
                        if not isinstance(area, dict):
                            continue
                        a_label = area.get("label") or ""
                        a_avail = area.get("sAvail")
                        a_total = area.get("sTotal")
                        a_price = area.get("price")
                        if a_label and a_avail is not None and a_total is not None:
                            price_s = f" ₹{int(round(float(a_price)))}" if a_price else ""
                            cat_parts.append(f"{a_label}{price_s}: {a_avail}/{a_total} left")

                    slots.append(
                        ShowtimeSlot(
                            time=time_label,
                            date=slot_date,
                            screen_format=fmt_display,
                            status=status,
                            available_seats=avail_seats,
                            total_seats=total_seats,
                            category_info=", ".join(cat_parts) if cat_parts else None,
                        )
                    )

                if slots:
                    venue_date = (slots[0].date if slots and slots[0].date else search_date) or (
                        target_date if not is_any_date else None
                    )
                    venues.append(
                        CinemaVenue(
                            name=c_name,
                            platform=platform_label,
                            booking_url=booking_url,
                            showtimes=slots,
                            address=address,
                            latitude=lat,
                            longitude=lon,
                            show_date=venue_date,
                        )
                    )

        if venues:
            return venues

    # 2. Fallback / unit-test schema: pageProps.initialState.cinemas
    active_date = data_root.get("selectedDate") or data_root.get("date")
    if not is_any_date and active_date and str(active_date)[:10] != target_date:
        return []

    cinemas_raw = (
        data_root.get("cinemas")
        or data_root.get("theatres")
        or data_root.get("pageData", {}).get("cinemas")
        or []
    )

    for c_obj in cinemas_raw:
        if not isinstance(c_obj, dict):
            continue
        c_name = (
            c_obj.get("name")
            or c_obj.get("cinemaName")
            or c_obj.get("theatreName")
            or ""
        ).strip()
        if not c_name:
            continue

        lat = None
        lon = None
        try:
            if c_obj.get("latitude") and c_obj.get("longitude"):
                lat = float(c_obj["latitude"])
                lon = float(c_obj["longitude"])
            elif c_obj.get("lat") and c_obj.get("lon"):
                lat = float(c_obj["lat"])
                lon = float(c_obj["lon"])
        except (ValueError, TypeError):
            pass

        address = c_obj.get("address") or c_obj.get("locality")
        sessions = c_obj.get("sessions") or c_obj.get("showtimes") or c_obj.get("shows") or []
        slots = []

        for s_obj in sessions:
            if not isinstance(s_obj, dict):
                continue
            s_date = str(s_obj.get("showDate") or s_obj.get("date") or active_date or "")[:10]
            if not is_any_date and s_date and s_date != target_date:
                continue

            slot_date = s_date or (target_date if not is_any_date else None)
            time_str = (
                s_obj.get("showTime")
                or s_obj.get("time")
                or s_obj.get("displayTime")
                or ""
            ).strip()
            if not time_str:
                continue

            fmt = (
                s_obj.get("soundFormat")
                or s_obj.get("screenFormat")
                or s_obj.get("format")
                or "2D"
            )
            status = _map_district_status(
                s_obj.get("status") or s_obj.get("availabilityStatus") or "AVAILABLE"
            )

            avail: Optional[int] = None
            total: Optional[int] = None
            booked: Optional[int] = None
            try:
                if s_obj.get("availableSeats") is not None:
                    avail = int(s_obj["availableSeats"])
                if s_obj.get("totalSeats") is not None:
                    total = int(s_obj["totalSeats"])
                if s_obj.get("bookedSeats") is not None:
                    booked = int(s_obj["bookedSeats"])
            except (ValueError, TypeError):
                pass

            slots.append(
                ShowtimeSlot(
                    time=time_str,
                    date=slot_date,
                    screen_format=str(fmt),
                    status=status,
                    available_seats=avail,
                    booked_seats=booked,
                    total_seats=total,
                )
            )

        if slots:
            venue_date = (slots[0].date if slots and slots[0].date else active_date) or (
                target_date if not is_any_date else None
            )
            venues.append(
                CinemaVenue(
                    name=c_name,
                    platform=platform_label,
                    booking_url=booking_url,
                    showtimes=slots,
                    address=address,
                    latitude=lat,
                    longitude=lon,
                    show_date=venue_date,
                )
            )

    return venues


def parse_district_html(
    html: str,
    target_date: str,
    booking_url: str,
    platform_label: str = "District (Paytm)",
) -> list[CinemaVenue]:
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
            return parse_district_next_data(
                data, target_date, booking_url, platform_label=platform_label
            )
        except Exception:
            pass
    return []


def resolve_district_movie_candidates(
    job: WatchJob,
) -> list[tuple[int, str, str]]:
    """
    Discovers matching (contentId, frmtId, movie_slug) candidates for job.movie in job.city
    by reading the city catalog from TicketNew/District.
    Respects language keywords in job.movie (e.g., 'tamil', 'telugu', 'hindi').
    """
    city_slug = job.city.lower().strip().replace(" ", "-")
    catalog_url = f"https://ticketnew.com/movies/{city_slug}"
    html = fetch_page_content(catalog_url, timeout=10.0, use_browser_fallback=False)
    if not html:
        return []

    m_next = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
        html,
        flags=re.DOTALL,
    )
    if not m_next:
        return []

    try:
        data = json.loads(m_next.group(1))
        crm = (
            data.get("props", {})
            .get("pageProps", {})
            .get("data", {})
            .get("serverState", {})
            .get("currentlyRunningMovies", {})
            .get(city_slug, {})
            .get("data", {})
        )
        movies_list = crm.get("movies") or []

        raw_words = re.sub(r"[^a-z0-9]+", " ", job.movie.lower()).split()
        requested_lang: Optional[str] = None
        for w in raw_words:
            if w in KNOWN_LANGUAGES:
                requested_lang = w
                break

        title_words = [
            w
            for w in raw_words
            if w not in KNOWN_LANGUAGES and w not in ("version", "movie", "lang", "language", "2d", "3d", "imax")
        ]
        if not title_words:
            return []

        candidates: list[tuple[int, str, str]] = []
        seen_frmt: set[str] = set()

        for item in movies_list:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("label") or "").lower()
            if not all(w in name for w in title_words):
                continue
            item_lang = str(item.get("lang") or "").lower()
            if requested_lang and item_lang and requested_lang != item_lang:
                continue

            content_id = item.get("contentId")
            frmt_id = item.get("frmtId")
            if content_id and frmt_id and str(frmt_id) not in seen_frmt:
                seen_frmt.add(str(frmt_id))
                m_slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
                candidates.append((int(content_id), str(frmt_id), m_slug))

        return candidates
    except Exception:
        return []


class DistrictChecker(PlatformChecker):
    platform_name = "District (Paytm)"

    def check(self, job: WatchJob) -> list[CinemaVenue]:
        city_slug = job.city.lower().strip().replace(" ", "-")
        candidates = resolve_district_movie_candidates(job)
        date_param = "" if job.target_date.upper() == "ANY" else f"&fromdate={job.target_date}"

        all_venues: list[CinemaVenue] = []
        for content_id, frmt_id, m_slug in candidates:
            url = (
                f"https://www.district.in/movies/{m_slug}-movie-tickets-in-{city_slug}-MV{content_id}"
                f"?frmtid={frmt_id}{date_param}"
            )
            html = fetch_page_content(url, timeout=12.0, use_browser_fallback=False)
            venues = parse_district_html(
                html, job.target_date, url, platform_label="District (Paytm)"
            )
            all_venues.extend(venues)

        return all_venues
