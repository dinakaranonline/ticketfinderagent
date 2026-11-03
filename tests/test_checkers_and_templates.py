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


if __name__ == "__main__":
    unittest.main()
