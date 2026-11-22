"""Parser tests for the direct cinema-chain checkers, using payloads captured on 2026-11-22."""

import json
import unittest
from pathlib import Path

from ticketfinder.checkers.cinepolis import (
    find_cinepolis_movie_ids,
    find_city_id,
    is_cloudflare_block,
    parse_cinepolis_showtimes,
)
from ticketfinder.checkers.pvrinox import (
    find_pvr_movie_ids,
    parse_pvr_msessions,
    parse_pvr_seatlayout,
)
from ticketfinder.matcher import merge_venues_across_platforms
from ticketfinder.models import CinemaVenue, ShowtimeSlot

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestPVRInoxParser(unittest.TestCase):
    def test_movie_lookup(self) -> None:
        ns = _load("pvr_nowshowing_chennai.json")
        ids = find_pvr_movie_ids(ns, "Avengers Doomsday")
        self.assertEqual([i for i, _ in ids], ["35312"])
        self.assertEqual(find_pvr_movie_ids(ns, "Avengers Doomsday Tamil")[0][0], "35312")
        self.assertEqual(find_pvr_movie_ids(ns, "Some Other Film"), [])

    def test_msessions_status_mapping(self) -> None:
        payload = _load("pvr_msessions_2026-12-18.json")
        venues = parse_pvr_msessions(payload, "2026-12-18", "https://pvr/url")
        by_name = {v.name: v for v in venues}
        self.assertEqual(len(venues), 3)

        marina = by_name["INOX The Marina Mall, OMR, Chennai"]
        self.assertAlmostEqual(marina.latitude, 12.83598504)
        self.assertEqual(marina.showtimes[0].time, "1:30 PM")  # leading zero stripped
        self.assertEqual(marina.showtimes[0].date, "2026-12-18")
        # "Lapsed" (status 0) shows are greyed out on the site
        self.assertTrue(all(s.bookable is False for s in marina.showtimes))
        self.assertFalse(any(s.is_bookable for s in marina.showtimes))

        aero = by_name["PVR AEROHUB Chennai"]
        housefull = [s for s in aero.showtimes if s.time == "1:00 PM"][0]
        self.assertEqual(housefull.status, "SOLD_OUT")
        self.assertEqual(housefull.available_seats, 0)
        self.assertFalse(housefull.is_bookable)

        ampa = by_name["PVR, Ampa Mall, Nelson Manickam Road Chennai"]
        self.assertEqual(len(ampa.showtimes), 6)
        self.assertTrue(all(s.bookable for s in ampa.showtimes))
        self.assertTrue(all(s.status == "FAST_FILLING" for s in ampa.showtimes))
        self.assertIn("Tamil", ampa.showtimes[0].screen_format)

    def test_language_filter_and_date_filter(self) -> None:
        payload = _load("pvr_msessions_2026-12-18.json")
        tamil = parse_pvr_msessions(payload, "2026-12-18", "u", language="tamil")
        self.assertEqual([v.name for v in tamil], ["PVR, Ampa Mall, Nelson Manickam Road Chennai"])
        self.assertEqual(parse_pvr_msessions(payload, "2026-12-19", "u"), [])
        self.assertEqual(len(parse_pvr_msessions(payload, "ANY", "u")), 3)

    def test_seat_lookup_only_for_open_shows(self) -> None:
        payload = _load("pvr_msessions_2026-12-18.json")
        asked: list[int] = []

        def lookup(show: dict) -> tuple:
            asked.append(show["sessionId"])
            return 27, 539, "CLASSIC ₹94: 17/75 left"

        venues = parse_pvr_msessions(payload, "2026-12-18", "u", seat_lookup=lookup)
        self.assertEqual(len(asked), 6)  # only the 6 status-2 Ampa shows
        ampa = [v for v in venues if v.name.startswith("PVR, Ampa")][0]
        slot = ampa.showtimes[0]
        self.assertEqual((slot.available_seats, slot.total_seats, slot.booked_seats), (27, 539, 512))
        self.assertTrue(slot.is_bookable)

    def test_seatlayout_counts(self) -> None:
        avail, total, cats = parse_pvr_seatlayout(_load("pvr_seatlayout_sample.json"))
        self.assertEqual((avail, total), (28, 539))
        self.assertIn("CLASSIC ₹94: 18/75 left", cats)
        self.assertIn("PRIME ₹224: 10/464 left", cats)
        self.assertEqual(parse_pvr_seatlayout({}), (None, None, None))


class TestCinepolisParser(unittest.TestCase):
    def test_city_and_movie_lookup(self) -> None:
        cities = {"data": [{"city_id": 9, "city_name": "Delhi"}, {"city_id": 38, "city_name": "Chennai"}]}
        self.assertEqual(find_city_id(cities, "chennai"), "38")
        now_playing = {
            "data": [
                {"ID": "HO00003303", "Title": "AVENGERS: DOOMSDAY INFINITY VISION(ENG WI ENG SUB)"},
                {"ID": "HO00009999", "Title": "SOME OTHER FILM"},
            ]
        }
        self.assertEqual(find_cinepolis_movie_ids(now_playing, "Avengers Doomsday")[0][0], "HO00003303")
        self.assertEqual(find_cinepolis_movie_ids(now_playing, "Avengers Doomsday English")[0][0], "HO00003303")
        self.assertEqual(find_cinepolis_movie_ids(now_playing, "Avengers Doomsday Tamil"), [])

    def test_showtimes(self) -> None:
        payload = _load("cinepolis_showtimes_2026-12-18.json")
        venues = parse_cinepolis_showtimes(payload, "2026-12-18", "https://cine/url")
        self.assertEqual(len(venues), 1)
        v = venues[0]
        self.assertEqual(v.name, "Cinepolis BSR Mall Chennai")  # accent folded
        self.assertAlmostEqual(v.latitude, 12.949551)
        self.assertEqual([s.time for s in v.showtimes], ["1:30 PM", "4:45 PM", "8:00 PM", "11:15 PM"])
        self.assertEqual([s.available_seats for s in v.showtimes], [9, 2, 14, 14])
        self.assertTrue(all(s.bookable for s in v.showtimes))
        # 2 seats left is below MIN_BOOKABLE_SEATS (4)
        self.assertEqual([s.is_bookable for s in v.showtimes], [True, False, True, True])
        self.assertEqual(parse_cinepolis_showtimes(payload, "2026-12-19", "u"), [])

    def test_sales_closed_and_sold_out(self) -> None:
        payload = _load("cinepolis_showtimes_2026-12-18.json")
        shows = payload["data"][0]["Screens"][0]["ShowTimes"]
        shows[0]["AllowTicketSales"] = 0
        shows[2]["SoldoutStatus"] = 1
        v = parse_cinepolis_showtimes(payload, "2026-12-18", "u")[0]
        self.assertFalse(v.showtimes[0].bookable)
        self.assertEqual(v.showtimes[2].status, "SOLD_OUT")
        self.assertFalse(v.showtimes[2].is_bookable)

    def test_cloudflare_block_detection(self) -> None:
        html = "<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title>"
        self.assertTrue(is_cloudflare_block(403, html))
        self.assertFalse(is_cloudflare_block(200, '{"status":true}'))


class TestCrossPlatformMerge(unittest.TestCase):
    def test_same_cinema_different_names_merge(self) -> None:
        district = CinemaVenue(
            name="INOX The Marina Mall, OMR, Egatoor, Chennai",
            platform="District (Paytm)",
            booking_url="https://district/x",
            latitude=12.8360,
            longitude=80.2294,
            showtimes=[ShowtimeSlot(time="1:30 PM", date="2026-12-18", available_seats=0, total_seats=200, bookable=False)],
        )
        pvr = parse_pvr_msessions(_load("pvr_msessions_2026-12-18.json"), "2026-12-18", "https://pvr/x")
        merged = merge_venues_across_platforms([district] + pvr)
        marina = [v for v in merged if "marina" in v.name.lower()]
        self.assertEqual(len(marina), 1)
        self.assertEqual(marina[0].name, district.name)  # first (aggregator) name kept
        self.assertIn(("PVR INOX", "https://pvr/x"), marina[0].extra_platforms)
        self.assertEqual(len(marina[0].showtimes), 4)  # 1:30 PM merged, 3 more added
        self.assertEqual(len(merged), 3)

    def test_open_on_chain_site_overrides_greyed_aggregator_slot(self) -> None:
        district = CinemaVenue(
            name="Cinepolis BSR Mall OMR Thoraipakkam Chennai",
            platform="District (Paytm)",
            booking_url="https://district/y",
            latitude=12.9496,
            longitude=80.2403,
            showtimes=[ShowtimeSlot(time="1:30 PM", date="2026-12-18", bookable=False)],
        )
        cine = parse_cinepolis_showtimes(_load("cinepolis_showtimes_2026-12-18.json"), "2026-12-18", "https://cine/y")
        merged = merge_venues_across_platforms([district] + cine)
        self.assertEqual(len(merged), 1)
        slot = [s for s in merged[0].showtimes if s.time == "1:30 PM"][0]
        self.assertTrue(slot.bookable)
        self.assertEqual(slot.available_seats, 9)

    def test_different_cinemas_do_not_merge(self) -> None:
        a = CinemaVenue(name="PVR Grand Mall Velachery", platform="District (Paytm)", booking_url="a",
                        latitude=12.9721, longitude=80.2209)
        b = CinemaVenue(name="PVR Grand Galada Mall Pallavaram", platform="PVR INOX", booking_url="b",
                        latitude=12.9675, longitude=80.1491)
        self.assertEqual(len(merge_venues_across_platforms([a, b])), 2)


if __name__ == "__main__":
    unittest.main()


class TestDeadSessions(unittest.TestCase):
    """INOX Marina 17 Dec 4:50 PM: listed "Available, 462 seats" on PVR and District,
    but PVR's seat map says "Sorry, This Show is no longer available for booking"."""

    def test_seatlayout_error_message(self) -> None:
        from ticketfinder.checkers.pvrinox import seatlayout_error

        err = {"status": 500, "code": 12002, "result": "error",
               "msg": "Sorry, This Show is no longer available for booking", "output": None}
        self.assertIn("no longer available", seatlayout_error(err))
        self.assertIsNone(seatlayout_error(_load("pvr_seatlayout_sample.json")))

    def test_refused_seat_map_marks_show_unbookable(self) -> None:
        payload = _load("pvr_msessions_2026-12-18.json")
        venues = parse_pvr_msessions(
            payload, "2026-12-18", "u",
            seat_lookup=lambda show: (0, None, "Sorry, This Show is no longer available for booking", False),
        )
        ampa = [v for v in venues if v.name.startswith("PVR, Ampa")][0]
        self.assertTrue(all(s.bookable is False and not s.is_bookable for s in ampa.showtimes))

    def test_chain_says_dead_overrides_aggregator_available(self) -> None:
        district = CinemaVenue(
            name="INOX The Marina Mall OMR, Egatoor, Chennai", platform="District (Paytm)",
            booking_url="d", latitude=12.8360, longitude=80.2294,
            showtimes=[ShowtimeSlot(time="4:50 PM", date="2026-12-17", available_seats=462,
                                    total_seats=510, bookable=True)],
        )
        pvr = CinemaVenue(
            name="INOX The Marina Mall, OMR, Chennai", platform="PVR INOX",
            booking_url="p", latitude=12.83598504, longitude=80.22942733,
            showtimes=[ShowtimeSlot(time="4:50 PM", date="2026-12-17", available_seats=0, bookable=False,
                                    category_info="Sorry, This Show is no longer available for booking")],
        )
        merged = merge_venues_across_platforms([district, pvr])
        self.assertEqual(len(merged), 1)
        slot = merged[0].showtimes[0]
        self.assertFalse(slot.is_bookable)
        self.assertEqual(slot.available_seats, 0)
        # A later aggregator saying "available" does not undo the chain's answer.
        bms = CinemaVenue(name="INOX The Marina Mall OMR, Egatoor, Chennai", platform="BookMyShow",
                          booking_url="b", showtimes=[ShowtimeSlot(time="4:50 PM", date="2026-12-17",
                                                                   available_seats=300, bookable=True)])
        merged = merge_venues_across_platforms([district, pvr, bms])
        self.assertFalse(merged[0].showtimes[0].is_bookable)
