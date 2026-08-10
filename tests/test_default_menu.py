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


if __name__ == "__main__":
    unittest.main()
