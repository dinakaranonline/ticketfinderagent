import json
import unittest

from ticketfinder.checkers.bookmyshow import parse_bms_html
from ticketfinder.checkers.district import parse_district_html
from ticketfinder.diff import compute_diff_report
from ticketfinder.models import WatchJob
from ticketfinder.notifiers.templates import (
    render_email_content,
    render_multi_email_content,
    render_multi_whatsapp_message,
    render_whatsapp_message,
)


class TestCheckersAndTemplates(unittest.TestCase):
    def test_parse_bms_initial_state_with_seats(self) -> None:
        mock_state = {
            "showtimesByEvent": {
                "currentDateCode": "20261106",
                "venues": [
                    {
                        "VenueName": "PVR: Grand Mall, Velachery",
                        "VenueLatitude": "12.9719",
                        "VenueLongitude": "80.2185",
                        "showtimes": [
                            {
                                "ShowTime": "10:15 AM",
                                "ShowDateCode": "20261106",
                                "Dimension": "Dolby Atmos",
                                "AvailabilityStatus": "1",
                                "Categories": [
                                    {
                                        "PriceDesc": "Prime",
                                        "CurPrice": "190",
                                        "CurStatus": "1",
                                        "SeatsAvail": 150,
                                        "MaxSeats": 200,
                                    },
                                    {
                                        "PriceDesc": "Classic",
                                        "CurPrice": "150",
                                        "CurStatus": "2",
                                        "SeatsAvail": 34,
                                        "MaxSeats": 50,
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        }
        html = f"<html><script>window.__INITIAL_STATE__ = {json.dumps(mock_state)};</script></html>"
        venues = parse_bms_html(html, "2026-11-06", "https://in.bookmyshow.com/buytickets/test")
        self.assertEqual(len(venues), 1)
        v = venues[0]
        self.assertEqual(v.name, "PVR: Grand Mall, Velachery")
        self.assertEqual(v.showtimes[0].available_seats, 184)
        self.assertEqual(v.showtimes[0].booked_seats, 66)
        self.assertEqual(v.showtimes[0].total_seats, 250)

        # Wrong date should return empty (prevents auto-redirect false positives)
        wrong_date_venues = parse_bms_html(html, "2026-11-07", "https://in.bookmyshow.com/buytickets/test")
        self.assertEqual(len(wrong_date_venues), 0)

    def test_parse_district_next_data_and_templates(self) -> None:
        next_payload = {
            "props": {
                "pageProps": {
                    "initialState": {
                        "selectedDate": "2026-11-06",
                        "cinemas": [
                            {
                                "name": "SPI Sathyam, Royapettah",
                                "latitude": 13.0556,
                                "longitude": 80.2580,
                                "sessions": [
                                    {
                                        "showDate": "2026-11-06",
                                        "showTime": "06:30 PM",
                                        "soundFormat": "Dolby Atmos",
                                        "status": "FAST_FILLING",
                                        "availableSeats": 52,
                                        "totalSeats": 250,
                                    }
                                ],
                            }
                        ],
                    }
                }
            }
        }
        html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_payload)}</script></html>'
        venues = parse_district_html(html, "2026-11-06", "https://www.district.in/movies/coolie")
        self.assertEqual(len(venues), 1)
        self.assertEqual(venues[0].showtimes[0].available_seats, 52)
        self.assertEqual(venues[0].showtimes[0].booked_seats, 198)

        job = WatchJob(
            id=1,
            movie="Coolie",
            target_date="2026-11-06",
            city="Chennai",
            area="Velachery",
            radius_km=12.0,
            cinemas=["Sathyam"],
        )
        report = compute_diff_report(job, venues, {})
        wa_msg = render_whatsapp_message(report)
        self.assertIn("NEWLY OPENED", wa_msg)
        self.assertIn("52 avail / 198 booked", wa_msg)

        subject, text_body, html_body = render_email_content(report)
        self.assertIn("Coolie", subject)
        self.assertIn("52 avail / 198 booked", text_body)
        self.assertIn("<table", html_body)

    def test_multi_date_and_heartbeat_templates(self) -> None:
        job1 = WatchJob(
            id=16,
            movie="Ramayana Tamil Version",
            target_date="2026-11-07",
            city="Chennai",
            area="Perumbakkam",
            radius_km=15.0,
            cinemas=["INOX", "PVR", "Cinepolis", "Miraj"],
            check_count=4,
        )
        job2 = WatchJob(
            id=17,
            movie="Ramayana Tamil Version",
            target_date="2026-11-09",
            city="Chennai",
            area="Perumbakkam",
            radius_km=15.0,
            cinemas=["INOX", "PVR", "Cinepolis", "Miraj"],
            check_count=4,
        )
        rep1 = compute_diff_report(job1, [], {})
        rep2 = compute_diff_report(job2, [], {})

        wa_hb = render_multi_whatsapp_message(
            [rep1, rep2], is_daemon_running=True, is_heartbeat=True
        )
        self.assertIn("4-HOUR HEARTBEAT", wa_hb)
        self.assertIn("Sat, 07 Nov 2026 & Mon, 09 Nov 2026", wa_hb)

        subj, txt, htm = render_multi_email_content(
            [rep1, rep2], is_daemon_running=True, is_heartbeat=True
        )
        self.assertIn("[4-Hour Heartbeat]", subj)
        self.assertIn("Sat, 07 Nov 2026 & Mon, 09 Nov 2026", subj)
        self.assertIn("2026-11-07", txt)
        self.assertIn("2026-11-09", txt)
        self.assertIn("4-Hour Heartbeat", htm)

    def test_any_available_date_alert_displays_actual_date(self) -> None:
        """Verifies that an ANY date query displays the actual discovered calendar date in alerts."""
        from ticketfinder.models import CinemaVenue, ShowtimeSlot

        job = WatchJob(
            id=52,
            movie="Avengers Doomsday",
            target_date="ANY",
            city="Chennai",
            area="Perumbakkam",
            radius_km=15.0,
            cinemas=["INOX", "PVR", "Cinepolis", "Miraj"],
        )

        slots = [
            ShowtimeSlot(
                time="10:00 AM",
                date="2026-11-21",
                screen_format="IMAX 3D",
                status="AVAILABLE",
                available_seats=150,
                booked_seats=50,
                total_seats=200,
            ),
            ShowtimeSlot(
                time="02:30 PM",
                date="2026-11-21",
                screen_format="IMAX 3D",
                status="FAST_FILLING",
                available_seats=40,
                booked_seats=160,
                total_seats=200,
            ),
        ]
        venue = CinemaVenue(
            name="INOX The Marina Mall, OMR",
            platform="District (Paytm)",
            booking_url="https://www.district.in/movies/avengers",
            showtimes=slots,
            distance_km=6.2,
            show_date="2026-11-21",
        )

        report = compute_diff_report(job, [venue], {})
        self.assertEqual(report.discovered_dates, ["2026-11-21"])

        # 1. Telegram / WhatsApp Alert
        wa_text = render_whatsapp_message(report)
        self.assertIn("Sat, 21 Nov 2026", wa_text)
        self.assertIn("Date: *Sat, 21 Nov 2026*", wa_text)
        self.assertNotIn("Avengers Doomsday — Any Available Date*", wa_text)

        # 2. Email Alert
        subj, txt, htm = render_email_content(report)
        self.assertIn("Sat, 21 Nov 2026", subj)
        self.assertIn("Sat, 21 Nov 2026", txt)
        self.assertIn("Date", txt)  # Date column in ASCII table
        self.assertIn("Sat, 21 Nov 2026", htm)
        self.assertIn("IMAX 3D", txt)

    def test_bms_and_district_any_date_parsing(self) -> None:
        """Verifies BookMyShow and District checkers extract dates when target_date is ANY."""
        # 1. BookMyShow
        bms_mock = {
            "showtimesByEvent": {
                "currentDateCode": "20261121",
                "venues": [
                    {
                        "VenueName": "PVR: Grand Mall, Velachery",
                        "showtimes": [
                            {
                                "ShowTime": "07:00 PM",
                                "ShowDateCode": "20261121",
                                "Dimension": "2D",
                                "AvailabilityStatus": "1",
                            }
                        ],
                    }
                ],
            }
        }
        bms_html = f"<html><script>window.__INITIAL_STATE__ = {json.dumps(bms_mock)};</script></html>"
        bms_venues = parse_bms_html(bms_html, "ANY", "https://in.bookmyshow.com/buytickets/test")
        self.assertEqual(len(bms_venues), 1)
        self.assertEqual(bms_venues[0].showtimes[0].date, "2026-11-21")
        self.assertEqual(bms_venues[0].dates, ["2026-11-21"])

        # 2. District SSR movieSessions
        dist_mock = {
            "props": {
                "pageProps": {
                    "data": {
                        "serverState": {
                            "movieSessions": {
                                "bundle_1": {
                                    "searchDate": "2026-11-22",
                                    "pageData": {
                                        "nearbyCinemas": [
                                            {
                                                "cinemaInfo": {
                                                    "name": "Cinepolis: BBD Mall",
                                                    "latitude": "12.9",
                                                    "longitude": "80.2",
                                                },
                                                "sessions": [
                                                    {
                                                        "showTime": "2026-11-22T04:30:00Z",
                                                        "scrnFmt": "2D",
                                                        "avail": "100",
                                                        "total": "200",
                                                    }
                                                ],
                                            }
                                        ]
                                    },
                                }
                            }
                        }
                    }
                }
            }
        }
        dist_html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(dist_mock)}</script></html>'
        dist_venues = parse_district_html(dist_html, "ANY", "https://www.district.in/movies/test")
        self.assertEqual(len(dist_venues), 1)
        self.assertEqual(dist_venues[0].showtimes[0].date, "2026-11-22")
        self.assertEqual(dist_venues[0].dates, ["2026-11-22"])


if __name__ == "__main__":
    unittest.main()
