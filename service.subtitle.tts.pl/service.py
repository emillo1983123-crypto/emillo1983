from __future__ import absolute_import

import os
import sys

import xbmcvfs


LIB = xbmcvfs.translatePath("special://home/addons/service.subtitle.tts.pl/resources/lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from reader_service import run  # noqa: E402


if __name__ == "__main__":
    run()
