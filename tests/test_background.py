import asyncio
from pathlib import Path
import tempfile
import unittest

from backend.app.background import (
    start_background_jobs,
    stop_background_jobs,
    try_acquire_leader_lock,
)


class BackgroundJobLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_and_awaits_running_jobs(self):
        started = asyncio.Event()
        cleaned_up = asyncio.Event()

        async def job():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up.set()

        tasks = start_background_jobs([("test-job", job)])
        await asyncio.wait_for(started.wait(), timeout=1)

        await stop_background_jobs(tasks)

        self.assertTrue(tasks[0].cancelled())
        self.assertTrue(cleaned_up.is_set())
        self.assertEqual(tasks[0].get_name(), "test-job")

    async def test_stop_accepts_an_empty_job_list(self):
        await stop_background_jobs([])


class BackgroundJobLeaderTests(unittest.TestCase):
    def test_only_one_process_handle_can_be_leader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leader.lock"
            leader = try_acquire_leader_lock(path)
            self.assertIsNotNone(leader)
            self.addCleanup(leader.release)

            follower = try_acquire_leader_lock(path)
            self.assertIsNone(follower)

            leader.release()
            replacement = try_acquire_leader_lock(path)
            self.assertIsNotNone(replacement)
            replacement.release()
