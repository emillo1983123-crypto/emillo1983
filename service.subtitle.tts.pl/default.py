from __future__ import absolute_import

import os
import sys

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON_ID = "service.subtitle.tts.pl"
LIB = xbmcvfs.translatePath("special://home/addons/%s/resources/lib" % ADDON_ID)
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from auto_subtitles import (  # noqa: E402
    JsonRpcError,
    apply_setting_changes,
    build_polish_auto_download_changes,
    get_setting_value,
    jsonrpc_call,
    list_subtitle_modules,
)
from speech import ElevenLabsClient, SpeechError  # noqa: E402


def notify(message, error=False):
    icon = xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO
    xbmcgui.Dialog().notification("Kodi Lektor PL", message, icon, 6000)


def test_voice(addon):
    settings = addon.getSettings()
    key = settings.getString("api_key").strip()
    if not key:
        notify("Najpierw wpisz klucz API ElevenLabs w ustawieniach.", True)
        addon.openSettings()
        return
    profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
    cache_dir = os.path.join(profile, "cache")
    client = ElevenLabsClient(
        api_key=key,
        voice_id=settings.getString("voice_id").strip(),
        model_id=settings.getString("model_id").strip(),
        cache_dir=cache_dir,
    )
    dialog = xbmcgui.DialogProgress()
    dialog.create("Kodi Lektor PL", "Tworzę próbkę głosu…")
    try:
        result = client.synthesize("Dzień dobry. Lektor napisów działa prawidłowo.")
        dialog.close()
        xbmc.playSFX(result.path, False)
        notify("Odtwarzam próbkę. Jeśli jej nie słychać, wyłącz passthrough i ustaw dźwięki GUI na Zawsze.")
    except SpeechError as exc:
        dialog.close()
        notify(exc.user_message, True)
    except Exception:
        dialog.close()
        notify("Nie udało się utworzyć próbki. Sprawdź klucz, głos i połączenie z internetem.", True)


def choose_voice(addon):
    settings = addon.getSettings()
    key = settings.getString("api_key").strip()
    if not key:
        notify("Najpierw wpisz klucz API ElevenLabs.", True)
        addon.openSettings()
        return
    dialog = xbmcgui.DialogProgress()
    dialog.create("Kodi Lektor PL", "Pobieram listę głosów…")
    try:
        client = ElevenLabsClient(key, "", settings.getString("model_id"), "")
        voices = client.list_voices()
        dialog.close()
        if not voices:
            notify("ElevenLabs nie zwrócił dostępnych głosów.", True)
            return
        labels = ["%s%s" % (name, " — %s" % category if category else "") for name, voice_id, category in voices]
        selected = xbmcgui.Dialog().select("Wybierz głos ElevenLabs", labels)
        if selected >= 0:
            settings.setString("voice_id", voices[selected][1])
            notify("Wybrano głos: %s" % voices[selected][0])
    except SpeechError as exc:
        dialog.close()
        notify(exc.user_message, True)
    except Exception:
        dialog.close()
        notify("Nie udało się pobrać głosów. Sprawdź klucz API.", True)


def show_api_key_help(addon):
    xbmcgui.Dialog().textviewer(
        addon.getLocalizedString(32201),
        addon.getLocalizedString(32202),
        usemono=False,
    )


def configure_auto_subtitles(addon):
    """Configure Kodi's official subtitle search after explicit confirmation."""

    try:
        modules = list_subtitle_modules(xbmc.executeJSONRPC)
    except (JsonRpcError, ValueError, TypeError):
        notify(addon.getLocalizedString(32213), True)
        return
    if not modules:
        xbmcgui.Dialog().ok(
            addon.getLocalizedString(32210),
            addon.getLocalizedString(32211),
        )
        return

    labels = []
    for module in modules:
        suffix = "" if module["enabled"] else " — %s" % addon.getLocalizedString(32214)
        version = " (%s)" % module["version"] if module["version"] else ""
        labels.append("%s%s%s" % (module["name"], version, suffix))
    selected = xbmcgui.Dialog().select(addon.getLocalizedString(32210), labels)
    if selected < 0:
        return

    module = modules[selected]
    try:
        if not module["enabled"]:
            jsonrpc_call(
                xbmc.executeJSONRPC,
                "Addons.SetAddonEnabled",
                {"addonid": module["id"], "enabled": True},
            )
        languages = get_setting_value(xbmc.executeJSONRPC, "subtitles.languages", [])
        changes = build_polish_auto_download_changes(languages)
        changes["subtitles.movie"] = module["id"]
        changes["subtitles.tv"] = module["id"]
        apply_setting_changes(xbmc.executeJSONRPC, changes)
        addon.getSettings().setBool("auto_subtitles", True)
        notify(addon.getLocalizedString(32212) % module["name"])
    except (JsonRpcError, ValueError, TypeError):
        notify(addon.getLocalizedString(32213), True)


def main():
    addon = xbmcaddon.Addon(ADDON_ID)
    choices = [
        "Test głosu",
        "Wybierz głos ElevenLabs",
        addon.getLocalizedString(32210),
        addon.getLocalizedString(32200),
        "Ustawienia",
        "Pomoc z dźwiękiem",
    ]
    selected = xbmcgui.Dialog().select("Kodi Lektor PL", choices)
    if selected == 0:
        test_voice(addon)
    elif selected == 1:
        choose_voice(addon)
    elif selected == 2:
        configure_auto_subtitles(addon)
    elif selected == 3:
        show_api_key_help(addon)
    elif selected == 4:
        addon.openSettings()
    elif selected == 5:
        xbmcgui.Dialog().ok(
            "Kodi Lektor PL — dźwięk",
            "W Kodi otwórz Ustawienia → System → Dźwięk. Wyłącz przekazywanie dźwięku (passthrough) i ustaw „Odtwarzaj dźwięki GUI” na „Zawsze”. Lektor jest miksowany z filmem jako dźwięk WAV.",
        )


if __name__ == "__main__":
    main()
