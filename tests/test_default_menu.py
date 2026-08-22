import importlib.util
import os
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "service.subtitle.tts.pl")
LIB = os.path.join(ADDON, "resources", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)


def _load_default_module():
    xbmc = types.ModuleType("xbmc")
    xbmc.playSFX = lambda *args, **kwargs: None
    xbmc.executeJSONRPC = lambda value: "{}"
    xbmc.executebuiltin = lambda value: None
    xbmc.getCondVisibility = lambda value: False

    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon.Addon = object

    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.NOTIFICATION_ERROR = "error"
    xbmcgui.NOTIFICATION_INFO = "info"
    xbmcgui.DialogProgress = object
    xbmcgui.Dialog = object

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda value: LIB if value.startswith("special://home/addons/") else value

    modules = {
        "xbmc": xbmc,
        "xbmcaddon": xbmcaddon,
        "xbmcgui": xbmcgui,
        "xbmcvfs": xbmcvfs,
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        path = os.path.join(ADDON, "default.py")
        spec = importlib.util.spec_from_file_location("kodi_lektor_default_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class _Settings:
    def __init__(self):
        self.values = {}

    def setString(self, key, value):
        self.values[key] = value

    def setInt(self, key, value):
        self.values[key] = value

    def setBool(self, key, value):
        self.values[key] = bool(value)

    def getString(self, key):
        return str(self.values.get(key, ""))

    def getInt(self, key):
        return int(self.values.get(key, 95))

    def getBool(self, key):
        return bool(self.values.get(key, False))


class _Addon:
    def __init__(self):
        self.settings = _Settings()

    def getSettings(self):
        return self.settings

    @staticmethod
    def getLocalizedString(label_id):
        if label_id == 32212:
            return "configured: %s"
        return "label-%s" % label_id


class _Progress:
    def __init__(self):
        self.created = False
        self.closed = False

    def create(self, *_args):
        self.created = True

    def close(self):
        self.closed = True


class ProviderDispatchMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_default_module()

    @staticmethod
    def _addon():
        addon = _Addon()
        addon.settings.values.update(
            {
                "api_key": "secret",
                "voice_id": "voice",
                "model_id": "eleven_flash_v2_5",
            }
        )
        addon.getAddonInfo = lambda _key: "profile"
        addon.openSettings = lambda: None
        return addon

    def test_voice_test_dispatches_to_free_device_tts(self):
        addon = self._addon()
        calls = []
        original_available = self.module.kodi_tts_available
        original_deliver = self.module.deliver_kodi_tts
        original_notify = self.module.notify
        try:
            self.module.kodi_tts_available = lambda: True
            self.module.deliver_kodi_tts = lambda text: calls.append(text)
            self.module.notify = lambda *args, **kwargs: None
            self.assertTrue(self.module.test_voice(addon))
        finally:
            self.module.kodi_tts_available = original_available
            self.module.deliver_kodi_tts = original_deliver
            self.module.notify = original_notify

        self.assertEqual(calls, ["Dzień dobry. Darmowy lektor napisów jest gotowy."])

    def test_voice_choice_explains_that_tts_service_selects_the_voice(self):
        addon = self._addon()
        notices = []
        original_notify = self.module.notify
        try:
            self.module.notify = lambda *args, **kwargs: notices.append(args)
            self.assertFalse(self.module.choose_voice(addon))
        finally:
            self.module.notify = original_notify

        self.assertEqual(len(notices), 1)
        self.assertIn("usłudze TTS", notices[0][0])
        self.assertNotIn("ElevenLabs", notices[0][0])

    def test_unknown_provider_closes_progress_and_constructs_no_client(self):
        addon = self._addon()
        addon.settings.values["speech_provider"] = "unknown-provider"
        progresses = []
        notifications = []
        original_progress = self.module.xbmcgui.DialogProgress
        original_notify = self.module.notify
        try:
            self.module.xbmcgui.DialogProgress = lambda: (
                progresses.append(_Progress()) or progresses[-1]
            )
            self.module.notify = lambda *args, **kwargs: notifications.append(args)
            with mock.patch(
                "provider_factory.ElevenLabsClient",
                side_effect=AssertionError("client must not be constructed"),
            ) as client_class:
                self.module.test_voice(addon)
                self.module.choose_voice(addon)
                client_class.assert_not_called()
        finally:
            self.module.xbmcgui.DialogProgress = original_progress
            self.module.notify = original_notify

        self.assertEqual(len(progresses), 0)
        self.assertEqual(len(notifications), 2)
        self.assertTrue(notifications[0][-1])
        self.assertFalse(notifications[1][-1])


class VoiceProfileMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_default_module()

    def test_free_provider_contract_matches_settings(self):
        settings = ET.parse(os.path.join(ADDON, "resources", "settings.xml"))
        node = settings.find(".//setting[@id='speech_provider']")
        option_keys = {option.text for option in node.findall("./constraints/options/option")}
        self.assertEqual(option_keys, {"kodi_tts"})

    def test_new_setting_readers_preserve_valid_values_and_fall_back_safely(self):
        settings = _Settings()
        settings.values.update(
            {"voice_profile": "dynamic", "speech_speed_percent": 70}
        )
        self.assertEqual(self.module._safe_voice_profile(settings), "dynamic")
        self.assertEqual(self.module._safe_speech_speed(settings), 70)

        settings.values.update(
            {"voice_profile": "unknown", "speech_speed_percent": 999}
        )
        self.assertEqual(self.module._safe_voice_profile(settings), "classic")
        self.assertEqual(self.module._safe_speech_speed(settings), 95)

        class LegacySettings:
            def getString(self, key):
                raise TypeError(key)

            def getInt(self, key):
                raise TypeError(key)

        self.assertEqual(self.module._safe_voice_profile(LegacySettings()), "classic")
        self.assertEqual(self.module._safe_speech_speed(LegacySettings()), 95)

    def test_each_one_click_profile_saves_profile_and_recommended_speed(self):
        original_dialog = self.module.xbmcgui.Dialog
        original_notify = self.module.notify
        try:
            self.module.notify = lambda *args, **kwargs: None
            for selected, (profile, _label, speed) in enumerate(self.module.VOICE_PROFILES):
                with self.subTest(profile=profile):
                    self.module.xbmcgui.Dialog = lambda: types.SimpleNamespace(
                        select=lambda _heading, _labels, value=selected: value
                    )
                    addon = _Addon()
                    self.module.choose_voice_profile(addon)
                    self.assertEqual(addon.settings.values["voice_profile"], profile)
                    self.assertEqual(addon.settings.values["speech_speed_percent"], speed)
        finally:
            self.module.xbmcgui.Dialog = original_dialog
            self.module.notify = original_notify

    def test_cancel_does_not_change_settings(self):
        original_dialog = self.module.xbmcgui.Dialog
        try:
            self.module.xbmcgui.Dialog = lambda: types.SimpleNamespace(
                select=lambda _heading, _labels: -1
            )
            addon = _Addon()
            self.module.choose_voice_profile(addon)
            self.assertEqual(addon.settings.values, {})
        finally:
            self.module.xbmcgui.Dialog = original_dialog

    def test_stale_settings_schema_reports_restart_instead_of_crashing(self):
        class LegacySettings(_Settings):
            def getString(self, key):
                raise TypeError('Invalid setting type "string" for "%s"' % key)

            def getInt(self, key):
                raise TypeError('Invalid setting type "integer" for "%s"' % key)

        addon = _Addon()
        addon.settings = LegacySettings()
        notifications = []
        original_dialog = self.module.xbmcgui.Dialog
        original_notify = self.module.notify
        try:
            self.module.xbmcgui.Dialog = lambda: types.SimpleNamespace(
                select=lambda _heading, _labels: 0
            )
            self.module.notify = lambda *args: notifications.append(args)
            self.assertFalse(self.module.choose_voice_profile(addon))
        finally:
            self.module.xbmcgui.Dialog = original_dialog
            self.module.notify = original_notify

        self.assertEqual(addon.settings.values, {})
        self.assertEqual(len(notifications), 1)
        self.assertTrue(notifications[0][-1])
        self.assertIn("uruchom", notifications[0][0].casefold())


class OpenSubtitlesMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_default_module()

    def test_missing_official_provider_opens_addon_browser_without_installing(self):
        module = self.module
        opened = []
        dialogs = []
        originals = (
            module.list_subtitle_modules,
            module.xbmc.executebuiltin,
            module.xbmcgui.Dialog,
        )
        try:
            module.list_subtitle_modules = lambda _jsonrpc: []
            module.xbmc.executebuiltin = opened.append
            module.xbmcgui.Dialog = lambda: types.SimpleNamespace(
                ok=lambda heading, message: dialogs.append((heading, message))
            )
            self.assertFalse(module.configure_auto_subtitles(_Addon()))
        finally:
            (
                module.list_subtitle_modules,
                module.xbmc.executebuiltin,
                module.xbmcgui.Dialog,
            ) = originals

        self.assertEqual(opened, ["ActivateWindow(addonbrowser)"])
        self.assertEqual(len(dialogs), 1)

    def test_installed_provider_is_enabled_and_set_for_movies_and_tv(self):
        module = self.module
        rpc_calls = []
        applied = []
        notices = []
        originals = (
            module.list_subtitle_modules,
            module.jsonrpc_call,
            module.get_setting_value,
            module.apply_setting_changes,
            module.notify,
        )
        try:
            module.list_subtitle_modules = lambda _jsonrpc: [
                {
                    "id": "service.subtitles.opensubtitles-com",
                    "name": "OpenSubtitles.com",
                    "version": "1.0.9",
                    "enabled": False,
                }
            ]
            module.jsonrpc_call = lambda jsonrpc, method, params: rpc_calls.append(
                (method, params)
            )
            module.get_setting_value = lambda jsonrpc, setting, default: ["English"]
            module.apply_setting_changes = lambda jsonrpc, changes: applied.append(changes)
            module.notify = lambda *args: notices.append(args)
            addon = _Addon()
            self.assertTrue(module.configure_auto_subtitles(addon))
        finally:
            (
                module.list_subtitle_modules,
                module.jsonrpc_call,
                module.get_setting_value,
                module.apply_setting_changes,
                module.notify,
            ) = originals

        self.assertEqual(rpc_calls[0][0], "Addons.SetAddonEnabled")
        self.assertEqual(
            rpc_calls[0][1]["addonid"],
            "service.subtitles.opensubtitles-com",
        )
        self.assertEqual(
            applied[0]["subtitles.movie"],
            "service.subtitles.opensubtitles-com",
        )
        self.assertEqual(
            applied[0]["subtitles.tv"],
            "service.subtitles.opensubtitles-com",
        )
        self.assertEqual(applied[0]["subtitles.languages"], ["Polish", "English"])
        self.assertTrue(applied[0]["subtitles.downloadfirst"])
        self.assertTrue(addon.settings.values["auto_subtitles"])
        self.assertEqual(len(notices), 1)


class AccountUsageMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_default_module()

    @staticmethod
    def _subscription():
        from usage import parse_subscription_usage

        return parse_subscription_usage(
            {
                "tier": "starter",
                "status": "active",
                "character_count": 2_000,
                "character_limit": 10_000,
            }
        )

    @staticmethod
    def _estimate():
        from usage import estimate_film_usage

        return estimate_film_usage(
            ["x" * 2_000],
            lambda value: value,
            "eleven_flash_v2_5",
        )

    def test_report_is_transparent_about_credits_and_approximation(self):
        report = self.module.format_usage_report(
            self._subscription(),
            self._estimate(),
        )
        self.assertIn("Wykorzystano: 2 000 / 10 000 znaków limitu", report)
        self.assertIn("API ElevenLabs raportuje ten limit w znakach", report)
        self.assertIn("około 1 000 kredytów", report)
        self.assertIn("po filmie pozostanie około 6 000 znaków limitu", report)
        self.assertIn("przybliżenie, nie rachunek", report)

    def test_report_distinguishes_included_limit_from_payg_extension(self):
        from usage import parse_subscription_usage

        usage = parse_subscription_usage(
            {
                "character_count": 9_500,
                "character_limit": 10_000,
                "max_character_limit_extension": 5_000,
            }
        )
        report = self.module.format_usage_report(usage, self._estimate())
        self.assertIn("przekroczy wliczony limit", report)
        self.assertIn("PAYG albo rozliczenie dodatkowe", report)
        self.assertNotIn("może zabraknąć", report)

    def test_manual_usage_action_reports_that_free_device_tts_has_no_credits(self):
        module = self.module
        notices = []
        original_notify = module.notify
        try:
            module.notify = lambda *args, **kwargs: notices.append(args)
            self.assertFalse(module.show_account_usage(_Addon()))
        finally:
            module.notify = original_notify

        self.assertEqual(len(notices), 1)
        self.assertIn("nie ma kredytów", notices[0][0])

    def test_legacy_top_up_action_does_not_open_a_paid_service(self):
        module = self.module
        opened = []
        originals = (
            module.xbmc.executebuiltin,
            module.notify,
        )
        try:
            module.xbmc.executebuiltin = opened.append
            notices = []
            module.notify = lambda *args, **kwargs: notices.append(args)
            self.assertFalse(module.open_elevenlabs_billing(_Addon()))
        finally:
            (
                module.xbmc.executebuiltin,
                module.notify,
            ) = originals

        self.assertEqual(opened, [])
        self.assertIn("nie obsługuje płatności", notices[0][0])


if __name__ == "__main__":
    unittest.main()
