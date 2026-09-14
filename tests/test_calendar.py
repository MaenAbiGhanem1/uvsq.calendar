import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bridge", ROOT / "scripts" / "update_calendar.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class CalendarTests(unittest.TestCase):
    def test_build_ics(self):
        cfg = {
            "group": "S5 PHYSIQUE PSC",
            "calendar_name": "UVSQ — S5 PHYSIQUE PSC",
        }
        events = [{
            "id": "123",
            "start": "2026-09-14T10:00:00",
            "end": "2026-09-14T12:00:00",
            "eventCategory": "COURS",
            "module": "Mécanique quantique",
            "description": "COURS<br />Mécanique quantique<br />Salle 101<br />S5 PHYSIQUE PSC"
        }]
        ics, count = bridge.build_ics(events, cfg)
        self.assertEqual(count, 1)
        self.assertIn("SUMMARY:Mécanique quantique", ics)
        self.assertIn("DTSTART;TZID=Europe/Paris:20260914T100000", ics)
        self.assertIn("BEGIN:VEVENT", ics)

    def test_html_cleaning(self):
        self.assertEqual(bridge.clean_html("A<br />B &amp; C"), "A\nB & C")


if __name__ == "__main__":
    unittest.main()
