from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from ticketfinder.config import Settings

MONTH_MAP: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

WEEKDAY_MAP: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

KNOWN_CITIES: dict[str, str] = {
    "chennai": "Chennai",
    "madras": "Chennai",
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "mumbai": "Mumbai",
    "bombay": "Mumbai",
    "hyderabad": "Hyderabad",
    "delhi": "Delhi",
    "ncr": "Delhi",
    "noida": "Noida",
    "gurgaon": "Gurugram",
    "gurugram": "Gurugram",
    "pune": "Pune",
    "kochi": "Kochi",
    "cochin": "Kochi",
    "coimbatore": "Coimbatore",
    "madurai": "Madurai",
    "trichy": "Trichy",
    "salem": "Salem",
    "kolkata": "Kolkata",
    "ahmedabad": "Ahmedabad",
}

LOCALITY_TO_CITY: dict[str, str] = {
    "velachery": "Chennai",
    "anna nagar": "Chennai",
    "t nagar": "Chennai",
    "adyar": "Chennai",
    "royapettah": "Chennai",
    "vadapalani": "Chennai",
    "omr": "Chennai",
    "thoraipakkam": "Chennai",
    "navalur": "Chennai",
    "tambaram": "Chennai",
    "chromepet": "Chennai",
    "guindy": "Chennai",
    "mylapore": "Chennai",
    "nungambakkam": "Chennai",
    "egmore": "Chennai",
    "porur": "Chennai",
    "sholinganallur": "Chennai",
    "perungudi": "Chennai",
    "perumbakkam": "Chennai",
    "medavakkam": "Chennai",
    "pallikaranai": "Chennai",
    "siruseri": "Chennai",
    "kelambakkam": "Chennai",
    "koramangala": "Bengaluru",
    "indiranagar": "Bengaluru",
    "whitefield": "Bengaluru",
    "jayanagar": "Bengaluru",
    "marathahalli": "Bengaluru",
    "andheri": "Mumbai",
    "bandra": "Mumbai",
    "lower parel": "Mumbai",
    "gachibowli": "Hyderabad",
    "banjara hills": "Hyderabad",
    "edappally": "Kochi",
}


@dataclass
class ParsedNLCommand:
    intent: str = "WATCH"  # WATCH, STOP, STATUS
    movie: str = ""
    target_date: str = ""  # Primary YYYY-MM-DD
    target_dates: list[str] = field(default_factory=list)  # All extracted YYYY-MM-DD dates
    city: str = "Chennai"
    area: Optional[str] = None
    radius_km: float = 15.0
    cinemas: list[str] = field(default_factory=list)
    raw_query: str = ""


def parse_date_from_text(text: str, ref_date: Optional[date] = None) -> tuple[str, str]:
    """
    Extracts target date (YYYY-MM-DD) from natural language text and returns
    (iso_date, matched_substring_to_strip).
    """
    today = ref_date or date.today()
    lower = text.lower()

    # 1. Explicit ISO YYYY-MM-DD
    m_iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if m_iso:
        return m_iso.group(1), m_iso.group(0)

    # 2. DD/MM/YYYY or DD-MM-YYYY
    m_dmy = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", text)
    if m_dmy:
        d, mo, y = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        return date(y, mo, d).isoformat(), m_dmy.group(0)

    # 3. Optional weekday + "Month Day" (e.g., "this Friday Nov 6", "starting Nov 5")
    month_names = "|".join(MONTH_MAP.keys())
    weekday_names = "|".join(WEEKDAY_MAP.keys())
    m_md = re.search(
        rf"\b(?:(?:this|next|on|starting|from|after)\s+)?(?:(?:{weekday_names})\s*,?\s*)?({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(20\d{{2}}))?\b",
        lower,
    )
    if m_md:
        mo = MONTH_MAP[m_md.group(1)]
        d = int(m_md.group(2))
        y = int(m_md.group(3)) if m_md.group(3) else today.year
        candidate = date(y, mo, d)
        if not m_md.group(3) and candidate < today - timedelta(days=2):
            candidate = date(y + 1, mo, d)
        return candidate.isoformat(), m_md.group(0)

    # 4. "Day Month" (e.g., "6th Nov", "starting 5th November")
    m_dm = re.search(
        rf"\b(?:(?:this|next|on|starting|from|after)\s+)?(?:(?:{weekday_names})\s*,?\s*)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month_names})\.?(?:,?\s*(20\d{{2}}))?\b",
        lower,
    )
    if m_dm:
        d = int(m_dm.group(1))
        mo = MONTH_MAP[m_dm.group(2)]
        y = int(m_dm.group(3)) if m_dm.group(3) else today.year
        candidate = date(y, mo, d)
        if not m_dm.group(3) and candidate < today - timedelta(days=2):
            candidate = date(y + 1, mo, d)
        return candidate.isoformat(), m_dm.group(0)

    # 5. Relative keywords: "day after tomorrow", "tomorrow", "today"
    if "day after tomorrow" in lower:
        return (today + timedelta(days=2)).isoformat(), "day after tomorrow"
    if re.search(r"\btomorrow\b", lower):
        return (today + timedelta(days=1)).isoformat(), "tomorrow"
    if re.search(r"\btoday\b", lower):
        return today.isoformat(), "today"

    # 6. Weekday only (e.g., "this Friday", "on Saturday")
    m_wd = re.search(rf"\b(?:(this|next|on)\s+)?({weekday_names})\b", lower)
    if m_wd:
        target_wd = WEEKDAY_MAP[m_wd.group(2)]
        days_ahead = (target_wd - today.weekday()) % 7
        if days_ahead == 0 and m_wd.group(1) == "next":
            days_ahead = 7
        return (today + timedelta(days=days_ahead)).isoformat(), m_wd.group(0)

    # Default to "ANY" (any available date whenever bookings open) if no date specified
    return "ANY", ""


def _parse_with_gemini(query: str, api_key: str, default_city: str, ref_date: date) -> Optional[ParsedNLCommand]:
    """Optional Gemini LLM parser when GEMINI_API_KEY is configured."""
    if not api_key:
        return None
    prompt = (
        f"Today's date is {ref_date.isoformat()} ({ref_date.strftime('%A')}). "
        f"Parse this user movie ticket request into JSON with keys: "
        f"intent ('WATCH' or 'STOP'), movie (string), target_date ('YYYY-MM-DD'), "
        f"city (string, default '{default_city}'), area (string or null), "
        f"radius_km (number, default 15), cinemas (array of strings). "
        f"User query: {query!r}"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            text_out = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text_out)
            return ParsedNLCommand(
                intent=parsed.get("intent", "WATCH").upper(),
                movie=parsed.get("movie", "").strip(),
                target_date=parsed.get("target_date") or (ref_date + timedelta(days=1)).isoformat(),
                city=parsed.get("city") or default_city,
                area=parsed.get("area"),
                radius_km=float(parsed.get("radius_km") or 15.0),
                cinemas=[str(c).strip() for c in (parsed.get("cinemas") or []) if str(c).strip()],
                raw_query=query,
            )
    except Exception:
        return None


def parse_natural_language_query(
    query: str,
    settings: Optional[Settings] = None,
    ref_date: Optional[date] = None,
) -> ParsedNLCommand:
    """
    Parses a natural language movie ticket request into a structured ParsedNLCommand.
    Uses Gemini if GEMINI_API_KEY is set, otherwise uses deterministic regex/token parsing.
    """
    cfg = settings or Settings.from_env()
    today = ref_date or date.today()
    cleaned_query = query.strip()

    # Check if this is a stop command
    m_stop = re.match(
        r"^(?:please\s+)?(?:stop|cancel|disable|kill)\s*(?:watching|bot|agent|setup|all|for)?\s*(.*)$",
        cleaned_query,
        flags=re.IGNORECASE,
    )
    if m_stop:
        target_movie = m_stop.group(1).strip()
        if target_movie.lower() in ("all", "everything", "bot", "agent", "setup", ""):
            target_movie = ""
        return ParsedNLCommand(intent="STOP", movie=target_movie, raw_query=cleaned_query)

    # Try Gemini if configured
    if cfg.gemini_api_key:
        llm_res = _parse_with_gemini(cleaned_query, cfg.gemini_api_key, cfg.default_city, today)
        if llm_res and llm_res.movie:
            return llm_res

    working = cleaned_query

    # 1. Extract radius (e.g. "within 12km", "10-15 kms", "radius 12 km")
    radius_km = 15.0
    m_rad_range = re.search(
        r"\b(?:within|of|around|radius(?:\s+of)?)\s*(\d{1,2})\s*-\s*(\d{1,2})\s*(?:kms?|kilometers?)\b",
        working,
        flags=re.IGNORECASE,
    )
    if m_rad_range:
        radius_km = float(m_rad_range.group(2))
        working = working[: m_rad_range.start()] + " __SPLIT__ " + working[m_rad_range.end() :]
    else:
        m_rad = re.search(
            r"\b(?:within|around|radius(?:\s+of)?)?\s*(\d{1,2}(?:\.\d+)?)\s*(?:kms?|kilometers?)(?:\s+radius)?\b",
            working,
            flags=re.IGNORECASE,
        )
        if m_rad:
            radius_km = float(m_rad.group(1))
            working = working[: m_rad.start()] + " __SPLIT__ " + working[m_rad.end() :]

    # 2. Extract target date(s) (handles "on 7th nov or 9th nov")
    extracted_dates: list[str] = []
    for _ in range(4):
        d_iso, d_span = parse_date_from_text(working, ref_date=today)
        if not d_span:
            if not extracted_dates:
                extracted_dates.append(d_iso)
            break
        if d_iso not in extracted_dates:
            extracted_dates.append(d_iso)
        working = re.sub(
            rf"\b(?:on|for|or|0r|and)?\s*{re.escape(d_span)}\b",
            " __SPLIT__ ",
            working,
            count=1,
            flags=re.IGNORECASE,
        )
    iso_date = extracted_dates[0]

    # 3. Extract city
    detected_city: Optional[str] = None
    for city_key, canonical_city in KNOWN_CITIES.items():
        m_city = re.search(rf"\b(?:in|at)\s+{re.escape(city_key)}\b", working, flags=re.IGNORECASE)
        if m_city:
            detected_city = canonical_city
            working = working[: m_city.start()] + " __SPLIT__ " + working[m_city.end() :]
            break
    if not detected_city:
        for city_key, canonical_city in KNOWN_CITIES.items():
            m_city = re.search(rf"\b{re.escape(city_key)}\b", working, flags=re.IGNORECASE)
            if m_city:
                detected_city = canonical_city
                working = working[: m_city.start()] + " __SPLIT__ " + working[m_city.end() :]
                break

    # 4. Extract area / locality
    detected_area: Optional[str] = None
    for loc_key, loc_city in sorted(LOCALITY_TO_CITY.items(), key=lambda x: -len(x[0])):
        m_loc = re.search(
            rf"\b(?:near|around|in|at|area|locality)?\s*{re.escape(loc_key)}\b",
            working,
            flags=re.IGNORECASE,
        )
        if m_loc:
            detected_area = loc_key.title()
            if not detected_city:
                detected_city = loc_city
            working = working[: m_loc.start()] + " __SPLIT__ " + working[m_loc.end() :]
            break

    if not detected_area:
        m_area_phrase = re.search(
            r"\b(?:near|around|locality|area)\s+([A-Za-z][A-Za-z ]{2,22}?)(?=\s+(?:within|and|or|at|in|on|for|cinemas?|theatres?|__SPLIT__|$)|[,.;]|$)",
            working,
            flags=re.IGNORECASE,
        )
        if m_area_phrase:
            detected_area = m_area_phrase.group(1).strip().title()
            working = working[: m_area_phrase.start()] + " __SPLIT__ " + working[m_area_phrase.end() :]

    # 5. Extract cinemas (either via explicit cinema keywords or trailing clause after __SPLIT__)
    cinemas: list[str] = []
    segments = [s.strip() for s in working.split("__SPLIT__") if s.strip()]
    first_segment = segments[0] if segments else working
    trailing_segments = " ".join(segments[1:]) if len(segments) > 1 else ""

    known_brands = {
        "inox", "pvr", "cinepolis", "miraj", "ags", "sathyam", "palazzo",
        "rohini", "kamala", "vetri", "devi", "kasi", "luxe", "escape", "mayajaal",
    }

    def _add_cinema_token(raw_tok: str) -> None:
        words = [w for w in raw_tok.split() if w]
        if len(words) > 1 and all(w.lower() in known_brands for w in words):
            for w in words:
                fmt = w.upper() if w.lower() in ("pvr", "inox", "ags", "imax") else w.title()
                if fmt not in cinemas:
                    cinemas.append(fmt)
            return
        fmt = raw_tok.upper() if raw_tok.lower() in ("pvr", "inox", "ags", "imax") else raw_tok.title()
        if fmt not in cinemas:
            cinemas.append(fmt)

    if trailing_segments:
        parts = re.split(r"\s*(?:,|\band\b|\bor\b|\b0r\b|&)\s*", trailing_segments, flags=re.IGNORECASE)
        for p in parts:
            clean_p = re.sub(
                r"\b(restrict|restricted|only|the|following|to|preferred|also|check|include|watch|cinemas?|theatres?|screens?|multiplexes?|tickets?|in|at|near|for|on|0r)\b",
                "",
                p,
                flags=re.IGNORECASE,
            ).strip(" .,;-:")
            if clean_p and len(clean_p) >= 2:
                _add_cinema_token(clean_p)

    m_cinemas = re.search(
        r"\b(?:(?:and\s+)?(?:also\s+)?(?:restrict(?:ed)?\s+)?(?:only\s+)?(?:to\s+)?(?:the\s+)?(?:following\s+)?(?:preferred\s+)?(?:check|include|watch|at|in)?\s*(?:cinemas?|theatres?|screens?|multiplexes?)\s*[:=-]?\s*|(?:\b(?:or|and)\s+)?(?:also\s+)?(?:at|check)\s+)([A-Za-z0-9 ,&/+-]+)$",
        first_segment,
        flags=re.IGNORECASE,
    )
    if m_cinemas:
        raw_cinema_str = m_cinemas.group(1)
        first_segment = first_segment[: m_cinemas.start()]
        parts = re.split(r"\s*(?:,|\band\b|\bor\b|\b0r\b|&)\s*", raw_cinema_str, flags=re.IGNORECASE)
        for p in parts:
            clean_p = re.sub(
                r"\b(restrict|restricted|only|the|following|to|preferred|cinemas?|theatres?|tickets?|in|at|near|0r)\b", "", p, flags=re.IGNORECASE
            ).strip(" .,;-:")
            if clean_p and len(clean_p) >= 2:
                _add_cinema_token(clean_p)

    # 6. Extract movie name from first segment
    movie_candidate = first_segment
    # Strip common leading verbs/phrases
    movie_candidate = re.sub(
        r"^(?:please\s+)?(?:i\s+want\s+to\s+|can\s+you\s+)?(?:watch|check|find|alert\s+me|notify\s+me|let\s+me\s+know|track|monitor|book|set\s*up(?:\s+watcher)?)\s*"
        r"(?:when\s+|if\s+|for\s+)?(?:the\s+)?(?:movie\s+)?(?:tickets?\s+(?:for|of)\s+)?",
        "",
        movie_candidate,
        flags=re.IGNORECASE,
    )
    movie_candidate = re.sub(
        r"^(?:movie\s+)?tickets?\s+(?:for|of)\s+",
        "",
        movie_candidate,
        flags=re.IGNORECASE,
    )
    # Strip trailing filler tokens
    movie_candidate = re.sub(
        r"\b(?:is\s+open(?:ing|ed)?|open(?:ed|s|ing)?|movie\s+tickets?|tickets?\s+open(?:ed|s)?|tickets?|bookings?|in|at|on|for|near|around|within|and|or|also|check)\b",
        " ",
        movie_candidate,
        flags=re.IGNORECASE,
    )
    movie_candidate = re.sub(r"\b(?:tickets?|movie)\b", "", movie_candidate, flags=re.IGNORECASE)
    movie_candidate = re.sub(r"\s+", " ", movie_candidate).strip(" ,.-:;\"'")

    if not movie_candidate:
        movie_candidate = "Unknown Movie"
    elif movie_candidate.islower():
        movie_candidate = movie_candidate.title()

    return ParsedNLCommand(
        intent="WATCH",
        movie=movie_candidate,
        target_date=iso_date,
        target_dates=extracted_dates,
        city=detected_city or cfg.default_city or "Chennai",
        area=detected_area,
        radius_km=radius_km,
        cinemas=cinemas,
        raw_query=cleaned_query,
    )
