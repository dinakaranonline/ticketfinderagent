import tempfile
import unittest
from pathlib import Path

from ticketfinder.geo import geocode_location, haversine_km
from ticketfinder.matcher import filter_and_rank_venues
from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob
from ticketfinder.storage import Storage


class TestGeoAndMatcher(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp_dir.name) / "test.db")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_haversine_and_known_geocoding(self) -> None:
        velachery = geocode_location("Velachery", "Chennai", storage=self.storage)
        self.assertIsNotNone(velachery)
        grand_mall = geocode_location("PVR Grand Mall Velachery", "Chennai", storage=self.storage)
        self.assertIsNotNone(grand_mall)
        dist = haversine_km(velachery[0], velachery[1], grand_mall[0], grand_mall[1])  # type: ignore[index]
        self.assertLess(dist, 3.0)

    def test_filter_and_rank_by_radius_and_requested_cinema(self) -> None:
        job = WatchJob(
            movie="Coolie",
            target_date="2026-11-06",
            city="Chennai",
            area="Velachery",
            area_lat=12.9755,
            area_lon=80.2207,
            radius_km=12.0,
            cinemas=["Rohini"],
        )

        venues = [
            # Within 2 km of Velachery (should match by nearby_locality)
            CinemaVenue(
                name="PVR: Grand Mall, Velachery",
                platform="BookMyShow",
                booking_url="https://bms/1",
                latitude=12.9719,
                longitude=80.2185,
                showtimes=[ShowtimeSlot(time="10:15 AM")],
            ),
            # Duplicate on District with exact seat counts (should merge)
            CinemaVenue(
                name="PVR: Grand Mall, Velachery",
                platform="District",
                booking_url="https://district/1",
                latitude=12.9719,
                longitude=80.2185,
                showtimes=[
                    ShowtimeSlot(
                        time="10:15 AM",
                        available_seats=180,
                        booked_seats=70,
                        total_seats=250,
                    )
                ],
            ),
            # 11.4 km away (outside 10km, inside 12km -> should match)
            CinemaVenue(
                name="Rohini Silver Screens, Koyambedu",
                platform="BookMyShow",
                booking_url="https://bms/2",
                latitude=13.0766,
                longitude=80.1945,
                showtimes=[ShowtimeSlot(time="06:30 PM")],
            ),
            # 25 km away and not in requested cinemas -> should be filtered out
            CinemaVenue(
                name="Far Away Cinema, Chengalpattu",
                platform="TicketNew",
                booking_url="https://tn/3",
                latitude=12.6819,
                longitude=79.9888,
                showtimes=[ShowtimeSlot(time="02:00 PM")],
            ),
        ]

        matched = filter_and_rank_venues(venues, job, storage=self.storage)
        self.assertEqual(len(matched), 2)
        self.assertEqual(matched[0].name, "PVR: Grand Mall, Velachery")
        self.assertEqual(matched[0].showtimes[0].available_seats, 180)
        self.assertEqual(len(matched[0].all_platform_links()), 2)
        self.assertEqual(matched[1].name, "Rohini Silver Screens, Koyambedu")


if __name__ == "__main__":
    unittest.main()
