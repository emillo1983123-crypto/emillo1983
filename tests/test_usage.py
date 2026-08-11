import json
import os
import sys
import unittest
from types import SimpleNamespace


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from usage import (  # noqa: E402
    UsageDataError,
    base_credits_per_character,
    estimate_film_usage,
    parse_subscription_usage,
)


class SubscriptionUsageTests(unittest.TestCase):
    def test_current_character_fields_are_parsed_as_legacy_units(self):
        usage = parse_subscription_usage(
            {
                "tier": "starter",
                "status": "active",
                "character_count": 1_250,
                "character_limit": 10_000,
                "next_character_count_reset_unix": 1_738_356_858,
                "current_overage": {"amount": "1.25", "currency": "usd"},
                "max_credit_limit_extension": 2_000,
            }
        )
        self.assertEqual(usage.used, 1_250)
        self.assertEqual(usage.limit, 10_000)
        self.assertEqual(usage.remaining, 8_750)
        self.assertEqual(usage.over_limit, 0)
        self.assertEqual(usage.tier, "starter")
        self.assertEqual(usage.status, "active")
        self.assertEqual(usage.reset_unix, 1_738_356_858)
        self.assertEqual(usage.overage_amount, "1.25")
        self.assertEqual(usage.overage_currency, "usd")
        self.assertEqual(usage.max_credit_limit_extension, 2_000)
        self.assertEqual(usage.source_units, "characters")
        self.assertAlmostEqual(usage.used_percent, 12.5)

    def test_future_credit_fields_and_nested_user_response_are_supported(self):
        usage = parse_subscription_usage(
            json.dumps(
                {
                    "subscription": {
                        "credit_count": "950",
                        "credit_limit": 1_000,
                        "credit_remaining": 50,
                        "next_credit_count_reset_unix": 2_000_000_000,
                        "max_credit_limit_extension": "unlimited",
                    }
                }
            )
        )
        self.assertEqual(usage.used, 950)
        self.assertEqual(usage.remaining, 50)
        self.assertEqual(usage.reset_unix, 2_000_000_000)
        self.assertEqual(usage.max_credit_limit_extension, "unlimited")
        self.assertEqual(usage.source_units, "credits")

    def test_over_limit_and_remaining_after_estimate_are_conservative(self):
        usage = parse_subscription_usage({"character_count": 1_025, "character_limit": 1_000})
        self.assertEqual(usage.remaining, 0)
        self.assertEqual(usage.over_limit, 25)
        self.assertEqual(usage.remaining_after(10.5), 0)

        usage = parse_subscription_usage({"character_count": 100, "character_limit": 1_000})
        self.assertEqual(usage.remaining_after(100.1), 799)

    def test_inconsistent_remaining_is_capped_by_used_and_limit(self):
        usage = parse_subscription_usage(
            {"credit_count": 900, "credit_limit": 1_000, "credit_remaining": 999}
        )
        self.assertEqual(usage.remaining, 100)

    def test_missing_or_malformed_quota_is_not_reported_as_zero(self):
        for value in (
            {},
            {"character_count": True, "character_limit": 1_000},
            {"character_count": 1, "character_limit": -1},
            "not json",
            [],
        ):
            with self.subTest(value=value):
                with self.assertRaises(UsageDataError):
                    parse_subscription_usage(value)


class FilmUsageEstimateTests(unittest.TestCase):
    def test_flash_and_turbo_are_half_credit_per_prepared_character(self):
        for model in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
            with self.subTest(model=model):
                self.assertEqual(base_credits_per_character(model), 0.5)
        self.assertEqual(base_credits_per_character("eleven_multilingual_v2"), 1.0)
        self.assertEqual(base_credits_per_character("future_model"), 1.0)

    def test_estimate_uses_filtered_text_and_skips_empty_cues(self):
        cues = [
            SimpleNamespace(text="  Ala  "),
            SimpleNamespace(text="[music]"),
            SimpleNamespace(text="ma kota"),
        ]

        def prepare(value):
            if value == "[music]":
                return ""
            return value.strip().replace("  ", " ")

        estimate = estimate_film_usage(cues, prepare, "eleven_flash_v2_5")
        self.assertEqual(estimate.cue_count, 3)
        self.assertEqual(estimate.spoken_cue_count, 2)
        self.assertEqual(estimate.text_characters, len("Ala") + len("ma kota"))
        self.assertEqual(estimate.estimated_credits, 5.0)
        self.assertEqual(estimate.rounded_credits, 5)
        self.assertTrue(estimate.approximate)
        self.assertFalse(estimate.voice_multiplier_known)

    def test_custom_voice_multiplier_is_applied_but_keeps_approximate_flag(self):
        estimate = estimate_film_usage(
            ["abcd"],
            lambda value: value,
            "eleven_multilingual_v2",
            voice_credit_multiplier=1.5,
        )
        self.assertEqual(estimate.estimated_credits, 6.0)
        self.assertEqual(estimate.rounded_credits, 6)
        self.assertTrue(estimate.voice_multiplier_known)
        self.assertTrue(estimate.approximate)

    def test_fractional_estimate_rounds_up_for_quota_comparison(self):
        estimate = estimate_film_usage(["a"], lambda value: value, "eleven_flash_v2_5")
        self.assertEqual(estimate.estimated_credits, 0.5)
        self.assertEqual(estimate.rounded_credits, 1)

    def test_invalid_prepare_callback_or_multiplier_is_rejected(self):
        with self.assertRaises(TypeError):
            estimate_film_usage([], None, "eleven_flash_v2_5")
        for value in (0, -1, float("inf"), "bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    estimate_film_usage([], lambda text: text, "model", value)


if __name__ == "__main__":
    unittest.main()
