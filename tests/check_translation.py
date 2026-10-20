"""Translation boundary, asynchronous lifecycle and real panel-code checks."""
import ast
import builtins
from collections.abc import Mapping
import importlib.util
import json
from pathlib import Path
import queue
import socket
import ssl
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon/globalPlugins/extraSettings"
MAIN_THREAD = threading.get_ident()
builtins._ = lambda text: text

def stub(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    sys.modules[name] = result
    return result

package = stub("translationTestPackage")
package.__path__ = [str(ADDON)]
stub("addonHandler", initTranslation=lambda: None)
messages = []
callbacks = queue.Queue()
delays = []
def message(text):
    assert threading.get_ident() == MAIN_THREAD, "Speech called from worker"
    messages.append(text)
stub("ui", message=message)
stub("core", callLater=lambda delay, func, *args: delays.append((func, args)))
stub("wx", CallAfter=lambda func, *args: callbacks.put((func, args)))
api = stub("api", isTypingProtected=lambda: False)
stub("textInfos", POSITION_CARET="caret", UNIT_LINE="line")

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ADDON / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

service = load("translationTestPackage.translationService", "translationService.py")
translation = load("translationTestPackage.lineTranslation", "lineTranslation.py")
translation.REQUEST_INTERVAL = 0

class Response:
    status = 200
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, maximum): return self.data[:maximum]

class ServiceTests(unittest.TestCase):
    def test_legacy_trusted_chain_compatibility_is_scoped(self):
        legacy = ssl.SSLCertVerificationError(1, 'legacy root')
        legacy.verify_code = 85
        good = Response(b'[[["Merhaba"]]]')
        with patch.object(service, 'urlopen', side_effect=[URLError(legacy), good]) as opener:
            self.assertEqual(service.fetchTranslation('Hello', 'tr'), 'Merhaba')
        self.assertEqual(opener.call_count, 2)
        context = opener.call_args.kwargs['context']
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertFalse(context.verify_flags & ssl.VERIFY_X509_STRICT)
        self.assertGreater(len(context.get_ca_certs()), 0)
        for code in (10, 18, 20, 62):
            failure = ssl.SSLCertVerificationError(1, 'untrusted or invalid certificate')
            failure.verify_code = code
            with patch.object(service, 'urlopen', side_effect=URLError(failure)) as opener:
                with self.assertRaises(service.TranslationFailure):
                    service.fetchTranslation('Hello', 'tr')
                self.assertEqual(opener.call_count, 1)

    def test_segmented_unicode_response(self):
        raw = json.dumps([[["Merhaba. ", "Hello."], [None], ["Dünya!", "World!"]], None, "en"]).encode()
        self.assertEqual(service.parseTranslation(raw), "Merhaba. Dünya!")

    def test_bad_or_empty_response_is_never_spoken_as_translation(self):
        for data in (b"<html>captcha</html>", b"{}", b"null", b"[]", b"[null]", b"[[[null]]]", b"[[[42]]]", b'[[[""]]]', b"\xff"):
            with self.subTest(data=data), self.assertRaises(service.TranslationFailure):
                service.parseTranslation(data)

    def test_post_hides_source_from_url_and_encodes_reserved_characters(self):
        text = "A&B? #ş + 世界\nİkinci satır"
        def openRequest(request, timeout):
            self.assertEqual(request.get_method(), "POST")
            self.assertTrue(request.full_url.startswith("https://translate.googleapis.com/"))
            self.assertNotIn("q=", request.full_url)
            self.assertEqual(parse_qs(request.data.decode("ascii"))["q"], [text])
            self.assertIn("tl=tr", request.full_url)
            self.assertGreater(timeout, 0)
            return Response(b'[[["translated"]]]')
        with patch.object(service, "urlopen", openRequest):
            self.assertEqual(service.fetchTranslation(text, "tr"), "translated")

    def test_invalid_input_makes_no_network_request(self):
        with patch.object(service, "urlopen") as network:
            for text, target in (("", "tr"), ("   ", "tr"), ("x" * 5001, "tr"), ("hello", "not-a-language")):
                with self.assertRaises(service.TranslationFailure): service.fetchTranslation(text, target)
            network.assert_not_called()

    def test_size_limit_errors_and_timeout_categories(self):
        for error, reason in ((TimeoutError(), "timeout"), (URLError(socket.timeout()), "timeout"),
                              (URLError("network down"), "unavailable"),
                              (HTTPError("https://example.invalid", 429, "limit", {}, None), "limited"),
                              (HTTPError("https://example.invalid", 403, "forbidden", {}, None), "limited"),
                              (HTTPError("https://example.invalid", 500, "server", {}, None), "unavailable")):
            with self.subTest(reason=reason), patch.object(service, "urlopen", side_effect=error):
                with self.assertRaises(service.TranslationFailure) as caught:
                    service.fetchTranslation("hello", "tr")
                self.assertEqual(caught.exception.reason, reason)
                self.assertNotIn("hello", str(caught.exception))
        with patch.object(service, "urlopen", return_value=Response(b" " * (service.MAX_RESPONSE + 1))):
            with self.assertRaises(service.TranslationFailure) as caught: service.fetchTranslation("hello", "tr")
            self.assertEqual(caught.exception.reason, "response")

    def test_all_bundled_languages_have_localized_names_without_network(self):
        with patch.object(service, "urlopen", side_effect=AssertionError("Settings must be offline")):
            tr, en = dict(service.languageChoices("tr_TR")), dict(service.languageChoices("en"))
        self.assertEqual(len(tr), 249)
        self.assertEqual(tr.keys(), en.keys())
        self.assertEqual(tr["tr"], "Türkçe")
        self.assertEqual(en["tr"], "Turkish")
        self.assertTrue(all(isinstance(name, str) and name for name in tr.values()))

class TextInfo:
    def __init__(self, text, protected=False):
        self._text = text
        self.NVDAObjectAtStart = types.SimpleNamespace(isProtected=protected)
        self.read = False
    def expand(self, unit): assert unit == "line"
    @property
    def text(self):
        self.read = True
        return self._text

class Container:
    isProtected = False
    def __init__(self, text, protected=False): self.info = TextInfo(text, protected)
    def invalidateCache(self): pass
    def makeTextInfo(self, position):
        assert position == "caret"
        return self.info

class LineTests(unittest.TestCase):
    def setUp(self):
        api.isTypingProtected = lambda: False
    def test_reading_line_preserves_internal_text_without_moving_caret(self):
        container = Container("  Hello  world & 世界.\r\n")
        api.getCaretObject = lambda: container
        self.assertEqual(translation.currentLine(), "Hello  world & 世界.")
    def test_protected_focus_and_protected_browse_range_are_not_read(self):
        container = Container("secret")
        api.getCaretObject = lambda: container
        api.isTypingProtected = lambda: True
        with self.assertRaises(service.TranslationFailure): translation.currentLine()
        self.assertFalse(container.info.read)
        api.isTypingProtected = lambda: False
        container.info.NVDAObjectAtStart.isProtected = True
        with self.assertRaises(service.TranslationFailure): translation.currentLine()
        self.assertFalse(container.info.read)
    def test_blank_long_and_inaccessible_lines(self):
        for text, reason in ((" \r\n", "empty"), ("x" * 5001, "length")):
            api.getCaretObject = lambda text=text: Container(text)
            with self.assertRaises(service.TranslationFailure) as caught: translation.currentLine()
            self.assertEqual(caught.exception.reason, reason)
        api.getCaretObject = lambda: None
        with self.assertRaises(service.TranslationFailure) as caught: translation.currentLine()
        self.assertEqual(caught.exception.reason, "caret")

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        messages.clear()
        delays.clear()
        while not callbacks.empty(): callbacks.get_nowait()
        api.isTypingProtected = lambda: False
        self.settings = {"lineTranslation": True, "translationTarget": "tr"}
        self.controllers = []
        self.releases = []
    def controller(self, fetch, clock=None):
        def settings():
            self.assertEqual(threading.get_ident(), MAIN_THREAD, "Config read by network worker")
            return self.settings
        controller = translation.LineTranslation(settings, fetch=fetch, clock=clock)
        self.controllers.append(controller)
        return controller
    def pump(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            try: func, args = callbacks.get(timeout=0.02)
            except queue.Empty: continue
            func(*args)
        self.assertTrue(predicate(), messages)
    def tearDown(self):
        for release in self.releases: release.set()
        for controller in self.controllers:
            controller.terminate()
            if controller._worker: controller._worker.join(1)
    def blocked(self):
        started, release = threading.Event(), threading.Event()
        self.releases.append(release)
        calls = []
        def fetch(text, target):
            calls.append((text, target))
            started.set()
            release.wait(2)
            return "translated:" + text
        return fetch, started, release, calls

    def test_manual_switch_is_independent_and_keeps_legacy_default(self):
        controller = self.controller(lambda text, target: 'translated:' + text)
        self.assertTrue(controller._settingsKey()[0], 'Old configuration lost manual translation')
        self.settings.update(manualTranslation=False, automaticTranslation=True)
        with patch.object(translation, 'currentLine', side_effect=AssertionError('Disabled manual mode read caret')):
            controller.translateCurrentLine()
        self.assertIn('Enable line translation', messages[-1])
        self.assertIsNone(controller._worker)
        self.settings['manualTranslation'] = True
        self.settings['automaticTranslation'] = False
        with patch.object(translation, 'currentLine', return_value='manual'):
            controller.translateCurrentLine()
        self.pump(lambda: 'translated:manual' in messages)

    def test_disabling_manual_drops_in_flight_result_with_master_still_on(self):
        fetch, started, release, calls = self.blocked()
        controller = self.controller(fetch)
        controller._request('pending', 'tr')
        self.assertTrue(started.wait(1))
        self.settings.update(manualTranslation=False, automaticTranslation=True)
        controller.applySettings()
        release.set()
        self.pump(lambda: controller._current is None)
        while not callbacks.empty():
            func, args = callbacks.get_nowait(); func(*args)
        self.assertFalse(controller._cache)
        self.assertNotIn('translated:pending', messages)
        self.assertTrue(self.settings['lineTranslation'])
    def test_submit_is_nonblocking_duplicate_is_not_sent_and_latest_pending_wins(self):
        fetch, started, release, calls = self.blocked()
        controller = self.controller(fetch)
        before = time.monotonic()
        controller._request("first", "tr")
        self.assertLess(time.monotonic() - before, 0.2)
        self.assertTrue(started.wait(1))
        controller._request("first", "tr")
        controller._request("discarded", "tr")
        controller._request("last", "tr")
        release.set()
        self.pump(lambda: "translated:last" in messages)
        self.assertEqual(calls, [("first", "tr"), ("last", "tr")])
        self.assertNotIn("translated:first", messages)
        self.assertNotIn("translated:discarded", messages)
    def test_disabled_target_changed_protected_or_terminated_drops_late_result(self):
        for action in ("disable", "target", "protected", "terminate"):
            with self.subTest(action=action):
                self.settings.update(lineTranslation=True, translationTarget="tr")
                api.isTypingProtected = lambda: False
                messages.clear()
                fetch, started, release, calls = self.blocked()
                controller = self.controller(fetch)
                controller._request(action, "tr")
                self.assertTrue(started.wait(1))
                if action == "disable":
                    self.settings["lineTranslation"] = False
                    controller.applySettings()
                elif action == "target": self.settings["translationTarget"] = "en"
                elif action == "protected": api.isTypingProtected = lambda: True
                else: controller.terminate()
                release.set()
                # Pump until the response is queued or the worker terminates.
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline and not callbacks.qsize() and controller._worker.is_alive():
                    if controller._current is None: break
                    time.sleep(0.005)
                while not callbacks.empty():
                    func, args = callbacks.get_nowait(); func(*args)
                self.assertFalse(any(text.startswith("translated:") for text in messages))
                controller.terminate()
    def test_memory_cache_target_separation_expiry_and_capacity(self):
        clock = [0.0]
        calls = []
        controller = self.controller(lambda text, target: calls.append((text, target)) or target + ":" + text,
                                     clock=lambda: clock[0])
        controller._request("hello", "tr")
        self.pump(lambda: len(messages) == 1)
        controller._request("hello", "tr")
        self.assertEqual(len(calls), 1)
        clock[0] = 121
        controller._request("hello", "tr")
        self.pump(lambda: len(messages) == 3)
        self.assertEqual(len(calls), 2)
        self.settings["translationTarget"] = "en"
        controller.applySettings()
        self.assertFalse(controller._cache)
        for i in range(35):
            count = len(messages)
            controller._request(str(i), "en")
            self.pump(lambda count=count: len(messages) > count)
        self.assertEqual(len(controller._cache), 32)
        self.assertNotIn(("0", "en"), controller._cache)
        self.settings["lineTranslation"] = False
        controller.applySettings()
        self.assertFalse(controller._cache)
    def test_failure_message_and_rate_limit_prevent_network_retries(self):
        calls = []
        def fetch(text, target):
            calls.append(text)
            raise service.TranslationFailure("limited")
        controller = self.controller(fetch)
        controller._request("one", "tr")
        self.pump(lambda: bool(messages))
        controller._request("two", "tr")
        self.assertEqual(calls, ["one"])
        self.assertTrue(all("limiting" in text for text in messages))
    def test_empty_line_cancels_previous_request_and_disabled_feature_does_not_read(self):
        fetch, started, release, calls = self.blocked()
        controller = self.controller(fetch)
        controller._request("old", "tr")
        self.assertTrue(started.wait(1))
        api.getCaretObject = lambda: Container("")
        controller.translateCurrentLine()
        release.set()
        self.pump(lambda: controller._current is None)
        while not callbacks.empty():
            func, args = callbacks.get_nowait(); func(*args)
        self.assertNotIn("translated:old", messages)
        self.settings["lineTranslation"] = False
        with patch.object(translation, "currentLine", side_effect=AssertionError("Disabled feature read text")):
            controller.translateCurrentLine()
    def test_wait_notice_is_suppressed_after_fast_completion(self):
        controller = self.controller(lambda text, target: "done")
        controller._request("fast", "tr")
        self.pump(lambda: messages == ["done"])
        for func, args in delays: func(*args)
        self.assertEqual(messages, ["done"])
    def test_request_interval_is_respected(self):
        previous = translation.REQUEST_INTERVAL
        translation.REQUEST_INTERVAL = 0.03
        times = []
        try:
            controller = self.controller(lambda text, target: times.append(time.monotonic()) or text)
            controller._request("one", "tr")
            self.pump(lambda: messages == ["one"])
            controller._request("two", "tr")
            self.pump(lambda: messages == ["one", "two"])
            self.assertGreaterEqual(times[1] - times[0], 0.025)
        finally: translation.REQUEST_INTERVAL = previous

    def test_gui_shutdown_and_unavailable_focus_do_not_leak_errors(self):
        controller = self.controller(lambda text, target: "done")
        with patch.object(translation.wx, "CallAfter", side_effect=RuntimeError("wx has closed")):
            controller._request("shutdown", "tr")
            controller._worker.join(1)
            self.assertFalse(controller._worker.is_alive())
        api.isTypingProtected = lambda: (_ for _ in ()).throw(RuntimeError("no focus"))
        controller._finish(controller._serial, ("shutdown", "tr"), "done", None)
        controller._announceWaiting(controller._serial)
        self.assertEqual(messages, [])

class PanelTests(unittest.TestCase):
    def setUp(self):
        self.conf = {"webCycle": False, "skipWebBlankLines": False, "announceClipboard": False, "recordHistory": False,
                     "historyLimit": 100, "historyOrder": "newestFirst", "replaceApplicationsKey": False,
                     "applicationsGesture": "", "appendClipboard": False, "numpadNavigation": False, "textStatistics": False, "inspectClipboard": False, "bookmarks": False, "quickFind": False,
                     "lineTranslation": False, "manualTranslation": True,
                     "automaticTranslation": False, "translationTarget": "tr"}
        class Control:
            def __init__(self, parent=None, label="", value="", choices=None, **kwargs):
                self.label, self.value, self.choices = label, value, choices
                self.selection, self.visible = -1, True
            def SetValue(self, value): self.value = value
            def GetValue(self): return self.value
            def SetLabel(self, value): self.label = value
            def SetSelection(self, value): self.selection = value
            def GetSelection(self): return self.selection
            def Show(self, value): self.visible = bool(value)
            def Bind(self, *args): pass
            def SetSizer(self, *args): pass
            def SetFocus(self): pass
        class Panel(Control):
            def Layout(self): pass
            def _sendLayoutUpdatedEvent(self): pass
            def onPanelActivated(self): pass
            def _validationErrorMessageBox(self, *args): self.error = args
        class Helper:
            def __init__(self, parent, sizer=None): self.parent = parent
            def addItem(self, item): return item
            def addLabeledControl(self, label, kind, **kwargs): return kind(self.parent, label=label, **kwargs)
        self.gestures = ["kb:NVDA+shift+t"]
        self.info = types.SimpleNamespace(moduleName="testedSettingsPanel", scriptName="translateCurrentLine",
                                         gestures=self.gestures)
        self.autoGestures = ["kb:NVDA+shift+o"]
        self.autoInfo = types.SimpleNamespace(moduleName="testedSettingsPanel", scriptName="toggleAutomaticTranslation",
                                             gestures=self.autoGestures)
        self.statisticsGestures = ["kb:NVDA+shift+k"]
        self.statisticsInfo = types.SimpleNamespace(moduleName="testedSettingsPanel", scriptName="showTextStatistics",
                                                   gestures=self.statisticsGestures)
        self.clipboardGestures = ["kb:NVDA+shift+c"]
        self.clipboardInfo = types.SimpleNamespace(moduleName="testedSettingsPanel", scriptName="inspectClipboard",
                                                  gestures=self.clipboardGestures)
        self.bookmarkGestures = ["kb:NVDA+shift+y"]
        self.bookmarkInfo = types.SimpleNamespace(moduleName="testedSettingsPanel", scriptName="showBookmarks",
                                                 gestures=self.bookmarkGestures)
        self.quickFindGestures = ["kb:NVDA+shift+h"]
        self.quickFindInfo = types.SimpleNamespace(moduleName="testedSettingsPanel", scriptName="showQuickFind",
                                                  gestures=self.quickFindGestures)
        self.applies = []
        scope = dict(__name__="testedSettingsPanel", SettingsPanel=Panel,
                     Mapping=Mapping, inputCore=types.SimpleNamespace(manager=types.SimpleNamespace(
                         getAllGestureMappings=lambda: {"extra": {"translate": self.info, "auto": self.autoInfo, "statistics": self.statisticsInfo, "clipboard": self.clipboardInfo, "bookmarks": self.bookmarkInfo, "quickFind": self.quickFindInfo}}),
                         getDisplayTextForGestureIdentifier=lambda gesture: (None, gesture.partition(":")[2])),
                     config=types.SimpleNamespace(conf={"extraSettings": self.conf}), CONFIG_SECTION="extraSettings",
                     guiHelper=types.SimpleNamespace(BoxSizerHelper=Helper),
                     languageHandler=types.SimpleNamespace(getLanguage=lambda: "tr_TR"),
                     languageChoices=service.languageChoices, shortcutLabel=lambda value: value,
                     historyShortcutLabel=lambda: "NVDA+G", parseLimit=int,
                     _speechHistoryController=None,
                     _skipBlankLinesController=None,
                     _textStatisticsController=None,
                     _clipboardInspectorController=None, _bookmarksController=None, _quickFindController=None,
                     _translationController=types.SimpleNamespace(applySettings=lambda: self.applies.append(True)),
                     wx=types.SimpleNamespace(CheckBox=Control, Panel=Panel, TextCtrl=Control, Choice=Control,
                         Button=Control, BoxSizer=lambda direction: object(), VERTICAL=1,
                         EVT_CHECKBOX=1, EVT_SET_FOCUS=2, EVT_BUTTON=3), _=lambda text: text)
        tree = ast.parse((ADDON / "__init__.py").read_text(encoding="utf-8-sig"))
        nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                 and node.name in ("ExtraSettingsPanel", "commandShortcutLabel")]
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "real-panel-code", "exec"), scope)
        self.panel = scope["ExtraSettingsPanel"]()
        with patch.object(service, "urlopen", side_effect=AssertionError("Settings made a network request")):
            self.panel.makeSettings(object())
    def test_default_language_hidden_options_apply_and_immediate_controller_update(self):
        panel = self.panel
        self.assertFalse(panel.translationOptions.visible)
        self.assertEqual(panel._targetCodes[panel.translationTarget.GetSelection()], "tr")
        self.assertEqual(len(panel.translationTarget.choices), 249)
        self.assertIn("NVDA+shift+T", panel.lineTranslation.label)
        self.assertFalse(panel.automaticTranslation.GetValue())
        self.assertIn("NVDA+shift+O", panel.automaticTranslation.label)
        self.assertEqual(panel.translation.label, "Translation")
        self.assertTrue(panel.lineTranslation.GetValue())
        panel.translation.SetValue(True)
        panel.onTranslationChanged(None)
        self.assertTrue(panel.translationOptions.visible)
        self.assertFalse(self.conf["lineTranslation"], "Unapplied checkbox changed configuration")
        panel.translationTarget.SetSelection(panel._targetCodes.index("de"))
        panel.automaticTranslation.SetValue(True)
        self.assertTrue(panel.isValid())
        panel.onSave()
        self.assertTrue(self.conf["lineTranslation"])
        self.assertTrue(self.conf["automaticTranslation"])
        self.assertEqual(self.conf["translationTarget"], "de")
        self.assertEqual(self.applies, [True])
        panel.translation.SetValue(False)
        panel.onTranslationChanged(None)
        panel.onSave()
        self.assertFalse(panel.translationOptions.visible)
        self.assertFalse(self.conf["lineTranslation"])
        self.assertTrue(self.conf["automaticTranslation"], "Master switch erased a child preference")
        self.assertTrue(self.conf["manualTranslation"])

    def test_manual_and_automatic_are_independent(self):
        panel = self.panel
        panel.translation.SetValue(True)
        for manual, automatic in ((False, True), (True, False), (True, True), (False, False)):
            panel.lineTranslation.SetValue(manual)
            panel.automaticTranslation.SetValue(automatic)
            panel.onSave()
            self.assertTrue(self.conf['lineTranslation'])
            self.assertEqual(self.conf['manualTranslation'], manual)
            self.assertEqual(self.conf['automaticTranslation'], automatic)

    def test_bookmarks_option_and_reassigned_shortcut(self):
        self.assertFalse(self.panel.bookmarks.GetValue())
        self.assertIn('NVDA+shift+Y', self.panel.bookmarks.label)
        self.panel.bookmarks.SetValue(True)
        self.panel.onSave()
        self.assertTrue(self.conf['bookmarks'])
        self.bookmarkGestures[:] = ['kb:NVDA+alt+y']
        self.panel.onBookmarksFocus(types.SimpleNamespace(Skip=lambda:None))
        self.assertIn('NVDA+alt+Y', self.panel.bookmarks.label)
        self.bookmarkGestures.clear()
        self.panel.onPanelActivated()
        self.assertIn('No shortcut assigned', self.panel.bookmarks.label)

    def test_quick_find_option_and_reassigned_shortcut(self):
        self.assertFalse(self.panel.quickFind.GetValue())
        self.assertIn('NVDA+shift+H', self.panel.quickFind.label)
        self.panel.quickFind.SetValue(True)
        self.panel.onSave()
        self.assertTrue(self.conf['quickFind'])
        self.quickFindGestures[:] = ['kb:NVDA+alt+h']
        self.panel.onQuickFindFocus(types.SimpleNamespace(Skip=lambda:None))
        self.assertIn('NVDA+alt+H', self.panel.quickFind.label)
        self.quickFindGestures.clear()
        self.panel.onPanelActivated()
        self.assertIn('No shortcut assigned', self.panel.quickFind.label)
    def test_shortcut_reassignment_multiple_and_removed_assignments_refresh_label(self):
        self.gestures[:] = ["kb:NVDA+control+j", "kb:NVDA+alt+y"]
        event = types.SimpleNamespace(Skip=lambda: None)
        self.panel.onTranslationFocus(event)
        self.assertIn("NVDA+control+J", self.panel.lineTranslation.label)
        self.assertIn("NVDA+alt+Y", self.panel.lineTranslation.label)
        self.gestures.clear()
        self.panel.onPanelActivated()
        self.assertIn("No shortcut assigned", self.panel.lineTranslation.label)
        self.autoGestures[:] = ["kb:NVDA+alt+o"]
        self.panel.onAutomaticTranslationFocus(event)
        self.assertIn("NVDA+alt+O", self.panel.automaticTranslation.label)
        self.autoGestures.clear()
        self.panel.onPanelActivated()
        self.assertIn("No shortcut assigned", self.panel.automaticTranslation.label)
    def test_invalid_target_does_not_save_any_changes(self):
        self.panel.translationTarget.SetSelection(-1)
        self.assertFalse(self.panel.isValid())
        before = self.conf.copy()
        with self.assertRaises(ValueError): self.panel.onSave()
        self.assertEqual(self.conf, before)

if __name__ == "__main__":
    unittest.main(verbosity=2)
