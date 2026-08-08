import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
sys.path.insert(0, LIB)

from family_filter import soften
from speech import ElevenLabsClient
from subtitle_parser import parse_subtitle


class FamilyFilterTests(unittest.TestCase):
    def test_requested_example(self):
        self.assertEqual(
            soften("Kto, do cholery, zainstalował tę rzecz?"),
            "Kto, do licha, zainstalował tę rzecz?",
        )

    def test_polish_and_english(self):
        self.assertEqual(
            soften("Kurwa mać, ale zajebisty film."),
            "kurczę, ale świetny film.",
        )
        self.assertEqual(soften("Fuck off, asshole!"), "idź stąd, palant!")

    def test_safe_ambiguous_words(self):
        value = "Jeb Bush, Dick i Suka biegną po pieprz."
        self.assertEqual(soften(value, "family"), value)

    def test_idempotent(self):
        value = soften("Ja pierdolę, co do cholery?")
        self.assertEqual(soften(value), value)


class SubtitleParserTests(unittest.TestCase):
    def test_srt(self):
        track = parse_subtitle(
            "1\n00:00:01,000 --> 00:00:03,000\n<b>Dzień dobry!</b>\n\n"
            "2\n00:00:04,500 --> 00:00:06,000\nDruga linia.\n",
            "movie.pl.srt",
        )
        self.assertEqual(track.at(2.0), "Dzień dobry!")
        self.assertEqual(track.at(4.0), "")
        self.assertEqual(track.at(5.0), "Druga linia.")

    def test_webvtt_and_ass(self):
        vtt = parse_subtitle("WEBVTT\n\n00:01.000 --> 00:02.000\nTekst VTT\n", "x.vtt")
        self.assertEqual(vtt.at(1.5), "Tekst VTT")
        ass = parse_subtitle(
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,,{\\i1}Ala\\Nma kota{\\i0}\n",
            "x.ass",
        )
        self.assertEqual(ass.at(3.0), "Ala ma kota")

    def test_cp1250(self):
        value = "1\n00:00:01,000 --> 00:00:02,000\nZażółć gęślą.\n".encode("cp1250")
        self.assertEqual(parse_subtitle(value, "x.srt").at(1.5), "Zażółć gęślą.")


class SpeechTests(unittest.TestCase):
    def test_pcm_is_wrapped_as_wav_and_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ElevenLabsClient("secret", "voice", "model", directory)
            client._request = lambda url, data=None: b"\x00\x00" * 2400
            first = client.synthesize("Test")
            self.assertFalse(first.cached)
            self.assertAlmostEqual(first.duration, 0.1, places=2)
            with open(first.path, "rb") as handle:
                self.assertEqual(handle.read(4), b"RIFF")
            second = client.synthesize("Test")
            self.assertTrue(second.cached)
            self.assertEqual(first.path, second.path)


if __name__ == "__main__":
    unittest.main()

