from __future__ import absolute_import

import os
import re
import sys
import traceback

import xbmc
import xbmcgui
import xbmcvfs


LIB = xbmcvfs.translatePath("special://home/addons/service.subtitle.tts.pl/resources/lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

TITLE = "Kodi Lektor PL"


def _redact_traceback(value):
    """Keep diagnostic context while removing API-key-shaped secrets."""

    value = re.sub(
        r"(?i)(xi-api-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+",
        r"\1[UKRYTO]",
        value,
    )
    return re.sub(r"\bsk_[A-Za-z0-9_-]{20,}\b", "[UKRYTO]", value)


def _run_service():
    # Import inside the guard as Kodi may reuse a Python interpreter while an
    # add-on is being updated and temporarily expose stale cached modules.
    from reader_service import run  # noqa: E402

    run()


def guarded_main():
    try:
        _run_service()
        return True
    except Exception as exc:
        details = _redact_traceback(traceback.format_exc())
        try:
            xbmc.log("[%s] Błąd startu:\n%s" % (TITLE, details), xbmc.LOGERROR)
        except Exception:
            pass
        try:
            xbmcgui.Dialog().notification(
                TITLE,
                "Nie udało się uruchomić lektora (%s). Sprawdź dziennik Kodi."
                % type(exc).__name__,
                xbmcgui.NOTIFICATION_ERROR,
                8000,
            )
        except Exception:
            pass
        return False


if __name__ == "__main__":
    guarded_main()
