from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


def min_bookable_seats() -> int:
    """Minimum seats left for a show to count as bookable (env MIN_BOOKABLE_SEATS, default 4)."""
    import os

    try:
        return max(1, int(os.environ.get("MIN_BOOKABLE_SEATS", "4")))
    except ValueError:
        return 4


@dataclass
class ShowtimeSlot:
    """Represents a single showtime and its seat/ticket availability."""

    time: str
    date: Optional[str] = None  # YYYY-MM-DD
    screen_format: str = "2D"
    status: str = "AVAILABLE"  # AVAILABLE, FAST_FILLING, ALMOST_FULL, SOLD_OUT
    available_seats: Optional[int] = None
    booked_seats: Optional[int] = None
    total_seats: Optional[int] = None
    category_info: Optional[str] = None
    price_info: Optional[str] = None
    # Platform-reported bookability (False = greyed out / click disabled / booking closed).
    bookable: Optional[bool] = None

    def __post_init__(self) -> None:
        self.status = (self.status or "AVAILABLE").upper()
        if self.total_seats is not None and self.available_seats is not None and self.booked_seats is None:
            self.booked_seats = max(0, self.total_seats - self.available_seats)
        elif self.total_seats is not None and self.booked_seats is not None and self.available_seats is None:
            self.available_seats = max(0, self.total_seats - self.booked_seats)
        elif self.available_seats is not None and self.booked_seats is not None and self.total_seats is None:
            self.total_seats = self.available_seats + self.booked_seats

        # Auto-derive status from seat ratio if status is generic
        if self.total_seats and self.total_seats > 0 and self.available_seats is not None:
            if self.available_seats == 0:
                self.status = "SOLD_OUT"
            else:
                pct = self.occupancy_pct or 0.0
                if self.status == "AVAILABLE":
                    if pct >= 85.0:
                        self.status = "ALMOST_FULL"
                    elif pct >= 55.0:
                        self.status = "FAST_FILLING"

    @property
    def is_bookable(self) -> bool:
        """
        True only if the platform lets you click the show AND it has at least
        MIN_BOOKABLE_SEATS seats left. A handful of leftover seats in an otherwise
        full hall are usually blocked/held seats that cannot actually be selected.
        """
        if self.bookable is False:
            return False
        if self.status.upper() == "SOLD_OUT":
            return False
        if self.available_seats is not None:
            return self.available_seats >= min_bookable_seats()
        return True

    @property
    def occupancy_pct(self) -> Optional[float]:
        if self.total_seats and self.total_seats > 0 and self.booked_seats is not None:
            return round((self.booked_seats / self.total_seats) * 100.0, 1)
        return None

    @property
    def status_emoji(self) -> str:
        mapping = {
            "AVAILABLE": "🟢",
            "FAST_FILLING": "🟡",
            "ALMOST_FULL": "🟠",
            "SOLD_OUT": "🔴",
        }
        return mapping.get(self.status.upper(), "🟢")

    @property
    def status_label(self) -> str:
        mapping = {
            "AVAILABLE": "Available",
            "FAST_FILLING": "Fast Filling",
            "ALMOST_FULL": "Almost Full",
            "SOLD_OUT": "Sold Out",
        }
        return mapping.get(self.status.upper(), "Available")

    def format_seat_summary(self, prev_slot: Optional[ShowtimeSlot] = None) -> str:
        """Formats current available/booked seats and optional delta from previous snapshot."""
        parts: list[str] = []
        if self.available_seats is not None and self.booked_seats is not None:
            pct_int = int(round(self.occupancy_pct or 0))
            if self.status == "SOLD_OUT":
                parts.append(f"{self.status_emoji} Sold Out (0 avail / {self.booked_seats} booked, 100% full)")
            elif self.status in ("FAST_FILLING", "ALMOST_FULL"):
                parts.append(
                    f"{self.status_emoji} {self.status_label}: {self.available_seats} avail / "
                    f"{self.booked_seats} booked ({pct_int}% full)"
                )
            else:
                parts.append(
                    f"{self.status_emoji} {self.available_seats} avail / "
                    f"{self.booked_seats} booked ({pct_int}% full)"
                )
        else:
            base = f"{self.status_emoji} {self.status_label}"
            if self.category_info:
                base += f" ({self.category_info})"
            parts.append(base)

        if prev_slot is not None:
            if prev_slot.available_seats is not None and prev_slot.booked_seats is not None:
                same_hall = (
                    prev_slot.total_seats is None
                    or self.total_seats is None
                    or prev_slot.total_seats == self.total_seats
                )
                if not same_hall:
                    # Different source last time (e.g. District counts 197 sellable seats, PVR's
                    # seat map 221 incl. held ones): "booked" isn't comparable, seats left are.
                    if prev_slot.available_seats != self.available_seats:
                        parts.append(f"(was {prev_slot.available_seats} avail)")
                elif (
                    prev_slot.available_seats != self.available_seats
                    or prev_slot.booked_seats != self.booked_seats
                ):
                    parts.append(
                        f"(was {prev_slot.available_seats} avail / {prev_slot.booked_seats} booked)"
                    )
            elif prev_slot.status != self.status:
                parts.append(f"(was {prev_slot.status_label})")

        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShowtimeSlot:
        return cls(
            time=data.get("time", ""),
            date=data.get("date"),
            screen_format=data.get("screen_format", "2D"),
            status=data.get("status", "AVAILABLE"),
            available_seats=data.get("available_seats"),
            booked_seats=data.get("booked_seats"),
            total_seats=data.get("total_seats"),
            category_info=data.get("category_info"),
            price_info=data.get("price_info"),
            bookable=data.get("bookable"),
        )


@dataclass
class CinemaVenue:
    """Represents an open cinema venue on one or more ticketing platforms."""

    name: str
    platform: str
    booking_url: str
    showtimes: list[ShowtimeSlot] = field(default_factory=list)
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    distance_km: Optional[float] = None
    matched_by: str = "requested_cinema"  # requested_cinema, nearby_locality, both
    first_seen_at: Optional[str] = None
    extra_platforms: list[tuple[str, str]] = field(default_factory=list)  # (platform, url)
    show_date: Optional[str] = None  # YYYY-MM-DD when known

    @property
    def dates(self) -> list[str]:
        """Returns sorted unique ISO dates (YYYY-MM-DD) discovered on this venue."""
        seen: set[str] = set()
        res: list[str] = []
        for s in self.showtimes:
            if s.date and s.date not in seen:
                seen.add(s.date)
                res.append(s.date)
        if not res and self.show_date:
            res.append(self.show_date)
        return sorted(res)

    def friendly_dates_label(self) -> str:
        """Formatted human dates, e.g. 'Sat, 21 Nov 2026'."""
        from datetime import datetime

        labels: list[str] = []
        for d in self.dates:
            try:
                labels.append(datetime.strptime(d, "%Y-%m-%d").strftime("%a, %d %b %Y"))
            except Exception:
                labels.append(d)
        return ", ".join(labels)

    @property
    def normalized_key(self) -> str:
        """Canonical key for deduplicating the same cinema across checks."""
        import re

        cleaned = re.sub(r"[^a-z0-9]+", " ", self.name.lower()).strip()
        return cleaned

    def distance_badge(self, area_name: Optional[str] = None) -> str:
        if self.distance_km is not None:
            if area_name:
                return f"{self.distance_km:.1f} km from {area_name}"
            return f"{self.distance_km:.1f} km away"
        if self.matched_by == "requested_cinema":
            return "Requested Cinema"
        return "Open"

    def all_platform_links(self) -> list[tuple[str, str]]:
        links = [(self.platform, self.booking_url)]
        seen = {self.platform.lower()}
        for plat, url in self.extra_platforms:
            if plat.lower() not in seen:
                seen.add(plat.lower())
                links.append((plat, url))
        return links

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "platform": self.platform,
            "booking_url": self.booking_url,
            "showtimes": [s.to_dict() for s in self.showtimes],
            "address": self.address,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "distance_km": self.distance_km,
            "matched_by": self.matched_by,
            "first_seen_at": self.first_seen_at,
            "extra_platforms": self.extra_platforms,
            "show_date": self.show_date,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CinemaVenue:
        return cls(
            name=data["name"],
            platform=data.get("platform", "Unknown"),
            booking_url=data.get("booking_url", ""),
            showtimes=[ShowtimeSlot.from_dict(s) for s in data.get("showtimes", [])],
            address=data.get("address"),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
            distance_km=data.get("distance_km"),
            matched_by=data.get("matched_by", "requested_cinema"),
            first_seen_at=data.get("first_seen_at"),
            extra_platforms=[tuple(x) for x in data.get("extra_platforms", [])],
            show_date=data.get("show_date"),
        )


@dataclass
class WatchJob:
    """User watch configuration stored in SQLite."""

    movie: str
    target_date: str  # YYYY-MM-DD
    city: str
    raw_query: str = ""
    area: Optional[str] = None
    area_lat: Optional[float] = None
    area_lon: Optional[float] = None
    radius_km: float = 15.0
    cinemas: list[str] = field(default_factory=list)
    status: str = "ACTIVE"  # ACTIVE, STOPPED, COMPLETED
    id: Optional[int] = None
    check_count: int = 0
    alert_count: int = 0
    created_at: Optional[str] = None
    last_checked_at: Optional[str] = None

    @property
    def target_summary(self) -> str:
        parts: list[str] = []
        if self.area:
            parts.append(f"{self.area}, {self.city} (within {self.radius_km:g} km)")
        else:
            parts.append(self.city)
        if self.cinemas:
            parts.append("Cinemas: " + ", ".join(self.cinemas))
        return " + ".join(parts)


@dataclass
class ExistingCinemaUpdate:
    """A previously opened cinema that either added showtimes or updated seat counts."""

    venue: CinemaVenue
    added_showtimes: list[ShowtimeSlot] = field(default_factory=list)
    previous_slots_by_time: dict[str, ShowtimeSlot] = field(default_factory=dict)


@dataclass
class DiffReport:
    """Result of comparing the current multi-site check against stored snapshot history."""

    job: WatchJob
    update_number: int
    newly_opened_cinemas: list[CinemaVenue] = field(default_factory=list)
    new_showtimes_cinemas: list[ExistingCinemaUpdate] = field(default_factory=list)
    previously_opened_cinemas: list[ExistingCinemaUpdate] = field(default_factory=list)
    is_initial_check: bool = False
    sites_checked: list[str] = field(
        default_factory=lambda: ["BookMyShow", "District (Paytm)", "TicketNew"]
    )
    checked_at: str = ""

    @property
    def has_new_openings(self) -> bool:
        """True only if a new cinema or new showtime has bookable (not sold-out) seats."""
        for v in self.newly_opened_cinemas:
            if any(s.is_bookable for s in v.showtimes):
                return True
        for u in self.new_showtimes_cinemas:
            if any(s.is_bookable for s in u.added_showtimes):
                return True
        return False

    @property
    def bookable_show_count(self) -> int:
        """Number of currently open showtimes that still have seats available."""
        all_venues = (
            self.newly_opened_cinemas
            + [u.venue for u in self.new_showtimes_cinemas]
            + [u.venue for u in self.previously_opened_cinemas]
        )
        return sum(1 for v in all_venues for s in v.showtimes if s.is_bookable)

    @property
    def any_tickets_open(self) -> bool:
        return bool(
            self.newly_opened_cinemas
            or self.new_showtimes_cinemas
            or self.previously_opened_cinemas
        )

    @property
    def discovered_dates(self) -> list[str]:
        """Returns sorted unique ISO dates (YYYY-MM-DD) discovered across all open venues."""
        seen: set[str] = set()
        res: list[str] = []
        all_venues = (
            self.newly_opened_cinemas
            + [u.venue for u in self.new_showtimes_cinemas]
            + [u.venue for u in self.previously_opened_cinemas]
        )
        for v in all_venues:
            for d in v.dates:
                if d and d not in seen:
                    seen.add(d)
                    res.append(d)
        return sorted(res)


def bookable_only_report(report: DiffReport) -> DiffReport:
    """
    Returns a copy of the report that keeps only showtimes you can actually book,
    dropping cinemas left with none. Used for notifications; snapshots keep everything.
    """
    from dataclasses import replace

    def _venue(v: CinemaVenue) -> Optional[CinemaVenue]:
        shows = [s for s in v.showtimes if s.is_bookable]
        return replace(v, showtimes=shows) if shows else None

    newly = [nv for nv in (_venue(v) for v in report.newly_opened_cinemas) if nv]
    new_shows: list[ExistingCinemaUpdate] = []
    for u in report.new_showtimes_cinemas:
        added = [s for s in u.added_showtimes if s.is_bookable]
        nv = _venue(u.venue)
        if added and nv:
            new_shows.append(replace(u, venue=nv, added_showtimes=added))
    prev: list[ExistingCinemaUpdate] = []
    for u in report.previously_opened_cinemas:
        nv = _venue(u.venue)
        if nv:
            prev.append(replace(u, venue=nv))
    return replace(
        report,
        newly_opened_cinemas=newly,
        new_showtimes_cinemas=new_shows,
        previously_opened_cinemas=prev,
    )
