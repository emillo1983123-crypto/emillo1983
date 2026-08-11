import io
import json
import os
import queue
import sys
import threading
import time
import unittest
import urllib.error
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from usage_worker import (  # noqa: E402
    UsageFetchError,
    UsageJob,
    UsageWorker,
    fetch_subscription,
)


class UsageWorkerTests(unittest.TestCase):
    @staticmethod
    def _stop(worker):
        worker.stop()
        worker.join(timeout=2.0)

    def test_estimate_and_subscription_fetch_happen_in_background_once(self):
        calls = []

        def fetcher(api_key, cancelled):
            calls.append((api_key, threading.current_thread().name, cancelled()))
            return {"character_count": 250, "character_limit": 1_000}

        worker = UsageWorker(fetcher=fetcher)
        worker.start()
        worker.invalidate(7)
        job = UsageJob(
            7,
            ("movie.srt", 1.0, 100),
            "sekretny-klucz",
            "eleven_flash_v2_5",
            (" Ala ", "[muzyka]", "ma kota"),
            lambda text: "" if text == "[muzyka]" else text.strip(),
        )
        try:
            self.assertTrue(worker.submit(job))
            self.assertFalse(worker.submit(job))
            result = worker.results.get(timeout=2.0)
        finally:
            self._stop(worker)

        self.assertEqual(result.generation, 7)
        self.assertEqual(result.estimate.text_characters, len("Ala") + len("ma kota"))
        self.assertEqual(result.estimate.rounded_credits, 5)
        self.assertEqual(result.subscription.remaining, 750)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "KodiLektorUsage")
        self.assertFalse(calls[0][2])
        self.assertNotIn("sekretny-klucz", repr(job))

    def test_generation_change_discards_in_flight_result(self):
        entered = threading.Event()
        release = threading.Event()

        def fetcher(_api_key, _cancelled):
            entered.set()
            release.wait(1.0)
            return {"character_count": 10, "character_limit": 100}

        worker = UsageWorker(fetcher=fetcher)
        worker.start()
        worker.invalidate(1)
        try:
            self.assertTrue(
                worker.submit(
                    UsageJob(1, ("old.srt", 1, 1), "key", "model", ("tekst",), str)
                )
            )
            self.assertTrue(entered.wait(1.0))
            worker.invalidate(2)
            release.set()
            time.sleep(0.1)
            with self.assertRaises(queue.Empty):
                worker.results.get_nowait()
        finally:
            release.set()
            self._stop(worker)

    def test_missing_user_read_has_precise_safe_message(self):
        secret = "bardzo-sekretny-klucz"
        body = json.dumps(
            {
                "detail": {
                    "status": "missing_permissions",
                    "message": "This API key is missing the permission user_read",
                }
            }
        ).encode("utf-8")
        error = urllib.error.HTTPError(
            "https://api.elevenlabs.io/v1/user/subscription",
            403,
            "Forbidden",
            {},
            io.BytesIO(body),
        )
        with mock.patch("usage_worker.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(UsageFetchError) as raised:
                fetch_subscription(secret)

        self.assertEqual(raised.exception.kind, "missing_user_read")
        self.assertIn("User: Read (user_read)", raised.exception.user_message)
        self.assertIn("Lektor nadal działa", raised.exception.user_message)
        self.assertNotIn(secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
