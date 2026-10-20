"""Exercise existing controllers with in-memory clipboard and synthetic documents."""
from contextlib import contextmanager
import importlib.util
import types
import unittest
from unittest.mock import patch
import check_translation as base

class Action:
    def __init__(self): self.handlers = []
    def register(self, handler): self.handlers.append(handler)
    def unregister(self, handler): self.handlers.remove(handler)

clipboard = {'text': '', 'formats': set(), 'failWrite': False}
@contextmanager
def openClipboard(*args): yield
def getData(fmt): return clipboard['text']
def setData(fmt, text):
    if clipboard['failWrite']: raise OSError('synthetic locked clipboard')
    clipboard['text'] = text; clipboard['formats'] = {13}
def emptyClipboard(): clipboard.update(text='', formats=set())
win = base.stub('winUser', CF_UNICODETEXT=13, openClipboard=openClipboard,
    getClipboardData=getData, setClipboardData=setData, emptyClipboard=emptyClipboard,
    getForegroundWindow=lambda: 7, getKeyState=lambda vk: 0)
manager = types.SimpleNamespace(isInputHelpActive=False, _captureFunc=None, emulateGesture=lambda gesture: None)
inputCore = base.stub('inputCore', manager=manager, decide_executeGesture=Action(),
    getDisplayTextForGestureIdentifier=lambda value: (None, value.partition(':')[2]))
scripts = []
base.stub('scriptHandler', queueScript=lambda script, gesture: scripts.append((script, gesture)))
base.stub('controlTypes', State=types.SimpleNamespace(SELECTED='selected', EDITABLE='editable'),
    OutputReason=types.SimpleNamespace(CARET='caret'))
base.stub('gui', mainFrame=types.SimpleNamespace(Handle=1))
base.textInfos = base.sys.modules['textInfos']
base.textInfos.POSITION_SELECTION = 'selection'
base.textInfos.POSITION_FIRST = 'first'; base.textInfos.POSITION_LAST = 'last'; base.textInfos.UNIT_PARAGRAPH = 'paragraph'

announcements = base.load('translationTestPackage.clipboardAnnouncements', 'clipboardAnnouncements.py')
append = base.load('translationTestPackage.appendClipboard', 'appendClipboard.py')
buffer = base.load('translationTestPackage.historyBuffer', 'historyBuffer.py')
preSpeech = Action()
base.stub('speech.extensions', pre_speechQueued=preSpeech)
history = base.load('translationTestPackage.speechHistory', 'speechHistory.py')
class Key:
    isModifier = False
    modifiers = ()
    def __init__(self, name):
        self.normalizedIdentifiers = self.identifiers = ['kb:' + name]
        self.name = name
    @classmethod
    def fromName(cls, value): return cls(value)
base.stub('keyboardHandler', KeyboardInputGesture=Key)
applications = base.load('translationTestPackage.applicationsKey', 'applicationsKey.py')

class Browse:
    def _quickNavScript(self, gesture, kind, direction, errorMessage, readUnit):
        item = next(iter(self._iterNodesByType(kind, direction, self.pos)), None)
        self.result = item
        return item
base.stub('browseMode', BrowseModeTreeInterceptor=Browse,
    TextInfoQuickNavItem=lambda kind, doc, info, **kwargs: info.index)
web = base.load('translationTestPackage.webCycle', 'webCycle.py')

class Selection:
    def __init__(self, text, collapsed=False): self.text, self.isCollapsed = text, collapsed
class Container:
    states = set(); sleepMode = False
    def __init__(self, text='', collapsed=False): self.selection = Selection(text, collapsed)
    def invalidateCache(self): pass
    def makeTextInfo(self, position): return self.selection

class ClipboardTests(unittest.TestCase):
    def setUp(self):
        clipboard.update(text='', formats=set(), failWrite=False)
        base.messages.clear(); base.delays.clear(); scripts.clear()
        base.api.isTypingProtected = lambda: False
        self.focus = Container('chosen')
        base.api.getCaretObject = base.api.getFocusObject = lambda: self.focus
        manager.isInputHelpActive = False; manager._captureFunc = None
        self.sent = []; manager.emulateGesture = self.sent.append
        def enum(previous):
            formats = sorted(clipboard['formats'])
            return next((fmt for fmt in formats if fmt > previous), 0)
        self.patches = [patch.object(announcements, '_enumClipboardFormats', enum),
            patch.object(announcements, '_hasClipboardFormat', lambda fmt: fmt in clipboard['formats']),
            patch.object(append, '_enumClipboardFormats', enum),
            patch.object(append, '_hasClipboardFormat', lambda fmt: fmt in clipboard['formats'])]
        for item in self.patches: item.start()
    def tearDown(self):
        for item in self.patches: item.stop()
    def test_append_empty_existing_and_line_endings(self):
        append.appendSelection()
        self.assertEqual(clipboard['text'], 'chosen')
        append.appendSelection()
        self.assertEqual(clipboard['text'], 'chosen\r\nchosen')
        self.assertEqual(append.combinedText('old\r\n', 'new'), 'old\r\nnew')
        self.assertEqual(append.combinedText('old', '\nnew'), 'old\nnew')
        self.assertEqual(append.combinedText('old', ' '), 'old\r\n ')
    def test_append_keeps_non_text_clipboard(self):
        clipboard.update(formats={15}, text='')
        append.appendSelection()
        self.assertEqual(clipboard['formats'], {15})
        self.assertIn('non-text data', base.messages[-1])
    def test_append_blank_selection_protected_and_write_failure(self):
        self.focus = Container('', collapsed=True)
        append.appendSelection(); self.assertIn('No selected text', base.messages[-1])
        base.api.isTypingProtected = lambda: True
        self.focus = Container('password')
        append.appendSelection(); self.assertIn('could not be read', base.messages[-1])
        self.assertEqual(clipboard['text'], '')
        base.api.isTypingProtected = lambda: False
        clipboard['failWrite'] = True
        append.appendSelection(); self.assertIn('Could not append', base.messages[-1])
    def test_paste_accepts_files_images_spaces_and_rejects_empty_text(self):
        for formats, text, expected in ((set(),'',False), ({13},'',False), ({13},' ',True),
                                          ({15},'',True), ({2},'',True), ({13,15},'',True)):
            clipboard.update(formats=formats, text=text)
            self.assertEqual(announcements.hasClipboardData(), expected)
        with patch.object(win, 'openClipboard', side_effect=OSError('locked')):
            self.assertIsNone(announcements.hasClipboardData())
    def test_selection_supports_one_character_browse_and_unknown_editable(self):
        self.focus = Container('x')
        self.assertTrue(announcements.hasSelectedData(self.focus))
        self.focus = Container('', collapsed=True)
        self.assertFalse(announcements.hasSelectedData(self.focus))
        broken = types.SimpleNamespace(states={'editable'})
        base.api.getCaretObject = lambda: broken
        self.assertIsNone(announcements.hasSelectedData(broken))
    def test_editing_command_executes_once_and_keeps_disabled_queued_command(self):
        enabled = [True]
        controller = announcements.ClipboardAnnouncements(lambda: enabled[0])
        key = Key('control+c')
        try:
            self.assertFalse(controller._onGesture(key))
            self.assertEqual(len(scripts), 1)
            enabled[0] = False
            script, gesture = scripts.pop(); script(gesture)
            self.assertEqual(self.sent, [key])
            self.assertEqual(base.delays, [])
        finally: controller.terminate()
        self.assertEqual(inputCore.decide_executeGesture.handlers, [])
    def test_editing_input_help_capture_sleep_and_other_modifiers_are_ignored(self):
        controller = announcements.ClipboardAnnouncements(lambda: True)
        try:
            manager.isInputHelpActive = True
            self.assertTrue(controller._onGesture(Key('control+c')))
            manager.isInputHelpActive = False; manager._captureFunc = object()
            self.assertTrue(controller._onGesture(Key('control+c')))
            manager._captureFunc = None; self.focus.sleepMode = True
            self.assertTrue(controller._onGesture(Key('control+c')))
            self.focus.sleepMode = False
            self.assertTrue(controller._onGesture(Key('control+shift+c')))
            self.assertEqual(scripts, [])
        finally: controller.terminate()
    def test_all_editing_outcomes_and_shared_unknown_selection_message(self):
        controller = announcements.ClipboardAnnouncements(lambda: True)
        try:
            for operation in ('cut','copy','paste'):
                for available in (True,False,None): controller._announce(operation, available)
            self.assertEqual(len(base.messages), 9)
            self.assertEqual(len(set(base.messages)), 8)
            self.assertEqual(base.messages[2], base.messages[5])
        finally: controller.terminate()

class HistoryTests(unittest.TestCase):
    def test_limit_validation_resize_order_clear_and_nontext_commands(self):
        for value in ('0', '1001', '-1', '1.2', 'abc', '١'):
            with self.assertRaises(ValueError): buffer.parseLimit(value)
        self.assertEqual(buffer.parseLimit(' 1000 '), 1000)
        records = buffer.HistoryBuffer(2)
        records.append(['old']); records.append(['second', object(), '\n row']); records.append(['new'])
        self.assertEqual(records.snapshot(), ['new','second row'])
        self.assertEqual(records.snapshot(False), ['second row','new'])
        records.resize(1); self.assertEqual(records.snapshot(), ['new'])
        records.clear(); self.assertFalse(records.snapshot())
    def test_disabled_protected_viewed_and_terminated_history_does_not_record(self):
        config = dict(historyLimit=2, recordHistory=True, historyOrder='newestFirst')
        controller = history.SpeechHistory(lambda: config)
        base.api.isTypingProtected = lambda: False
        controller._onSpeech(['one']); self.assertEqual(controller.entries(), ['one'])
        controller.viewActivated(True); controller._onSpeech(['viewer'])
        controller.viewActivated(False); base.api.isTypingProtected = lambda: True
        controller._onSpeech(['secret']); self.assertEqual(controller.entries(), ['one'])
        config['recordHistory'] = False; controller.applySettings()
        self.assertFalse(controller.entries())
        controller.terminate(); self.assertEqual(preSpeech.handlers, [])

class ApplicationsTests(unittest.TestCase):
    def setUp(self):
        self.conf = dict(replaceApplicationsKey=True, applicationsGesture='kb:control+f9')
        self.sent = []; manager.emulateGesture = self.sent.append; base.delays.clear(); base.messages.clear()
    def test_capture_modifier_escape_and_layout_identifiers(self):
        key = Key('control+f9')
        key.normalizedIdentifiers = ['kb(desktop):control+f9','kb:control+f9']
        self.assertEqual(applications.capturedIdentifier(key), 'kb:control+f9')
        key.isModifier = True; self.assertIsNone(applications.capturedIdentifier(key))
        self.assertEqual(applications.capturedIdentifier(Key('escape')), 'kb:escape')
    def test_only_enabled_assigned_key_matches_and_native_applications_is_not_recaptured(self):
        controller = applications.ApplicationsKey(lambda: self.conf)
        self.assertTrue(controller.matches(Key('control+f9')))
        self.assertFalse(controller.matches(Key('applications')))
        self.conf['replaceApplicationsKey'] = False
        self.assertFalse(controller.matches(Key('control+f9')))
    def test_modifier_release_foreground_change_and_termination(self):
        controller = applications.ApplicationsKey(lambda: self.conf)
        key = Key('control+f9'); key.modifiers = [(17,False)]
        with patch.object(win, 'getKeyState', return_value=0x8000): controller.execute(key)
        self.assertFalse(self.sent)
        func, args = base.delays.pop(); func(*args)
        self.assertEqual(self.sent[-1].name, 'applications')
        self.sent.clear()
        with patch.object(win, 'getForegroundWindow', return_value=8):
            controller._sendWhenReleased(controller._generation, (), 7, 0)
        self.assertFalse(self.sent)
        with patch.object(win, 'getKeyState', return_value=0x8000):
            controller._sendWhenReleased(controller._generation, (17,), 7, 100)
        self.assertIn('Release the shortcut keys', base.messages[-1])
        controller.terminate(); controller._sendWhenReleased(controller._generation, (), 7, 0)
        self.assertFalse(self.sent)

class WebDocument(Browse):
    __module__ = 'virtualBuffers.gecko_ia2'
    passThrough = False
    def __init__(self, nodes, pos=100): self.nodes, self.pos, self.rootNVDAObject = nodes, pos, self
    def _iterNodesByType(self, kind, direction='next', pos=None):
        values = self.nodes if pos is None else [x for x in self.nodes if (x > pos if direction == 'next' else x < pos)]
        return iter(values if direction == 'next' else reversed(values))
    def _iterNotLinkBlock(self, direction='next', pos=None): return self._iterNodesByType(None, direction, pos)
    def _iterSimilarParagraph(self, *args): return iter(())

class WebTests(unittest.TestCase):
    def setUp(self): base.messages.clear()
    def test_forward_reverse_and_no_matches_preserve_iterators(self):
        controller = web.WebCycle(lambda: True)
        try:
            for direction, pos, nodes, expected in (('next',100,[1,2,3],1),
                                                    ('previous',0,[1,2,3],3), ('next',100,[],None),
                                                    ('next',1,[1,2,3],2), ('next',100,[1],1)):
                doc = WebDocument(nodes,pos)
                self.assertEqual(doc._quickNavScript(None,'heading',direction,'none','line'), expected)
                self.assertNotIn('_iterNodesByType',doc.__dict__)
            self.assertEqual(len(base.messages), 3)
        finally: controller.terminate()
    def test_disabled_focus_mode_and_native_document_do_not_wrap(self):
        flag = [False]; controller = web.WebCycle(lambda: flag[0])
        try:
            doc = WebDocument([1,2],100)
            self.assertIsNone(doc._quickNavScript(None,'heading','next','none','line'))
            class Native:
                rootNVDAObject = object()
            self.assertFalse(web.isWebDocument(Native()))
            flag[0] = True; doc.passThrough = True
            self.assertIsNone(doc._quickNavScript(None,'heading','next','none','line'))
            self.assertEqual(base.messages, [])
        finally: controller.terminate()
    def test_iterators_restore_after_exception(self):
        doc = WebDocument([1,2])
        with self.assertRaises(RuntimeError):
            with web.cyclingIterators(doc, lambda: None): raise RuntimeError('provider failure')
        self.assertNotIn('_iterNodesByType', doc.__dict__)
        self.assertNotIn('_iterNotLinkBlock', doc.__dict__)
        self.assertNotIn('_iterSimilarParagraph', doc.__dict__)

    def test_paragraph_wrap_includes_boundary_at_offset_zero(self):
        class Info:
            isCollapsed = False
            def __init__(self, index): self.index = index
            def copy(self): return Info(self.index)
            def collapse(self): pass
            def expand(self, unit): pass
        for direction, expected in (('next', 0), ('previous',20)):
            doc = WebDocument([])
            doc.makeTextInfo = lambda pos: Info(0 if pos == 'first' else 20)
            wraps = []
            with web.cyclingIterators(doc, lambda: wraps.append(True)):
                result = list(doc._iterSimilarParagraph('paragraph', lambda info: 'same', None, direction, Info(10)))
            self.assertEqual(result, [expected])
            self.assertEqual(wraps, [True])

if __name__ == '__main__': unittest.main(verbosity=2)
