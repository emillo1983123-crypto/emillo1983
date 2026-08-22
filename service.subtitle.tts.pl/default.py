from __future__ import absolute_import

import os
import sys
from datetime import datetime

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON_ID = "service.subtitle.tts.pl"
LIB = xbmcvfs.translatePath("special://home/addons/%s/resources/lib" % ADDON_ID)
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from auto_subtitles import (  # noqa: E402
    ACTIVATE_ADDON_BROWSER,
    JsonRpcError,
    OPENSUBTITLES_COM_ADDON_ID,
    apply_setting_changes,
    build_subtitle_provider_changes,
    find_subtitle_module,
    get_setting_value,
    jsonrpc_call,
    list_subtitle_modules,
)
from family_filter import soften  # noqa: E402
from kodi_tts import deliver as deliver_kodi_tts  # noqa: E402
from kodi_tts import is_available as kodi_tts_available  # noqa: E402
from provider_factory import (  # noqa: E402
    ProviderConfigurationError,
    configured_provider_id,
    create_speech_provider,
    usage_fetcher_for,
)
from speech import SpeechError  # noqa: E402
from subtitle_source import SubtitleSource  # noqa: E402
from text_normalizer import compress_for_economy, normalize_for_speech  # noqa: E402
from usage import estimate_film_usage  # noqa: E402
from usage_worker import UsageFetchError, fetch_subscription  # noqa: E402


VOICE_PROFILES = (
    ("classic", 32028, 95),
    ("warm", 32029, 95),
    ("natural", 32033, 100),
    ("dynamic", 32034, 105),
)
VOICE_PROFILE_KEYS = {profile for profile, _label, _speed in VOICE_PROFILES}
ELEVENLABS_DEVELOPERS_URL = "https://elevenlabs.io/app/developers"


def _safe_speech_speed(settings):
    try:
        value = settings.getInt("speech_speed_percent")
    except Exception:
        return 95
    if isinstance(value, bool) or not isinstance(value, int) or not 70 <= value <= 120:
        return 95
    return value


def _safe_voice_profile(settings):
    try:
        value = settings.getString("voice_profile")
    except Exception:
        return "classic"
    if not isinstance(value, str):
        return "classic"
    value = value.strip().casefold()
    return value if value in VOICE_PROFILE_KEYS else "classic"


def notify(message, error=False):
    icon = xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO
    xbmcgui.Dialog().notification("Kodi Lektor PL", message, icon, 6000)


def test_voice(addon):
    del addon
    if not kodi_tts_available():
        notify(
            "Nie znaleziono kompatybilnej usługi TTS. Otwórz pomoc dodatku i zainstaluj darmowy silnik mowy dla Kodi.",
            True,
        )
        return False
    try:
        deliver_kodi_tts("Dzień dobry. Darmowy lektor napisów jest gotowy.")
        notify("Wysłano próbkę do głosu urządzenia.")
        return True
    except SpeechError as exc:
        notify(exc.user_message, True)
        return False
    except Exception as exc:
        notify("Nie udało się uruchomić darmowego głosu: %s" % exc, True)
        return False


def choose_voice(addon):
    del addon
    notify(
        "Polski głos wybiera się w usłudze TTS urządzenia. Ten dodatek nie używa listy głosów ani klucza API.",
        False,
    )
    return False


def choose_voice_profile(addon):
    """Apply a safe narration archetype and its recommended starting speed."""

    settings = addon.getSettings()
    labels = [addon.getLocalizedString(label_id) for _key, label_id, _speed in VOICE_PROFILES]
    selected = xbmcgui.Dialog().select(addon.getLocalizedString(32027), labels)
    if selected < 0:
        return
    profile, _label_id, speed = VOICE_PROFILES[selected]
    try:
        # Probe both typed settings first. During a hot update Kodi can briefly
        # retain the previous version's setting definitions in memory.
        settings.getString("voice_profile")
        settings.getInt("speech_speed_percent")
        settings.setInt("speech_speed_percent", speed)
        settings.setString("voice_profile", profile)
    except Exception:
        notify(
            "Kodi nie odświeżył jeszcze ustawień dodatku. Zamknij całkowicie Kodi, "
            "uruchom je ponownie i wybierz profil jeszcze raz.",
            True,
        )
        return False
    notify("%s: %s (%s%%)" % (addon.getLocalizedString(32027), labels[selected], speed))
    return True


def show_api_key_help(addon):
    xbmcgui.Dialog().textviewer(
        addon.getLocalizedString(32201),
        addon.getLocalizedString(32202),
        usemono=False,
    )


def _format_count(value):
    try:
        return format(int(value), ",").replace(",", " ")
    except (TypeError, ValueError, OverflowError):
        return "?"


def _prepare_estimate_text(settings, text):
    value = text or ""
    try:
        if settings.getBool("family_mode"):
            value = soften(value, settings.getString("filter_level") or "family")
    except Exception:
        value = soften(value, "family")
    try:
        economy = settings.getBool("economy_mode")
    except Exception:
        economy = True
    return compress_for_economy(value) if economy else normalize_for_speech(value)


def estimate_current_film(addon):
    """Return a best-effort full-track estimate for the currently playing video."""

    try:
        player = xbmc.Player()
        if not player.isPlayingVideo():
            return None
        playing_file = player.getPlayingFile()
        seconds = player.getTime()
    except Exception:
        return None
    source = SubtitleSource(player)
    source.reset(playing_file)
    track = source.selected_track(seconds)
    if not track:
        return None
    settings = addon.getSettings()
    model_id = settings.getString("model_id").strip() or "eleven_flash_v2_5"
    return estimate_film_usage(
        track.cues,
        lambda value: _prepare_estimate_text(settings, value),
        model_id,
    )


def format_usage_report(usage, estimate=None):
    quota_is_credits = usage.source_units == "credits"
    quota_unit = "kredytów" if quota_is_credits else "znaków limitu"
    lines = [
        "Plan: %s" % (usage.tier or "nieznany"),
        "Status: %s" % (usage.status or "nieznany"),
        "Wykorzystano: %s / %s %s (%.1f%%)"
        % (
            _format_count(usage.used),
            _format_count(usage.limit),
            quota_unit,
            usage.used_percent,
        ),
        "Pozostało w limicie: %s %s"
        % (_format_count(usage.remaining), quota_unit),
    ]
    if not quota_is_credits:
        lines.append(
            "API ElevenLabs raportuje ten limit w znakach; szacunek kredytów filmu jest pokazany osobno."
        )
    if usage.reset_unix:
        try:
            reset = datetime.fromtimestamp(usage.reset_unix).strftime("%Y-%m-%d %H:%M")
            lines.append("Odnowienie limitu: %s" % reset)
        except (OSError, OverflowError, ValueError):
            pass
    if usage.max_credit_limit_extension == "unlimited":
        lines.append("Dodatkowe użycie: bez limitu ustawionego w ElevenLabs")
    elif usage.max_credit_limit_extension:
        lines.append(
            "Dopuszczony dodatkowy limit: %s kredytów"
            % _format_count(usage.max_credit_limit_extension)
        )
    if usage.overage_amount not in ("", "0", "0.0", "0.00"):
        lines.append(
            "Bieżące użycie dodatkowe: %s %s"
            % (usage.overage_amount, usage.overage_currency.upper())
        )

    lines.append("")
    if estimate is None:
        lines.append("Bieżący film: nie znaleziono jeszcze pełnego tekstowego pliku napisów.")
    else:
        estimated_quota_use = (
            estimate.rounded_credits if quota_is_credits else estimate.text_characters
        )
        lines.extend(
            [
                "Bieżący film — szacunek bazowy:",
                "• %s znaków do przeczytania" % _format_count(estimate.text_characters),
                "• około %s kredytów" % _format_count(estimate.rounded_credits),
                "• po filmie pozostanie około %s %s"
                % (_format_count(usage.remaining_after(estimated_quota_use)), quota_unit),
            ]
        )
        if estimated_quota_use > usage.remaining:
            lines.append(
                "UWAGA: film przekroczy wliczony limit o około %s %s."
                % (
                    _format_count(estimated_quota_use - usage.remaining),
                    quota_unit,
                )
            )
            if usage.max_credit_limit_extension:
                lines.append(
                    "Dalsze użycie może zostać pokryte przez PAYG albo rozliczenie dodatkowe; "
                    "dokładne zasady pokaże ElevenLabs."
                )
    lines.extend(
        [
            "",
            "To jest przybliżenie, nie rachunek. Cache i pominięte kwestie mogą zmniejszyć zużycie; "
            "specjalna stawka wybranego głosu lub umowa Enterprise może je zmienić.",
            "Dokładna cena pieniędzy jest zawsze pokazywana przez ElevenLabs przed płatnością.",
        ]
    )
    return "\n".join(lines)


def show_account_usage(addon):
    del addon
    notify("Darmowy głos urządzenia nie ma kredytów ani licznika użycia.")
    return False


def open_elevenlabs_billing(addon):
    del addon
    notify("Ta wersja korzysta z darmowego głosu urządzenia i nie obsługuje płatności ElevenLabs.")
    return False


def configure_auto_subtitles(addon):
    """Prefer the official OpenSubtitles.com provider after an explicit click."""

    try:
        modules = list_subtitle_modules(xbmc.executeJSONRPC)
    except (JsonRpcError, ValueError, TypeError):
        notify(addon.getLocalizedString(32213), True)
        return False
    module = find_subtitle_module(modules)
    if module is None:
        xbmcgui.Dialog().ok(
            addon.getLocalizedString(32210),
            addon.getLocalizedString(32211),
        )
        try:
            xbmc.executebuiltin(ACTIVATE_ADDON_BROWSER)
        except Exception:
            notify(addon.getLocalizedString(32213), True)
        return False

    try:
        if not module["enabled"]:
            jsonrpc_call(
                xbmc.executeJSONRPC,
                "Addons.SetAddonEnabled",
                {"addonid": module["id"], "enabled": True},
            )
        languages = get_setting_value(xbmc.executeJSONRPC, "subtitles.languages", [])
        changes = build_subtitle_provider_changes(
            OPENSUBTITLES_COM_ADDON_ID,
            languages,
        )
        apply_setting_changes(xbmc.executeJSONRPC, changes)
        addon.getSettings().setBool("auto_subtitles", True)
        notify(addon.getLocalizedString(32212) % module["name"])
        return True
    except (JsonRpcError, ValueError, TypeError):
        notify(addon.getLocalizedString(32213), True)
        return False


def main():
    addon = xbmcaddon.Addon(ADDON_ID)
    choices = [
        "Test darmowego głosu urządzenia",
        addon.getLocalizedString(32210),
        "Ustawienia",
        "Pomoc: darmowy lektor i napisy",
    ]
    selected = xbmcgui.Dialog().select("Kodi Lektor PL", choices)
    if selected == 0:
        test_voice(addon)
    elif selected == 1:
        configure_auto_subtitles(addon)
    elif selected == 2:
        addon.openSettings()
    elif selected == 3:
        xbmcgui.Dialog().ok(
            "Kodi Lektor PL — darmowy tryb",
            "1. W Kodi zainstaluj i włącz kompatybilną usługę TTS dla urządzenia.\n\n"
            "2. W systemie telewizora wybierz polski głos jako domyślny głos TTS.\n\n"
            "3. W tym menu wybierz „Ustaw OpenSubtitles.com dla polskich napisów”, a następnie zaloguj konto w ustawieniach tego dostawcy.\n\n"
            "4. Włącz film. Dodatek odczytuje pobrany tekst napisów; nie korzysta z ElevenLabs ani z klucza API.\n\n"
            "Jeżeli test nie mówi, usługa TTS nie jest zainstalowana lub nie jest zgodna z wersją Kodi.",
        )


if __name__ == "__main__":
    main()
