import tempfile
import unittest
from pathlib import Path

from ticketfinder.diff import compute_diff_report
from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob
from ticketfinder.storage import Storage


class TestDiffAndStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp_dir.name) / "test.db")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_snapshot_diff_new_vs_previous_and_seat_deltas(self) -> None:
        job = self.storage.add_job(
            WatchJob(
                movie="Coolie",
                target_date="2026-11-06",
                city="Chennai",
                area="Velachery",
                radius_km=12.0,
                cinemas=["Sathyam"],
            )
        )
        assert job.id is not None

        # Check #1: Cinepolis opens at 11:00 AM with 115 avail / 65 booked
        wave1 = [
            CinemaVenue(
                name="Cinepolis: BSR Mall, OMR",
                platform="District",
                booking_url="https://district/1",
                distance_km=5.1,
                first_seen_at="2:00 PM",
                showtimes=[
                    ShowtimeSlot(
                        time="11:00 AM",
                        available_seats=115,
                        booked_seats=65,
                        total_seats=180,
                    )
                ],
            )
        ]

        report1 = compute_diff_report(job, wave1, {})
        self.assertTrue(report1.has_new_openings)
        self.assertEqual(len(report1.newly_opened_cinemas), 1)
        self.assertEqual(len(report1.previously_opened_cinemas), 0)

        self.storage.save_venue_snapshots(job.id, wave1)
        job = self.storage.record_check(job.id, alerted=True)  # type: ignore[assignment]

        # Check #2: PVR Grand Mall opens (brand new) + Cinepolis adds 07:00 PM AND 11:00 AM drops to 42 avail
        prev_snapshots = self.storage.get_venue_snapshots(job.id)
        wave2 = [
            CinemaVenue(
                name="PVR: Grand Mall, Velachery",
                platform="BookMyShow",
                booking_url="https://bms/1",
                distance_km=2.1,
                showtimes=[
                    ShowtimeSlot(
                        time="10:15 AM",
                        available_seats=184,
                        booked_seats=66,
                        total_seats=250,
                    )
                ],
            ),
            CinemaVenue(
                name="Cinepolis: BSR Mall, OMR",
                platform="District",
                booking_url="https://district/1",
                distance_km=5.1,
                showtimes=[
                    ShowtimeSlot(
                        time="11:00 AM",
                        available_seats=42,
                        booked_seats=138,
                        total_seats=180,
                    ),
                    ShowtimeSlot(
                        time="07:00 PM",
                        available_seats=160,
                        booked_seats=20,
                        total_seats=180,
                    ),
                ],
            ),
        ]

        report2 = compute_diff_report(job, wave2, prev_snapshots)
        self.assertEqual(report2.update_number, 2)
        self.assertEqual(len(report2.newly_opened_cinemas), 1)
        self.assertEqual(report2.newly_opened_cinemas[0].name, "PVR: Grand Mall, Velachery")

        self.assertEqual(len(report2.new_showtimes_cinemas), 1)
        self.assertEqual(report2.new_showtimes_cinemas[0].venue.name, "Cinepolis: BSR Mall, OMR")
        self.assertEqual(report2.new_showtimes_cinemas[0].added_showtimes[0].time, "07:00 PM")

        self.assertEqual(len(report2.previously_opened_cinemas), 1)
        prev_item = report2.previously_opened_cinemas[0]
        slot_11am = prev_item.venue.showtimes[0]
        prev_slot_11am = prev_item.previous_slots_by_time["11:00 AM"]
        formatted = slot_11am.format_seat_summary(prev_slot=prev_slot_11am)
        self.assertIn("42 avail / 138 booked", formatted)
        self.assertIn("was 115 avail / 65 booked", formatted)


if __name__ == "__main__":
    unittest.main()
