import json
import os
import sys
import unittest
import urllib.parse


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from speech import ElevenLabsClient, _error_message


def _error_body(error_type, code, message):
    return json.dumps(
        {
            "detail": {
                "type": error_type,
                "code": code,
                "status": code,
                "message": message,
            }
        }
    ).encode("utf-8")


class ElevenLabsErrorMessageTests(unittest.TestCase):
    def test_missing_voice_permission_is_not_reported_as_invalid_key(self):
        message = _error_message(
            403,
            _error_body(
                "authorization_error",
                "insufficient_permissions",
                "This API key does not have permission to list voices.",
            ),
        )
        self.assertIn("Klucz API działa", message)
        self.assertIn("Voices Read", message)
        self.assertIn("User Read", message)
        self.assertIn("Text to Speech", message)
        self.assertIn("adresów IP", message)
        self.assertNotIn("nieprawidłowy", message)

    def test_legacy_401_missing_permissions_is_not_reported_as_invalid_key(self):
        message = _error_message(
            401,
            _error_body(
                "authorization_error",
                "missing_permissions",
                "The API key is missing the voices_read permission.",
            ),
        )
        self.assertIn("Klucz API działa", message)
        self.assertIn("Voices Read", message)
        self.assertNotIn("nieprawidłowy", message)

    def test_legacy_401_permission_message_beats_generic_authentication_type(self):
        message = _error_message(
            401,
            _error_body(
                "authentication_error",
                "unauthorized",
                "Missing permission: voices_read.",
            ),
        )
        self.assertIn("Klucz API działa", message)
        self.assertNotIn("nieprawidłowy", message)

    def test_invalid_or_expired_key_remains_an_authentication_error(self):
        message = _error_message(
            401,
            _error_body("authentication_error", "invalid_api_key", "Invalid API key"),
        )
        self.assertIn("nieprawidłowy albo wygasł", message)

    def test_invalid_key_length_has_priority_over_generic_authentication_text(self):
        message = _error_message(
            400,
            _error_body(
                "authentication_error",
                "invalid_api_key_length",
                "API key must be exactly 51 characters, got 53.",
            ),
        )
        self.assertIn("złą długość", message)

    def test_voice_access_and_payment_errors_have_distinct_guidance(self):
        voice = _error_message(
            403,
            _error_body("authorization_error", "voice_access_denied", "Voice denied"),
        )
        payment = _error_message(
            402,
            _error_body("payment_required", "payment_required", "Payment required"),
        )
        self.assertIn("wybranego głosu", voice)
        self.assertIn("kredytów", payment)

    def test_legacy_401_quota_and_voice_errors_are_not_called_bad_keys(self):
        quota = _error_message(
            401,
            _error_body("authentication_error", "quota_exceeded", "Quota exceeded"),
        )
        missing_voice = _error_message(
            401,
            _error_body("authentication_error", "voice_not_found", "Voice not found"),
        )
        self.assertIn("kredyt", quota.casefold())
        self.assertNotIn("nieprawidłowy", quota.casefold())
        self.assertIn("wybranego głosu", missing_voice.casefold())
        self.assertNotIn("nieprawidłowy", missing_voice.casefold())


class ElevenLabsVoiceListTests(unittest.TestCase):
    def test_current_v2_endpoint_is_paginated_deduplicated_and_sorted(self):
        client = ElevenLabsClient("secret", "", "model", "")
        urls = []

        def request(url, data=None):
            self.assertIsNone(data)
            urls.append(url)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            if "next_page_token" not in query:
                return json.dumps(
                    {
                        "voices": [
                            {"voice_id": "z", "name": "Żaneta", "category": "premade"},
                            {"voice_id": "a", "name": "Adam", "category": "cloned"},
                        ],
                        "has_more": True,
                        "next_page_token": "page-2",
                    }
                ).encode("utf-8")
            return json.dumps(
                {
                    "voices": [
                        {"voice_id": "z", "name": "Żaneta nowa", "category": "premade"},
                        {"voice_id": "b", "name": "Basia", "category": "professional"},
                    ],
                    "has_more": False,
                    "next_page_token": None,
                }
            ).encode("utf-8")

        client._request = request
        self.assertEqual(
            client.list_voices(),
            [
                ("Adam", "a", "cloned"),
                ("Basia", "b", "professional"),
                ("Żaneta nowa", "z", "premade"),
            ],
        )
        self.assertEqual(len(urls), 2)
        self.assertTrue(all(url.startswith("https://api.elevenlabs.io/v2/voices?") for url in urls))
        self.assertIn("page_size=100", urls[0])

    def test_malformed_pagination_is_rejected(self):
        client = ElevenLabsClient("secret", "", "model", "")
        client._request = lambda _url, _data=None: json.dumps(
            {"voices": [], "has_more": True, "next_page_token": ""}
        ).encode("utf-8")
        with self.assertRaisesRegex(Exception, "nieprawidłową listę"):
            client.list_voices()


if __name__ == "__main__":
    unittest.main()
