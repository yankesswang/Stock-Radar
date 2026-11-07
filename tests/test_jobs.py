import asyncio
from pathlib import Path
import tempfile
import unittest

from backend.app.jobs import JobRegistry


class JobRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_job_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = JobRegistry(Path(directory))
            record = registry.submit("addition", lambda: {"value": 2 + 3})

            for _ in range(100):
                saved = registry.get(record["id"])
                if saved and saved["status"] == "succeeded":
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("job did not finish")

            self.assertEqual(saved["result"], {"value": 5})
            self.assertIsNotNone(saved["finished_at"])
            await registry.shutdown()

    async def test_invalid_job_id_is_not_found(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = JobRegistry(directory)
            self.assertIsNone(registry.get("not-a-uuid"))
