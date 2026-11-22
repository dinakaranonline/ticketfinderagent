"""
Cinépolis India direct checker (cinepolisindia.com).

The Next.js site reads a JSON API at https://api_new.cinepolisindia.com/api (Vista-backed).
The endpoints below need no login (the site only adds a token for signed-in users):

  GET movies/cities                                  -> data[] {city_id, city_name}
  GET movies/now-playing/?city_id=38                 -> data[] {ID "HO00003303", Title, ...}
  GET movies/show-dates/?movie_id=ID&isVip=N         -> data[] {ShowTime "2026-12-17T13:30:00.000Z"}
  GET movies/show-times/ID/?show_date=YYYY-MM-DD&movie_id=ID&isVip=N
      -> data[] {CinemaId, Screens[{ScreenName, ShowTimes[{SessionId, Showtime, SeatsAvailable,
                 AllowTicketSales, SoldoutStatus, is_active, CinemaName, cinema_address,
                 cinema_lat, cinema_long, ...}]}]}
  GET booking/seat-layout/{SessionId}/{CinemaId}     -> full seat map (not needed: show-times
                                                        already carries SeatsAvailable)
  (city_id is also sent as a "city_id" request header by the site.)

Showtime values are cinema-local wall-clock time with a misleading "Z" suffix
(Vista convention): "2026-12-18T13:30:00.000Z" is the 1:30 PM IST show.

IMPORTANT: api_new.cinepolisindia.com sits behind a Cloudflare WAF that answers
non-browser clients (urllib, curl) with HTTP 403 "Attention Required". The API is only
reachable from a real browser session. This checker does a plain request and, when it
gets the Cloudflare block page, returns no venues and records the reason in
`CinepolisChecker.last_error` — it does not try to get around the block.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
import urllib.parse
from datetime import datetime
from typing import Any, Optional

from ticketfinder.checkers.base import DEFAULT_HEADERS, PlatformChecker
from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob

API_BASE = "https://api_new.cinepolisindia.com/api/"
PLATFORM_LABEL = "Cinepolis"

KNOWN_LANGUAGES = ("tamil", "telugu", "hindi", "english", "malayalam", "kannada")
_TITLE_STOPWORDS = set(KNOWN_LANGUAGES) | {"version", "movie", "lang", "language", "2d", "3d", "imax"}
_LANG_CODES = {"english": "eng", "tamil": "tam", "telugu": "tel", "hindi": "hin", "malayalam": "mal", "kannada": "kan"}


class CinepolisBlocked(Exception):
    """Raised when Cloudflare refuses the request (non-browser client)."""


def is_cloudflare_block(status: int, body: str) -> bool:
    head = body[:3000].lower()
    return status in (403, 503) and ("cloudflare" in head or "attention required" in head or "just a moment" in head)


def _api_get(path: str, params: dict[str, str], city_id: Optional[str] = None, timeout: float = 12.0) -> dict[str, Any]:
    url = API_BASE + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": DEFAULT_HEADERS["User-Agent"],
        "Accept": "application/json",
        "Origin": "https://cinepolisindia.com",
        "Referer": "https://cinepolisindia.com/",
    }
    if city_id:
        headers["city_id"] = city_id
    # curl rather than urllib: Python's ssl module rejects the *.cinepolisindia.com
    # certificate for the underscore host "api_new", and urllib also stalls on IPv6.
    cmd = ["/usr/bin/curl", "-s", "--max-time", str(int(timeout)), "-w", "\n%{http_code}", url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode} for {path}")
    body, _, code = proc.stdout.rpartition("\n")
    status = int(code) if code.isdigit() else 0
    if is_cloudflare_block(status, body):
        raise CinepolisBlocked(f"Cloudflare blocked {path} (HTTP {status})")
    if status != 200:
        raise RuntimeError(f"HTTP {status} for {path}")
    data = json.loads(body)
    return data if isinstance(data, dict) else {}


def find_city_id(cities: dict[str, Any], city: str) -> Optional[str]:
    want = city.strip().lower()
    for c in cities.get("data") or []:
        if isinstance(c, dict) and str(c.get("city_name") or "").strip().lower() == want:
            return str(c.get("city_id"))
    return None


def find_cinepolis_movie_ids(now_playing: dict[str, Any], movie: str) -> list[tuple[str, str]]:
    """Returns [(ID, Title)] of now-playing entries matching the job's movie title (and language)."""
    raw = re.sub(r"[^a-z0-9]+", " ", movie.lower()).split()
    lang = next((w for w in raw if w in KNOWN_LANGUAGES), None)
    words = [w for w in raw if w not in _TITLE_STOPWORDS]
    if not words:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in now_playing.get("data") or []:
        if not isinstance(m, dict):
            continue
        title = str(m.get("Title") or m.get("movie_title") or "")
        hay = set(re.sub(r"[^a-z0-9]+", " ", f"{title} {m.get('movie_title') or ''}".lower()).split())
        if not all(w in hay for w in words):
            continue
        if lang:
            m_lang = f"{m.get('movie_lang') or ''} {title}".lower()
            # English versions are labelled "ENG"; others usually carry the language name
            if lang not in m_lang and not re.search(rf"\b{_LANG_CODES[lang]}\b", m_lang):
                continue
        mid = str(m.get("ID") or "")
        if mid and mid not in seen:
            seen.add(mid)
            out.append((mid, title))
    return out


def _local_time(raw: str) -> tuple[str, str]:
    """'2026-12-18T13:30:00.000Z' (local wall clock) -> ('2026-12-18', '1:30 PM')."""
    try:
        dt = datetime.strptime(raw[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return "", ""
    return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p").lstrip("0")


def parse_cinepolis_showtimes(
    payload: dict[str, Any],
    target_date: str,
    booking_url: str,
) -> list[CinemaVenue]:
    is_any = target_date.upper() == "ANY"
    venues: dict[str, CinemaVenue] = {}
    for cinema in payload.get("data") or []:
        if not isinstance(cinema, dict):
            continue
        for screen in cinema.get("Screens") or []:
            for s in (screen or {}).get("ShowTimes") or []:
                if not isinstance(s, dict):
                    continue
                s_date, time_label = _local_time(str(s.get("Showtime") or ""))
                if not time_label:
                    continue
                if not is_any and s_date != target_date:
                    continue
                # ASCII-fold "Cinépolis" so the name key matches other platforms' spelling
                name = unicodedata.normalize(
                    "NFKD", str(s.get("CinemaName") or s.get("cinema_name") or "")
                ).encode("ascii", "ignore").decode().strip()
                if not name:
                    continue

                avail: Optional[int] = None
                try:
                    if s.get("SeatsAvailable") is not None:
                        avail = int(s["SeatsAvailable"])
                except (TypeError, ValueError):
                    pass
                sold_out = str(s.get("SoldoutStatus") or "0") not in ("0", "") or avail == 0
                bookable = (
                    str(s.get("AllowTicketSales")) == "1"
                    and str(s.get("is_active") or "Y").upper() == "Y"
                    and not sold_out
                )
                slot = ShowtimeSlot(
                    time=time_label,
                    date=s_date or None,
                    screen_format=str(s.get("ScreenName") or screen.get("ScreenName") or "2D"),
                    status="SOLD_OUT" if sold_out else "AVAILABLE",
                    available_seats=avail,
                    bookable=bookable,
                )
                venue = venues.get(name)
                if venue is None:
                    lat = lon = None
                    try:
                        lat = float(s["cinema_lat"])
                        lon = float(s["cinema_long"])
                    except (KeyError, TypeError, ValueError):
                        lat = lon = None
                    venue = venues[name] = CinemaVenue(
                        name=name,
                        platform=PLATFORM_LABEL,
                        booking_url=booking_url,
                        address=s.get("cinema_address") or None,
                        latitude=lat,
                        longitude=lon,
                        show_date=slot.date,
                    )
                venue.showtimes.append(slot)
    return list(venues.values())


class CinepolisChecker(PlatformChecker):
    platform_name = PLATFORM_LABEL
    last_error: Optional[str] = None

    def check(self, job: WatchJob) -> list[CinemaVenue]:
        try:
            return self._check(job)
        except Exception as e:  # CinepolisBlocked (Cloudflare 403) is the usual case
            CinepolisChecker.last_error = str(e)
            return []

    def _check(self, job: WatchJob) -> list[CinemaVenue]:
        city_id = find_city_id(_api_get("movies/cities", {}), job.city)
        if not city_id:
            return []
        now_playing = _api_get("movies/now-playing/", {"city_id": city_id}, city_id)
        all_venues: list[CinemaVenue] = []
        for mid, _title in find_cinepolis_movie_ids(now_playing, job.movie):
            booking_url = f"https://cinepolisindia.com/movie-details/{mid}"
            if job.target_date.upper() == "ANY":
                sd = _api_get("movies/show-dates/", {"movie_id": mid, "isVip": "N", "city_id": city_id}, city_id)
                dates = sorted({str(d.get("ShowTime") or "")[:10] for d in sd.get("data") or [] if isinstance(d, dict)} - {""})
            else:
                dates = [job.target_date]
            for d in dates:
                payload = _api_get(
                    f"movies/show-times/{mid}/",
                    {"show_date": d, "movie_id": mid, "isVip": "N", "city_id": city_id},
                    city_id,
                )
                all_venues.extend(parse_cinepolis_showtimes(payload, d, booking_url))
        CinepolisChecker.last_error = None
        return all_venues
