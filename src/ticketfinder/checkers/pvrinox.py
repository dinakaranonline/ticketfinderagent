"""
PVR INOX direct checker (pvrcinemas.com, which also serves INOX cinemas).

The website is a React SPA backed by a public JSON API at https://api3.pvrcinemas.com/api/.
No login is needed for the endpoints used here; the site itself sends an empty
"Authorization: Bearer " header for anonymous visitors.

  POST v1/booking/content/nowshowing   {"city": "Chennai"}
       -> output.mv[]: {id (filmCommonCode), n, films[{filmId, language, format}], ...}
  POST v1/booking/content/msessions    {"city", "mid", "lat", "lng", "dated": "YYYY-MM-DD" | "NA"}
       -> output.days[] (bookable dates) and output.movieCinemaSessions[]:
          {cinema{theatreId, name, latitude, longitude, ...},
           experienceSessions[{shows[{showDate, showTime "01:30 PM", status, statusTxt,
                                      encrypted, language, filmFormat, screenName, ...}]}]}
          show.status: 1 Available, 2 Filling Up Fast, 3 Housefull, 0 Lapsed (greyed, not on sale)
  POST v1/booking/ticketing/seatlayout {"dated", "encrypted", "onPage": false, "layoutType": "NEW"}
       -> output.showId, output.rows[].s[]: seat {sn (seat no, null = gap), s: 1 free, 2 sold, other = blocked}
          ("onPage": true returns the day's first show instead of the requested one.)

The chain header (PVR/INOX) does not change the results: one call covers both brands.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from typing import Any, Optional

from ticketfinder.checkers.base import DEFAULT_HEADERS, PlatformChecker
from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob

API_BASE = "https://api3.pvrcinemas.com/api/"
PLATFORM_LABEL = "PVR INOX"
MAX_SEATLAYOUT_CALLS = 40

KNOWN_LANGUAGES = ("tamil", "telugu", "hindi", "english", "malayalam", "kannada")
_TITLE_STOPWORDS = set(KNOWN_LANGUAGES) | {"version", "movie", "lang", "language", "2d", "3d", "imax"}

# show.status -> (ShowtimeSlot.status, site lets you click it)
_STATUS_MAP: dict[int, tuple[str, bool]] = {
    1: ("AVAILABLE", True),
    2: ("FAST_FILLING", True),
    3: ("SOLD_OUT", False),
    0: ("AVAILABLE", False),  # "Lapsed": greyed out, booking not open / closed
}


def _api_post(
    path: str,
    body: dict[str, Any],
    city: str,
    timeout: float = 12.0,
    allow_error: bool = False,
) -> Optional[dict[str, Any]]:
    """POSTs to the PVR API. Returns None on transport errors, and on result != "success"
    unless allow_error is set (the seat map's error message is itself a signal)."""
    headers = {
        "User-Agent": DEFAULT_HEADERS["User-Agent"],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.pvrcinemas.com",
        "Referer": "https://www.pvrcinemas.com/",
        "Authorization": "Bearer ",
        "chain": "PVR",
        "city": city,
        "appVersion": "1.0",
        "platform": "WEBSITE",
        "country": "INDIA",
    }
    payload = json.dumps(body)
    raw = ""
    # curl first: urllib tries the host's IPv6 addresses one by one, which stalls ~10 s
    # each on networks without IPv6 routing; curl uses happy-eyeballs.
    try:
        cmd = ["/usr/bin/curl", "-s", "--max-time", str(int(timeout)), "-X", "POST", API_BASE + path]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        cmd += ["--data-binary", payload]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        if proc.returncode == 0:
            raw = proc.stdout
    except Exception:
        raw = ""
    if not raw:
        try:
            req = urllib.request.Request(
                API_BASE + path, data=payload.encode("utf-8"), method="POST", headers=headers
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("result") != "success" and not allow_error:
        return None
    return data


def seatlayout_error(payload: Optional[dict[str, Any]]) -> Optional[str]:
    """
    The message when PVR refuses to open the seat map for a show, e.g.
    "Sorry, This Show is no longer available for booking" (code 12002). PVR's listing can
    keep showing such a dead session as "Available" (seen: INOX Marina 17 Dec 4:50 PM,
    replaced by a 4:55 PM show on the same screen), and District mirrors it. A show you
    can't open the seat map for can't be booked.
    """
    if not payload or payload.get("result") == "success":
        return None
    return str(payload.get("msg") or f"seat layout error {payload.get('code')}")


def _title_words(movie: str) -> tuple[list[str], Optional[str]]:
    raw = re.sub(r"[^a-z0-9]+", " ", movie.lower()).split()
    lang = next((w for w in raw if w in KNOWN_LANGUAGES), None)
    return [w for w in raw if w not in _TITLE_STOPWORDS], lang


def find_pvr_movie_ids(nowshowing: dict[str, Any], movie: str) -> list[tuple[str, str]]:
    """Returns [(movie_id, display_name)] from a nowshowing response matching the job's movie."""
    words, _ = _title_words(movie)
    if not words:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in (nowshowing.get("output") or {}).get("mv") or []:
        if not isinstance(m, dict):
            continue
        names = [str(m.get("n") or ""), str(m.get("filmName") or "")]
        names += [str(f.get("filmCommonName") or "") for f in m.get("films") or [] if isinstance(f, dict)]
        hay = re.sub(r"[^a-z0-9]+", " ", " ".join(names).lower())
        hay_words = set(hay.split())
        mid = str(m.get("id") or "")
        if mid and mid not in seen and all(w in hay_words for w in words):
            seen.add(mid)
            out.append((mid, str(m.get("n") or m.get("filmName") or movie)))
    return out


def _fmt_time(raw: str) -> str:
    """'01:30 PM' -> '1:30 PM' (same label District uses, so showtimes merge)."""
    return raw.strip().lstrip("0")


def parse_pvr_seatlayout(payload: dict[str, Any]) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """Returns (available_seats, total_seats, category_info) from a seatlayout response."""
    out = (payload or {}).get("output") or {}
    rows = out.get("rows")
    if not isinstance(rows, list):
        return None, None, None
    price_desc = {
        k: (v.get("description") or k, v.get("price"))
        for k, v in (out.get("priceList") or {}).items()
        if isinstance(v, dict)
    }
    total = avail = 0
    per_cat: dict[str, list[int]] = {}
    for row in rows:
        for seat in (row or {}).get("s") or []:
            if not isinstance(seat, dict) or not seat.get("sn"):
                continue
            total += 1
            cat = str(seat.get("pc") or seat.get("c") or "")
            bucket = per_cat.setdefault(cat, [0, 0])
            bucket[1] += 1
            if seat.get("s") == 1:
                avail += 1
                bucket[0] += 1
    cat_parts: list[str] = []
    for code, (a, t) in per_cat.items():
        label, price = price_desc.get(code, (code, None))
        price_s = ""
        try:
            if price:
                price_s = f" ₹{int(round(float(price)))}"
        except (TypeError, ValueError):
            pass
        cat_parts.append(f"{label}{price_s}: {a}/{t} left")
    return avail, total, (", ".join(cat_parts) or None)


def parse_pvr_msessions(
    payload: dict[str, Any],
    target_date: str,
    booking_url: str,
    language: Optional[str] = None,
    seat_lookup: Optional[Any] = None,
) -> list[CinemaVenue]:
    """
    Converts an msessions response to CinemaVenue objects.
    seat_lookup(show_dict) -> (avail, total, category_info[, bookable]) is called for shows
    the site marks as open (status 1/2) to get exact seat counts; a 4th element of False
    marks the show unbookable (seat map refused). Pass None to skip it.
    """
    out = (payload or {}).get("output") or {}
    is_any = target_date.upper() == "ANY"
    venues: list[CinemaVenue] = []
    for entry in out.get("movieCinemaSessions") or []:
        if not isinstance(entry, dict):
            continue
        cin = entry.get("cinema") or {}
        name = str(cin.get("name") or "").strip()
        if not name:
            continue
        lat = lon = None
        try:
            lat = float(cin["latitude"])
            lon = float(cin["longitude"])
        except (KeyError, TypeError, ValueError):
            lat = lon = None

        slots: list[ShowtimeSlot] = []
        for exp in entry.get("experienceSessions") or []:
            for show in (exp or {}).get("shows") or []:
                if not isinstance(show, dict):
                    continue
                s_date = str(show.get("showDate") or "")[:10]
                if not is_any and s_date and s_date != target_date:
                    continue
                if language and str(show.get("language") or "").lower() not in ("", language):
                    continue
                time_label = _fmt_time(str(show.get("showTime") or ""))
                if not time_label:
                    continue
                try:
                    code = int(show.get("status"))
                except (TypeError, ValueError):
                    code = -1
                status, bookable = _STATUS_MAP.get(code, ("AVAILABLE", False))

                avail = total = None
                cat_info = None
                if code == 3:
                    avail = 0
                if bookable and seat_lookup is not None:
                    try:
                        looked = seat_lookup(show)
                        avail, total, cat_info = looked[:3]
                        if len(looked) > 3 and looked[3] is False:
                            bookable = False  # seat map refused: dead / closed session
                    except Exception:
                        avail = total = cat_info = None

                fmt_parts = [str(show.get("filmFormat") or show.get("movieFormat") or "2D").strip()]
                screen = str(show.get("screenType") or show.get("screenName") or "").strip()
                if screen and screen.upper() not in fmt_parts[0].upper():
                    fmt_parts.append(screen)
                lang = str(show.get("language") or "").strip()
                if lang:
                    fmt_parts.append(lang)

                slots.append(
                    ShowtimeSlot(
                        time=time_label,
                        date=s_date or (None if is_any else target_date),
                        screen_format=" • ".join(fmt_parts),
                        status=status,
                        available_seats=avail,
                        total_seats=total,
                        category_info=cat_info or (str(show.get("statusTxt") or "") or None),
                        bookable=bookable,
                    )
                )
        if slots:
            venues.append(
                CinemaVenue(
                    name=name,
                    platform=PLATFORM_LABEL,
                    booking_url=booking_url,
                    showtimes=slots,
                    address=(str(cin.get("address1") or "").strip(" ,") or None),
                    latitude=lat,
                    longitude=lon,
                    show_date=slots[0].date,
                )
            )
    return venues


def _merge_same_cinema(venues: list[CinemaVenue]) -> list[CinemaVenue]:
    """Folds per-date / per-movie-id results for the same cinema into one venue."""
    by_name: dict[str, CinemaVenue] = {}
    for v in venues:
        cur = by_name.get(v.name)
        if cur is None:
            by_name[v.name] = v
            continue
        have = {(s.date, s.time) for s in cur.showtimes}
        cur.showtimes.extend(s for s in v.showtimes if (s.date, s.time) not in have)
    return list(by_name.values())


class PVRInoxChecker(PlatformChecker):
    platform_name = PLATFORM_LABEL

    def check(self, job: WatchJob) -> list[CinemaVenue]:
        city = job.city.strip().title()
        city_slug = job.city.strip().lower().replace(" ", "-")
        nowshowing = _api_post("v1/booking/content/nowshowing", {"city": city}, city)
        if not nowshowing:
            return []
        movies = find_pvr_movie_ids(nowshowing, job.movie)
        _, language = _title_words(job.movie)
        lat = f"{job.area_lat:.4f}" if job.area_lat is not None else "0.00"
        lng = f"{job.area_lon:.4f}" if job.area_lon is not None else "0.00"

        calls_left = [MAX_SEATLAYOUT_CALLS]

        def seat_lookup(show: dict[str, Any]) -> tuple:
            if calls_left[0] <= 0 or not show.get("encrypted"):
                return None, None, None
            calls_left[0] -= 1
            layout = _api_post(
                "v1/booking/ticketing/seatlayout",
                {
                    "dated": str(show.get("showDate") or "")[:10],
                    "encrypted": show["encrypted"],
                    # onPage=True means "date switched inside the seat page" and makes the
                    # API return the day's FIRST show instead of the requested one.
                    "onPage": False,
                    "layoutType": "NEW",
                },
                city,
                allow_error=True,
            )
            if not layout:
                return None, None, None
            err = seatlayout_error(layout)
            if err:
                return 0, None, err, False
            got = str(((layout.get("output") or {}).get("showId")) or "")
            if got and show.get("sessionId") is not None and got != str(show["sessionId"]):
                return None, None, None  # layout for a different show: don't trust it
            return parse_pvr_seatlayout(layout)

        venues: list[CinemaVenue] = []
        for mid, mname in movies:
            slug = re.sub(r"[^a-z0-9]+", "-", mname.lower()).strip("-")
            booking_url = f"https://www.pvrcinemas.com/moviesessions/{city_slug}/{slug}/{mid}"
            if job.target_date.upper() == "ANY":
                first = _api_post(
                    "v1/booking/content/msessions",
                    {"city": city, "mid": mid, "lat": lat, "lng": lng, "dated": "NA"},
                    city,
                )
                dates = [
                    str(d.get("dt"))
                    for d in ((first or {}).get("output") or {}).get("days") or []
                    if isinstance(d, dict) and d.get("dt") and d.get("enable", True)
                ]
            else:
                dates = [job.target_date]
            for dated in dates:
                payload = _api_post(
                    "v1/booking/content/msessions",
                    {"city": city, "mid": mid, "lat": lat, "lng": lng, "dated": dated},
                    city,
                )
                if payload:
                    venues.extend(
                        parse_pvr_msessions(
                            payload, dated, booking_url, language=language, seat_lookup=seat_lookup
                        )
                    )
        return _merge_same_cinema(venues)
