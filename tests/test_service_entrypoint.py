import importlib.util
import os
import sys
import types
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "service.subtitle.tts.pl")


def _load_service_module(logs, notifications):
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGERROR = 4
    xbmc.log = lambda message, level: logs.append((message, level))

    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.NOTIFICATION_ERROR = "error"
    xbmcgui.Dialog = lambda: types.SimpleNamespace(
        notification=lambda *args: notifications.append(args)
    )

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda value: value

    modules = {"xbmc": xbmc, "xbmcgui": xbmcgui, "xbmcvfs": xbmcvfs}
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        path = os.path.join(ADDON, "service.py")
        spec = importlib.util.spec_from_file_location(
            "kodi_lektor_service_entrypoint_test", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class ServiceEntrypointTests(unittest.TestCase):
    def test_startup_exception_is_redacted_logged_and_not_raised(self):
        logs = []
        notifications = []
        module = _load_service_module(logs, notifications)
        secret = "sk_abcdefghijklmnopqrstuvwxyz1234567890"

        def fail():
            raise RuntimeError("startup failed with %s" % secret)

        module._run_service = fail
        self.assertFalse(module.guarded_main())
        self.assertEqual(len(logs), 1)
        self.assertIn("Traceback", logs[0][0])
        self.assertIn("RuntimeError", logs[0][0])
        self.assertIn("[UKRYTO]", logs[0][0])
        self.assertNotIn(secret, logs[0][0])
        self.assertEqual(len(notifications), 1)
        self.assertIn("RuntimeError", notifications[0][1])
        self.assertNotIn(secret, notifications[0][1])


if __name__ == "__main__":
    unittest.main()
