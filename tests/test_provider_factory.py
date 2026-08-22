import json
import os
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from provider_factory import (  # noqa: E402
    DEFAULT_PROVIDER_ID,
    KODI_TTS_PROVIDER_ID,
    PROVIDER_SETTING_ID,
    ProviderConfigurationError,
    configured_provider_id,
    create_speech_provider,
    resolve_provider_id,
    usage_fetcher_for,
)
from kodi_tts import KodiTtsClient  # noqa: E402
from speech import ElevenLabsClient  # noqa: E402


class ProviderFactoryTests(unittest.TestCase):
    def test_missing_values_and_retained_legacy_setting_select_the_free_provider(self):
        self.assertEqual(resolve_provider_id(), DEFAULT_PROVIDER_ID)
        self.assertEqual(resolve_provider_id(""), DEFAULT_PROVIDER_ID)
        self.assertEqual(resolve_provider_id("   "), DEFAULT_PROVIDER_ID)
        self.assertEqual(resolve_provider_id(" Kodi_TTS "), DEFAULT_PROVIDER_ID)
        self.assertEqual(resolve_provider_id(" ElevenLabs "), "elevenlabs")

        class LegacySettings:
            def getString(self, setting_id):
                raise TypeError(setting_id)

        self.assertEqual(configured_provider_id(LegacySettings()), DEFAULT_PROVIDER_ID)
        settings = mock.Mock()
        settings.getString.return_value = " ElevenLabs "
        self.assertEqual(configured_provider_id(settings), DEFAULT_PROVIDER_ID)
        settings.setString.assert_called_once_with("speech_provider", DEFAULT_PROVIDER_ID)
        self.assertEqual(PROVIDER_SETTING_ID, "speech_provider")

    def test_explicit_unknown_provider_creates_no_client_and_selects_no_fetcher(self):
        network_calls = []

        def fetcher(*args, **kwargs):
            network_calls.append((args, kwargs))

        with mock.patch(
            "provider_factory.ElevenLabsClient",
            side_effect=AssertionError("client must not be constructed"),
        ) as client_class:
            with self.assertRaises(ProviderConfigurationError) as speech_error:
                create_speech_provider(
                    "unknown-provider",
                    "secret",
                    "voice",
                    "model",
                    "cache",
                )
            with self.assertRaises(ProviderConfigurationError) as usage_error:
                usage_fetcher_for("unknown-provider", fetcher)
            with self.assertRaises(ProviderConfigurationError):
                create_speech_provider(0, "secret", "voice", "model", "cache")

        client_class.assert_not_called()
        self.assertEqual(network_calls, [])
        self.assertNotIn("unknown-provider", speech_error.exception.user_message)
        self.assertEqual(
            speech_error.exception.user_message,
            usage_error.exception.user_message,
        )

    def test_default_factory_is_free_and_legacy_elevenlabs_remains_explicit(self):
        local = create_speech_provider("", "", "", "", "")
        self.assertIsInstance(local, KodiTtsClient)
        self.assertEqual(local.synthesize("Ala ma kota.").delivery, "kodi_tts")
        self.assertEqual(DEFAULT_PROVIDER_ID, KODI_TTS_PROVIDER_ID)

        with tempfile.TemporaryDirectory() as directory:
            client = create_speech_provider(
                "elevenlabs",
                "secret",
                "voice",
                "eleven_flash_v2_5",
                directory,
                speech_speed_percent=95,
                voice_profile="classic",
            )
            self.assertIsInstance(client, ElevenLabsClient)
            requests = []

            def request(url, data=None):
                requests.append((url, data))
                return b"\x00\x00" * 2400

            client._request = request
            first = client.synthesize(
                "Ala , ma kota .",
                "Poprzednia , kwestia .",
                "Następna ?",
            )
            cached = client.synthesize(
                "Ala, ma kota.",
                "Poprzednia, kwestia.",
                "Następna?",
            )

            self.assertEqual(len(requests), 1)
            self.assertIn("/text-to-speech/voice", requests[0][0])
            payload = json.loads(requests[0][1].decode("utf-8"))
            self.assertEqual(
                payload,
                {
                    "text": "Ala, ma kota.",
                    "model_id": "eleven_flash_v2_5",
                    "voice_settings": {
                        "stability": 0.70,
                        "similarity_boost": 0.75,
                        "style": 0.0,
                        "use_speaker_boost": False,
                        "speed": 0.95,
                    },
                    "apply_text_normalization": "auto",
                    "language_code": "pl",
                    "previous_text": "Poprzednia, kwestia.",
                    "next_text": "Następna?",
                },
            )
            self.assertFalse(first.cached)
            self.assertTrue(cached.cached)
            self.assertEqual(cached.path, first.path)


if __name__ == "__main__":
    unittest.main()
