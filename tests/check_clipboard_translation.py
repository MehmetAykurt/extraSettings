"""Clipboard-only translation permission, read failures and async lifecycle."""
from unittest.mock import patch
import unittest
import check_translation as base


class ClipboardTests(unittest.TestCase):
    setUp = base.LifecycleTests.setUp
    tearDown = base.LifecycleTests.tearDown
    pump = base.LifecycleTests.pump
    blocked = base.LifecycleTests.blocked
    def controller(self, fetch, clock=None):
        controller = base.translation.ClipboardTranslation(lambda: self.settings, fetch=fetch, clock=clock)
        self.controllers.append(controller)
        return controller

    def test_clipboard_read_once_preserves_lines_and_never_writes(self):
        controller = self.controller(lambda text, target: 'translated:' + text)
        self.settings.update(manualTranslation=False, clipboardTranslation=True)
        source = 'First line.\nSecond line.'
        with patch.object(base.api, 'getClipData', return_value=source, create=True) as read:
            controller.translateClipboard()
        read.assert_called_once_with()
        self.pump(lambda: 'translated:' + source in base.messages)

    def test_disabled_master_or_clipboard_permission_never_reads(self):
        controller = self.controller(lambda text, target: self.fail('Unexpected request'))
        for master, permitted in ((False, True), (True, False)):
            self.settings.update(lineTranslation=master, clipboardTranslation=permitted)
            with patch.object(base.api, 'getClipData', side_effect=AssertionError('Read disabled clipboard'), create=True):
                controller.translateClipboard()
        self.assertIsNone(controller._worker)

    def test_empty_nontext_busy_long_and_protected_clipboard_make_no_request(self):
        controller = self.controller(lambda text, target: self.fail('Unexpected request'))
        for value in ('', ' \n\t', None, 'x' * 5001):
            with patch.object(base.api, 'getClipData', return_value=value, create=True):
                controller.translateClipboard()
        with patch.object(base.api, 'getClipData', side_effect=OSError('busy'), create=True):
            controller.translateClipboard()
        base.api.isTypingProtected = lambda: True
        with patch.object(base.api, 'getClipData', side_effect=AssertionError('Read protected context'), create=True):
            controller.translateClipboard()
        self.assertIsNone(controller._worker)
        self.assertTrue(any('no text on the clipboard' in text for text in base.messages))
        self.assertTrue(any('could not be read' in text for text in base.messages))

    def test_clipboard_disable_discards_pending_response(self):
        fetch, started, release, calls = self.blocked()
        controller = self.controller(fetch)
        with patch.object(base.api, 'getClipData', return_value='clipboard text', create=True):
            controller.translateClipboard()
        self.assertTrue(started.wait(1))
        self.settings['clipboardTranslation'] = False
        controller.applySettings()
        release.set()
        self.pump(lambda: controller._current is None)
        while not base.callbacks.empty():
            func, args = base.callbacks.get_nowait(); func(*args)
        self.assertNotIn('translated:clipboard text', base.messages)
        self.assertFalse(controller._cache)


if __name__ == '__main__':
    unittest.main(verbosity=2)
