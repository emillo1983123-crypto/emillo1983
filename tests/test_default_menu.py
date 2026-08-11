import importlib.util
import os
import sys
import types
import unittest
import xml.etree.ElementTree as ET


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

    def getString(self, key):
        return str(self.values.get(key, "classic"))

    def getInt(self, key):
        return int(self.values.get(key, 95))


class _Addon:
    def __init__(self):
        self.settings = _Settings()

    def getSettings(self):
        return self.settings

    @staticmethod
    def getLocalizedString(label_id):
        return "label-%s" % label_id


class VoiceProfileMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_default_module()

    def test_profile_contract_matches_settings_and_speech_core(self):
        from speech import VOICE_PROFILES

        settings = ET.parse(os.path.join(ADDON, "resources", "settings.xml"))
        node = settings.find(".//setting[@id='voice_profile']")
        option_keys = {option.text for option in node.findall("./constraints/options/option")}
        menu_keys = {profile for profile, _label, _speed in self.module.VOICE_PROFILES}
        self.assertEqual(menu_keys, option_keys)
        self.assertEqual(menu_keys, set(VOICE_PROFILES))

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


if __name__ == "__main__":
    unittest.main()
