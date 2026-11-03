import unittest
from datetime import date

from ticketfinder.config import Settings
from ticketfinder.nlp import parse_natural_language_query


class TestNaturalLanguageParser(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(default_city="Chennai")
        self.ref_date = date(2026, 11, 2)  # Monday, Nov 2, 2026

    def test_full_natural_language_query(self) -> None:
        q = "Watch for Coolie tickets on Friday Nov 6 in Chennai near Velachery within 12km and also check Sathyam and Palazzo"
        parsed = parse_natural_language_query(q, settings=self.settings, ref_date=self.ref_date)
        self.assertEqual(parsed.intent, "WATCH")
        self.assertEqual(parsed.movie, "Coolie")
        self.assertEqual(parsed.target_date, "2026-11-06")
        self.assertEqual(parsed.city, "Chennai")
        self.assertEqual(parsed.area, "Velachery")
        self.assertEqual(parsed.radius_km, 12.0)
        self.assertEqual(parsed.cinemas, ["Sathyam", "Palazzo"])

    def test_locality_infers_city_and_range_radius(self) -> None:
        q = "Alert me when Interstellar opens tomorrow near Koramangala within 10-15 kms"
        parsed = parse_natural_language_query(q, settings=self.settings, ref_date=self.ref_date)
        self.assertEqual(parsed.movie, "Interstellar")
        self.assertEqual(parsed.target_date, "2026-11-03")
        self.assertEqual(parsed.area, "Koramangala")
        self.assertEqual(parsed.city, "Bengaluru")
        self.assertEqual(parsed.radius_km, 15.0)

    def test_stop_intent(self) -> None:
        parsed = parse_natural_language_query("stop watching Coolie", settings=self.settings)
        self.assertEqual(parsed.intent, "STOP")
        self.assertEqual(parsed.movie.lower(), "coolie")

        parsed_all = parse_natural_language_query("stop all", settings=self.settings)
        self.assertEqual(parsed_all.intent, "STOP")
        self.assertEqual(parsed_all.movie, "")


if __name__ == "__main__":
    unittest.main()
