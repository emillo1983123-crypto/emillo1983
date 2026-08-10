import json
import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
sys.path.insert(0, LIB)

from family_filter import soften
from cue_tracker import ActiveCueTracker
from speech import ElevenLabsClient, SpeechError
from subtitle_parser import clean_text, parse_subtitle
from text_normalizer import compress_for_economy, normalize_for_speech


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
    def test_nonverbal_punctuation_only_cues_are_ignored(self):
        for value in ("...", ",", "!!!", "— —", "<i>...</i>", "😀"):
            with self.subTest(value=value):
                self.assertEqual(clean_text(value), "")

    def test_dialogue_prefix_is_removed_without_losing_negative_numbers(self):
        self.assertEqual(clean_text("- Halo."), "Halo.")
        self.assertEqual(clean_text("— Cześć."), "Cześć.")
        self.assertEqual(clean_text("-5 stopni."), "-5 stopni.")

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

    def test_active_cues_keep_stable_indexes_and_neighbor_context(self):
        track = parse_subtitle(
            "1\n00:00:01,000 --> 00:00:03,000\nPierwsza.\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\nDruga.\n\n"
            "3\n00:00:05,000 --> 00:00:06,000\nTrzecia.\n",
            "overlap.srt",
        )
        active = track.active(2.5)
        self.assertEqual([item.index for item in active], [0, 1])
        self.assertEqual(active[0].next_text, "Druga.")
        self.assertEqual(active[1].previous_text, "Pierwsza.")
        self.assertEqual(active[1].next_text, "Trzecia.")
        self.assertEqual(track.at(2.5), "Pierwsza. Druga.")


class CueTrackerTests(unittest.TestCase):
    def test_overlap_a_then_a_plus_b_then_b_submits_each_cue_once(self):
        track = parse_subtitle(
            "1\n00:00:01,000 --> 00:00:03,000\nA\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\nB\n",
            "overlap.srt",
        )
        tracker = ActiveCueTracker()
        self.assertTrue(tracker.use_source(("overlap.srt", 1.0, 100)))
        first = tracker.observe(track.active(1.5))
        second = tracker.observe(track.active(2.5))
        third = tracker.observe(track.active(3.5))
        self.assertEqual(first.text, "A")
        self.assertEqual(second.text, "B")
        self.assertIsNone(third)

    def test_simultaneous_cues_are_one_batch_and_source_change_resets(self):
        track = parse_subtitle(
            "1\n00:00:01,000 --> 00:00:03,000\nPierwsza\n\n"
            "2\n00:00:01,000 --> 00:00:03,000\nDruga\n",
            "dialog.srt",
        )
        tracker = ActiveCueTracker()
        tracker.use_source(("dialog.srt", 1.0, 100))
        batch = tracker.observe(track.active(1.5))
        self.assertEqual(batch.text, "Pierwsza … Druga")
        self.assertEqual(len(batch.cue_ids), 2)
        self.assertIsNone(tracker.observe(track.active(1.5)))
        self.assertTrue(tracker.use_source(("dialog.srt", 2.0, 101)))
        self.assertIsNotNone(tracker.observe(track.active(1.5)))


class TextNormalizerTests(unittest.TestCase):
    def test_natural_punctuation_and_spacing(self):
        self.assertEqual(normalize_for_speech("Ala , ma kota ."), "Ala, ma kota.")
        self.assertEqual(normalize_for_speech("Czekaj . . . Co ?!"), "Czekaj… Co?")

    def test_inline_sdh_decorations_and_zero_width(self):
        value = "»\u200b [muzyka] Halo\u200e, (śmiech) świecie! ♫ «"
        self.assertEqual(normalize_for_speech(value), "Halo, świecie!")
        self.assertEqual(normalize_for_speech("[door slams] Come in."), "Come in.")
        self.assertEqual(normalize_for_speech("[Anna] Cześć. (Naprawdę?) Tak."), "Anna Cześć. Naprawdę? Tak.")

    def test_meaningful_number_and_word_punctuation_is_preserved(self):
        value = "3,14 i 10.5 o 12:30; O'Connor wysłał e-mail."
        self.assertEqual(normalize_for_speech(value), value)

    def test_punctuation_only_is_empty_and_normalization_is_idempotent(self):
        for value in ("...", ",", "!!!", "— —", "♪ ♫", "___"):
            with self.subTest(value=value):
                self.assertEqual(normalize_for_speech(value), "")
        value = "[śmiech] Ala , ma kota ."
        normalized = normalize_for_speech(value)
        self.assertEqual(normalize_for_speech(normalized), normalized)

    def test_family_filter_on_and_off_paths_are_normalized(self):
        source = "Kto , do cholery , zainstalował tę rzecz ?"
        self.assertEqual(
            normalize_for_speech(soften(source, "family")),
            "Kto, do licha, zainstalował tę rzecz?",
        )
        self.assertEqual(
            normalize_for_speech(source),
            "Kto, do cholery, zainstalował tę rzecz?",
        )

    def test_economy_compression_is_shorter_sensible_and_idempotent(self):
        source = (
            "JAN: Yyy, n-nie, nie wiem wiem, ale mamy 12 biletów. "
            "bardzo dobrze bardzo dobrze."
        )
        compressed = compress_for_economy(source)
        self.assertEqual(compressed, "nie, nie wiem, ale mamy 12 biletów. bardzo dobrze.")
        self.assertLess(len(compressed), len(normalize_for_speech(source)))
        self.assertEqual(compress_for_economy(compressed), compressed)

    def test_economy_preserves_negations_numbers_names_and_semantic_labels(self):
        values = (
            "Anna nie ma 2 biletów dla Jana.",
            "UWAGA: NIE dotykaj 220 V.",
            "Jan Nowak Jan Nowak nie zapłacił 100 zł.",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(compress_for_economy(value), value)

    def test_economy_removes_fillers_stutters_and_direct_short_repetitions(self):
        self.assertEqual(compress_for_economy("Eee, um, hmm, to to był był plan."), "to był plan.")
        self.assertEqual(
            compress_for_economy("idziemy do domu, idziemy do domu!"),
            "idziemy do domu!",
        )


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

    def test_payload_and_cache_use_normalized_text(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ElevenLabsClient("secret", "voice", "model", directory)
            requests = []

            def request(url, data=None):
                requests.append((url, data))
                return b"\x00\x00" * 2400

            client._request = request
            first = client.synthesize("Ala , ma kota .")
            payload = json.loads(requests[0][1].decode("utf-8"))
            self.assertEqual(payload["text"], "Ala, ma kota.")
            second = client.synthesize("Ala, ma kota.")
            self.assertTrue(second.cached)
            self.assertEqual(first.path, second.path)
            self.assertEqual(len(requests), 1)

    def test_flash_payload_contains_polish_context_and_conservative_voice_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ElevenLabsClient("secret", "voice", "eleven_flash_v2_5", directory)
            requests = []

            def request(url, data=None):
                requests.append((url, data))
                return b"\x00\x00" * 2400

            client._request = request
            client.synthesize("Bieżąca kwestia.", "Poprzednia , kwestia .", "Następna ?")
            payload = json.loads(requests[0][1].decode("utf-8"))
            self.assertEqual(payload["language_code"], "pl")
            self.assertEqual(payload["previous_text"], "Poprzednia, kwestia.")
            self.assertEqual(payload["next_text"], "Następna?")
            self.assertEqual(payload["apply_text_normalization"], "auto")
            self.assertEqual(
                payload["voice_settings"],
                {
                    "stability": 0.55,
                    "similarity_boost": 0.75,
                    "style": 0.0,
                    "use_speaker_boost": False,
                    "speed": 1.05,
                },
            )

    def test_economy_off_keeps_context_and_context_specific_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ElevenLabsClient("secret", "voice", "eleven_multilingual_v2", directory)
            requests = []

            def request(url, data=None):
                requests.append((url, data))
                return b"\x00\x00" * 2400

            client._request = request
            first = client.synthesize("Test", "Pierwszy", "Następny", economy_mode=False)
            second = client.synthesize("Test", "Inny", "Następny", economy_mode=False)
            cached = client.synthesize("Test", "Pierwszy", "Następny", economy_mode=False)
            first_payload = json.loads(requests[0][1].decode("utf-8"))
            self.assertNotIn("language_code", first_payload)
            self.assertNotEqual(first.path, second.path)
            self.assertTrue(cached.cached)
            self.assertEqual(cached.path, first.path)
            self.assertEqual(len(requests), 2)

    def test_economy_zeroes_context_and_reuses_wav_for_identical_compressed_text(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ElevenLabsClient("secret", "voice", "eleven_flash_v2_5", directory)
            requests = []

            def request(url, data=None):
                requests.append((url, data))
                return b"\x00\x00" * 2400

            client._request = request
            first = client.synthesize(
                "JAN: Yyy, Test test.",
                "Pierwszy kontekst",
                "Następny kontekst",
                economy_mode=True,
            )
            second = client.synthesize(
                "Test.",
                "Zupełnie inny kontekst",
                "Jeszcze inny kontekst",
                economy_mode=True,
            )
            payload = json.loads(requests[0][1].decode("utf-8"))
            self.assertEqual(payload["text"], "Test.")
            self.assertNotIn("previous_text", payload)
            self.assertNotIn("next_text", payload)
            self.assertTrue(second.cached)
            self.assertEqual(first.path, second.path)
            self.assertEqual(len(requests), 1)

    def test_punctuation_only_does_not_call_api(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ElevenLabsClient("secret", "voice", "model", directory)
            requests = []
            client._request = lambda url, data=None: requests.append((url, data))
            with self.assertRaises(SpeechError):
                client.synthesize("... !!!")
            self.assertEqual(requests, [])


if __name__ == "__main__":
    unittest.main()
