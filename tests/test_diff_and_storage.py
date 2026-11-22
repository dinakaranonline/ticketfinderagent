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


def test_sold_out_new_showtimes_are_not_new_openings():
    from ticketfinder.diff import compute_diff_report
    from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob

    job = WatchJob(movie="M", target_date="2026-12-19", city="Chennai", id=1)
    prev = CinemaVenue(
        name="INOX X", platform="BMS", booking_url="u",
        showtimes=[ShowtimeSlot(time="1:30 PM", date="2026-12-19", available_seats=0, total_seats=100)],
    )
    cur_sold = CinemaVenue(
        name="INOX X", platform="BMS", booking_url="u",
        showtimes=[
            ShowtimeSlot(time="1:30 PM", date="2026-12-19", available_seats=0, total_seats=100),
            ShowtimeSlot(time="10:05 AM", date="2026-12-19", available_seats=0, total_seats=100),
        ],
    )
    new_sold_cinema = CinemaVenue(
        name="PVR Y", platform="BMS", booking_url="u",
        showtimes=[ShowtimeSlot(time="9:25 AM", date="2026-12-19", available_seats=0, total_seats=50)],
    )
    rep = compute_diff_report(job, [cur_sold, new_sold_cinema], {prev.normalized_key: prev})
    assert rep.new_showtimes_cinemas and rep.newly_opened_cinemas
    assert not rep.has_new_openings
    assert rep.bookable_show_count == 0

    cur_open = CinemaVenue(
        name="INOX X", platform="BMS", booking_url="u",
        showtimes=[ShowtimeSlot(time="10:05 AM", date="2026-12-19", available_seats=8, total_seats=100)],
    )
    rep2 = compute_diff_report(job, [cur_open], {prev.normalized_key: prev})
    assert rep2.has_new_openings
    assert rep2.bookable_show_count == 1


def test_heartbeat_email_subject_is_not_a_ticket_update():
    from ticketfinder.diff import compute_diff_report
    from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob
    from ticketfinder.notifiers.templates import render_multi_email_content

    reports = []
    for d in ("2026-12-19", "2026-12-20"):
        job = WatchJob(movie="M", target_date=d, city="Chennai", id=1)
        v = CinemaVenue(
            name="INOX X", platform="BMS", booking_url="u",
            showtimes=[ShowtimeSlot(time="1:30 PM", date=d, available_seats=0, total_seats=100)],
        )
        reports.append(compute_diff_report(job, [v], {v.normalized_key: v}))
    subject, _, _ = render_multi_email_content(reports, is_heartbeat=True)
    assert "Heartbeat" in subject and "Update #" not in subject
    assert "all shows sold out" in subject


def test_known_show_that_becomes_bookable_is_a_new_opening():
    from ticketfinder.diff import compute_diff_report
    from ticketfinder.models import CinemaVenue, ShowtimeSlot, WatchJob

    job = WatchJob(movie="M", target_date="2026-12-18", city="Chennai", id=1)

    def venue(**slot_kwargs):
        return CinemaVenue(
            name="INOX The Marina Mall", platform="PVR INOX", booking_url="u",
            showtimes=[ShowtimeSlot(time="1:30 PM", date="2026-12-18", **slot_kwargs)],
        )

    greyed = venue(bookable=False)  # PVR "Lapsed": listed but not on sale
    opened = venue(available_seats=120, total_seats=300, bookable=True)

    rep = compute_diff_report(job, [opened], {greyed.normalized_key: greyed})
    assert rep.has_new_openings
    assert [s.time for s in rep.new_showtimes_cinemas[0].added_showtimes] == ["1:30 PM"]

    # Sold out last time, seats released now: also worth an alert.
    sold = venue(available_seats=0, total_seats=300)
    assert compute_diff_report(job, [opened], {sold.normalized_key: sold}).has_new_openings

    # Already bookable last time: just a seat-count update, not a new opening.
    rep3 = compute_diff_report(job, [opened], {opened.normalized_key: venue(available_seats=150, total_seats=300)})
    assert not rep3.new_showtimes_cinemas
    assert not rep3.has_new_openings

    # Still greyed out: nothing new.
    assert not compute_diff_report(job, [greyed], {greyed.normalized_key: greyed}).new_showtimes_cinemas


def test_seat_delta_across_different_hall_sizes_shows_only_seats_left():
    from ticketfinder.models import ShowtimeSlot

    prev = ShowtimeSlot(time="8:10 PM", available_seats=6, total_seats=197)   # District
    cur = ShowtimeSlot(time="8:10 PM", available_seats=6, total_seats=221)    # PVR seat map
    assert "was" not in cur.format_seat_summary(prev_slot=prev)  # no fake "24 more booked"
    cur2 = ShowtimeSlot(time="8:10 PM", available_seats=5, total_seats=221)
    assert "(was 6 avail)" in cur2.format_seat_summary(prev_slot=prev)
    same = ShowtimeSlot(time="8:10 PM", available_seats=4, total_seats=197)
    assert "(was 6 avail / 191 booked)" in same.format_seat_summary(prev_slot=prev)
