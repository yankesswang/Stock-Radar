from pathlib import Path
import tempfile
import unittest

from backend.app.services import portfolio_store


class PortfolioStoreTests(unittest.TestCase):
    def test_json_write_is_atomic_and_round_trips_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.json"
            payload = {"主投組": {"starting_capital": 1000}}

            portfolio_store._write_json(path, payload)

            self.assertEqual(portfolio_store._read_json(path), payload)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_json_is_reported_as_missing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text("{not-json", encoding="utf-8")
            self.assertIsNone(portfolio_store._read_json(path))
