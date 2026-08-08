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


def main():
    addon = xbmcaddon.Addon(ADDON_ID)
    choices = ["Test głosu", "Wybierz głos ElevenLabs", "Ustawienia", "Pomoc z dźwiękiem"]
    selected = xbmcgui.Dialog().select("Kodi Lektor PL", choices)
    if selected == 0:
        test_voice(addon)
    elif selected == 1:
        choose_voice(addon)
    elif selected == 2:
        addon.openSettings()
    elif selected == 3:
        xbmcgui.Dialog().ok(
            "Kodi Lektor PL — dźwięk",
            "W Kodi otwórz Ustawienia → System → Dźwięk. Wyłącz przekazywanie dźwięku (passthrough) i ustaw „Odtwarzaj dźwięki GUI” na „Zawsze”. Lektor jest miksowany z filmem jako dźwięk WAV.",
        )


if __name__ == "__main__":
    main()
