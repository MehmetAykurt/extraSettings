"""Repeated anchors, provider differences, persistence and cancellation."""
import json
from pathlib import Path
import queue
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
import check_bookmarks as base

def load(name):
    module = base.load(name)
    sys.modules['bookmarkTestPackage.' + name] = module
    return module

anchors = load('quickFindAnchor')
storeModule = load('quickFindStore')
ti = types.SimpleNamespace(POSITION_FIRST='first', POSITION_LAST='last', POSITION_ALL='all', POSITION_CARET='caret',
                          UNIT_LINE='line', UNIT_CHARACTER='character')
api = types.SimpleNamespace(isTypingProtected=lambda:False)
with patch.dict(sys.modules, api=api, textInfos=ti):
    text = load('quickFindText')
sys.modules['bookmarkTestPackage.quickFindText'] = text
callbacks = queue.Queue()
messages = []
inputCore = types.SimpleNamespace(normalizeGestureIdentifier=lambda key:key.split(':',1)[0].lower()+':'+ '+'.join(sorted(key.split(':',1)[1].lower().split('+'))),
    manager=types.SimpleNamespace(getAllGestureMappings=lambda:{}))
globalVars = types.SimpleNamespace(appArgs=types.SimpleNamespace(configPath=''))
core = types.SimpleNamespace(callLater=lambda delay,func,*args:callbacks.put((func,args)))
ui = types.SimpleNamespace(message=messages.append)
scriptHandler = types.SimpleNamespace(findScript=lambda gesture:None)
wx = types.SimpleNamespace(CallAfter=lambda func,*args:callbacks.put((func,args)))
with patch.dict(sys.modules, addonHandler=types.SimpleNamespace(initTranslation=lambda:None), api=api,
                textInfos=ti, core=core, globalVars=globalVars, gui=base.gui, inputCore=inputCore, scriptHandler=scriptHandler, ui=ui, wx=wx,
                logHandler=types.SimpleNamespace(log=types.SimpleNamespace(debug=lambda *args:None))):
    controllerModule = load('quickFind')
sys.modules['bookmarkTestPackage.quickFind'] = controllerModule

class Info:
    def __init__(self, doc, start, end=None):
        self.doc, self.start, self.end = doc, start, start if end is None else end
    @property
    def text(self): return self.doc.source[self.start:self.end]
    @property
    def isCollapsed(self): return self.start == self.end
    @property
    def NVDAObjectAtStart(self): return self.doc
    def copy(self): return Info(self.doc,self.start,self.end)
    def collapse(self,end=False):
        self.start = self.end if end else self.start
        self.end = self.start
    def expand(self,unit):
        assert unit == 'line'
        self.start = self.doc.source.rfind('\n',0,self.start)+1
        end = self.doc.source.find('\n',self.start)
        self.end = len(self.doc.source) if end < 0 else end+1
    def move(self,unit,direction,endPoint=None):
        assert unit == 'character'
        old = self.end if endPoint == 'end' else self.start
        new = max(0,min(len(self.doc.source),old+direction))
        if endPoint == 'end': self.end = new
        elif endPoint == 'start': self.start = new
        else: self.start = self.end = new
        return new-old
    def setEndPoint(self,other,which):
        if which == 'endToStart': self.end = other.start
        else: raise AssertionError(which)
    def compareEndPoints(self,other,which):
        left = self.start if which.startswith('start') else self.end
        right = other.start if which.endswith('Start') else other.end
        return (left > right)-(left < right)
    def find(self,term,caseSensitive=False):
        start = self.start + (0 if self.doc.includesStart else 1)
        source, term = (self.doc.source,term) if caseSensitive else (self.doc.source.lower(),term.lower())
        found = source.find(term,start)
        if found < 0: return False
        self.start = found
        self.end = found + len(term) if self.doc.includesStart else found
        return True
    def updateCaret(self): self.doc.caret = self.start; self.doc.movements += 1

class Document:
    isProtected = False
    def __init__(self,source,app='notepad',url=None,includesStart=False):
        self.source, self.caret = source,0
        self.movements = 0
        self.includesStart = includesStart
        self.windowHandle = 321
        self.name = 'Deneme.txt - Not Defteri'
        self.appModule = types.SimpleNamespace(appName=app)
        self.documentConstantIdentifier = url
    def makeTextInfo(self,position):
        if position == 'all': return Info(self,0,len(self.source))
        if position == 'first': return Info(self,0)
        if position == 'last': return Info(self,len(self.source))
        if position == 'caret': return Info(self,self.caret)
        raise AssertionError(position)
    def setFocus(self):
        api.getCaretObject = api.getFocusObject = api.getForegroundObject = lambda:self

class AnchorTests(unittest.TestCase):
    def test_same_text_unchanged_second_occurrence_is_preserved(self):
        source = 'Başlık\nMenü\nBirinci bölüm\nAra\nMenü\nSon bölüm'
        offset = source.rfind('Menü')
        entry = anchors.capture(source,'Menü',offset,'web:test','Deneme')
        self.assertEqual(entry['ordinal'],1)
        self.assertEqual(entry['lineNumber'],5)
        self.assertEqual(anchors.resolve(source,entry),[1])
        self.assertEqual(anchors.resolve('Yeni içerik\n'+source,entry),[1])

    def test_ambiguous_identical_context_is_offered_without_guessing_by_offset(self):
        block = 'X'*150+'\nMenü\n'+'Y'*150
        source = block+'\n'+block
        entry = anchors.capture(source,'Menü',source.rfind('Menü'),'web:test','Test')
        self.assertEqual(anchors.resolve('Başlık\n'+source,entry),[0,1])
        self.assertEqual(anchors.resolve(source.replace('Menü','Başka'),entry),[])

    def test_deleted_repeated_target_does_not_jump_to_the_remaining_unrelated_menu(self):
        source='Üst bölüm\nMenü\nÜst açıklama\nAlt bölüm\nMenü\nAlt açıklama'
        saved=anchors.capture(source,'Menü',source.rfind('Menü'),'web:test','Test')
        changed=source[:source.rfind('Menü')]+source[source.rfind('Menü'):].replace('Menü','Silindi',1)
        self.assertEqual(anchors.resolve(changed,saved),[])

    def test_whitespace_emoji_case_and_overlapping_matches(self):
        source = '😀 Başlık\n   Menü\nSon'
        entry = anchors.capture(source,'  Menü\n',source.index('   Menü'),'x','Test')
        self.assertEqual(entry['offset'],source.index('Menü'))
        self.assertEqual(anchors.occurrences('aaaa','aa'),[0,1,2])
        self.assertEqual(anchors.resolve(source.replace('Menü','MENÜ'),entry),[0])

    def test_bounded_documents_lines_and_match_counts(self):
        for source,term in [('X'*1_000_001,'X'),('X'*1001,'X'*1001),('a\n'*1001,'a')]:
            with self.assertRaises(ValueError): anchors.capture(source,term,0,'x','Test')
        with self.assertRaisesRegex(ValueError,'empty'): anchors.capture(' \n',' ',0,'x','Test')

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='quickFind-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'extraSettingsQuickFind.json'
        self.store = storeModule.QuickFindStore(self.path); self.store.load()
        self.entry = anchors.capture('A\nMenü\nB','Menü',2,'web:test','Test')

    def test_crud_shortcuts_persist_and_backup_preserves_last_valid_data(self):
        first = self.store.saveAnchor(self.entry)
        saved = self.path.read_bytes()
        changed = dict(self.store.entries[0],name='Alt menü',shortcut='kb:alt+ö')
        self.store.saveAnchor(changed,first)
        reopened = storeModule.QuickFindStore(self.path); reopened.load()
        self.assertEqual(reopened.entries[0]['shortcut'],'kb:alt+ö')
        self.assertEqual(self.path.with_suffix('.json.bak').read_bytes(),saved)
        self.store.remove(first)
        self.assertEqual(storeModule.QuickFindStore(self.path).load(),[])

    def test_duplicate_locations_and_keys_are_rejected_only_within_same_document(self):
        self.entry['shortcut'] = 'kb:nvda+alt+1'
        self.store.saveAnchor(self.entry)
        with self.assertRaisesRegex(base.Error,'duplicate'): self.store.saveAnchor(dict(self.entry,shortcut=''))
        other = anchors.capture('A\nBaşka\nB','Başka',2,'web:test','Test')
        with self.assertRaisesRegex(base.Error,'shortcut'): self.store.saveAnchor(dict(other,shortcut=self.entry['shortcut']))
        self.store.saveAnchor(dict(other,docKey='web:other',shortcut=self.entry['shortcut']))
        self.assertEqual(len(self.store.entries),2)

    def test_invalid_or_damaged_records_never_overwrite_existing_data(self):
        for shortcut in ('kb:a','kb:shift+a','kb:control+c','kb:nvda+tab','kb:alt+escape','not-a-key'):
            with self.assertRaisesRegex(base.Error,'invalid'): self.store.saveAnchor(dict(self.entry,shortcut=shortcut))
        self.path.write_bytes(b'{"version":1,"entries":[{}]}')
        with self.assertRaisesRegex(base.Error,'damaged'): self.store.load()
        with self.assertRaisesRegex(base.Error,'changed'): self.store.saveAnchor(self.entry)
        self.assertEqual(self.path.read_bytes(),b'{"version":1,"entries":[{}]}')

    def test_nvda_normalized_key_order_is_valid_and_editing_shortcuts_remain_rejected(self):
        for key in ('kb:1+alt+nvda','kb:h+nvda+shift','kb:alt+f2','kb:alt+ö','kb:control+k+shift'):
            self.assertTrue(storeModule.validShortcut(key),key)
        for key in ('kb:c+control','kb:nvda+tab','kb:a+nvda+nvda','kb:1+2+nvda'):
            self.assertFalse(storeModule.validShortcut(key),key)

class TextTests(unittest.TestCase):
    def setUp(self):
        api.isTypingProtected = lambda:False
    def test_document_identity_preserves_web_paths_queries_and_fragment(self):
        doc = Document('A\nMenü\nB',app='firefox',url='https://EXAMPLE.org/a?q=1#b'); doc.setFocus()
        self.assertEqual(text.currentContext().key,'web:https://example.org/a?q=1#b')
        doc.documentConstantIdentifier = 'https://example.org/a?q=2#b'
        self.assertNotEqual(text.currentContext().key,'web:https://example.org/a?q=1#b')

    def test_txt_fallback_uses_title_and_both_samples_and_word_uses_real_path(self):
        doc = Document('A\nMenü\nB'); doc.setFocus()
        first = text.currentContext()
        self.assertTrue(first.sampled)
        doc.source = 'Other\nMenü\nB'
        self.assertNotEqual(text.currentContext().key,first.key)
        doc.appModule.appName = 'winword'
        doc.WinwordDocumentObject = types.SimpleNamespace(Path='C:\\Test',FullName='C:\\Test\\Belge.docx')
        self.assertEqual(text.currentContext().key,'file:c:\\test\\belge.docx')

    def test_protected_or_unsupported_apps_do_not_read_document(self):
        doc = Document('Secret',app='unknown'); doc.setFocus()
        with self.assertRaisesRegex(text.QuickFindError,'unsupported'): text.currentContext()
        api.isTypingProtected = lambda:True
        with self.assertRaisesRegex(text.QuickFindError,'protected'): text.currentContext()

    def test_native_search_browse_and_word_include_start_and_adjacent_matches(self):
        for inclusive in (False,True):
            for source,term,index,expected in [('Menü\nA\nMenü','Menü',1,7),('aa\n','a',1,1),('😀 Menü\n😀 Menü','😀 Menü',1,7)]:
                with self.subTest(inclusive=inclusive,source=source):
                    doc = Document(source,includesStart=inclusive); doc.setFocus()
                    context = text.currentContext()
                    iterator = text.findOccurrence(context,term,index)
                    while True:
                        try: next(iterator)
                        except StopIteration as found: info = found.value; break
                    self.assertEqual(info.start,expected)
                    self.assertEqual(doc.movements,0)

class ControllerTests(unittest.TestCase):
    def setUp(self):
        StoreTests.setUp(self)
        messages.clear()
        while not callbacks.empty(): callbacks.get_nowait()
        api.isTypingProtected = lambda:False
        self.doc = Document('Başlık\nMenü\nBirinci\nMenü\nSon',app='firefox',url='https://example.org/')
        self.doc.setFocus()
        self.enabled = [True]
        globalVars.appArgs.configPath = self.temp.name
        self.controller = controllerModule.QuickFind(lambda:self.enabled[0])
        self.addCleanup(self.controller.terminate)

    def pump(self):
        deadline = time.monotonic()+2
        while self.controller._busy and time.monotonic()<deadline:
            try:
                func,args = callbacks.get(timeout=.05); func(*args)
            except queue.Empty: pass
        self.assertFalse(self.controller._busy)

    def record(self):
        context = text.currentContext()
        self.doc.caret = self.doc.source.rfind('Menü')
        return text.captureLine(context,self.doc.source)

    def test_second_menu_navigation_and_nonmoving_caret_changes_cancel(self):
        entry = self.record()
        self.doc.caret = 0
        with patch.dict(sys.modules,review=types.SimpleNamespace(handleCaretMove=lambda info:None),
                        braille=types.SimpleNamespace(handler=types.SimpleNamespace(handleCaretMove=lambda doc:None))):
            self.controller.jump(entry); self.pump()
        self.assertEqual(self.doc.caret,self.doc.source.rfind('Menü'))
        self.assertEqual(messages[-1],'Menü')
        self.doc.caret = 0
        self.controller.jump(entry)
        self.doc.caret = 1
        self.pump()
        self.assertEqual(self.doc.caret,1)

    def test_disable_termination_and_other_document_ignore_pending_navigation(self):
        entry = self.record()
        for change in ('disable','terminate','document'):
            self.setUp()
            entry = self.record(); self.doc.caret=0
            self.controller.jump(entry)
            if change == 'disable': self.enabled[0]=False; self.controller.applySettings()
            elif change == 'terminate': self.controller.terminate()
            else: self.doc.documentConstantIdentifier='https://other.org/'
            time.sleep(.02)
            while not callbacks.empty():
                func,args=callbacks.get_nowait(); func(*args)
            self.assertEqual(self.doc.movements,0)

    def test_shortcut_only_resolves_in_correct_document_and_not_when_disabled(self):
        entry = dict(self.record(),shortcut='kb:nvda+alt+1')
        self.controller._store.saveAnchor(entry); self.controller.refreshBindings()
        gesture = types.SimpleNamespace(normalizedIdentifiers=[inputCore.normalizeGestureIdentifier('kb:nvda+alt+1')])
        with patch.object(controllerModule,'currentContext',side_effect=AssertionError('Document accessed during input lookup')):
            action=self.controller.getScript(gesture)
        self.assertTrue(callable(action))
        with patch.object(self.controller,'jump') as jump:
            action(gesture)
            jump.assert_called_once()
        def forward(source):
            self.assertIsNone(self.controller.getScript(source),'Forwarding re-entered our shortcut')
        with patch.object(inputCore.manager,'emulateGesture',side_effect=forward,create=True) as emulate:
            self.doc.documentConstantIdentifier='https://other.org/'
            action(gesture)
            emulate.assert_called_once_with(gesture)
            self.doc.documentConstantIdentifier='https://example.org/'
            api.isTypingProtected=lambda:True
            action(gesture)
            self.assertEqual(emulate.call_count,2)
        self.assertFalse(self.controller._emulating)
        self.enabled[0]=False
        self.assertIsNone(self.controller.getScript(gesture))

    def test_other_page_replaces_cached_script_before_async_forwarding(self):
        entry = dict(self.record(), shortcut='kb:alt+s')
        self.controller._store.saveAnchor(entry)
        self.controller.refreshBindings()
        for destination in ('native', 'nvda', 'unsupported'):
            with self.subTest(destination=destination):
                gesture = types.SimpleNamespace(normalizedIdentifiers=['kb:alt+s'])
                gesture.script = self.controller.getScript(gesture)
                original = gesture.script
                queued, sent, executed = [], [], []
                def normal(source):
                    executed.append(source)
                def resolve(source):
                    # Model NVDA's fresh lookup, including the global plugin.
                    return self.controller.getScript(source) or (normal if destination == 'nvda' else None)
                def emulate(source):
                    # NVDA reads the already cached script, then runs it later,
                    # after the controller's synchronous guard has been reset.
                    if source.script is not None:
                        queued.append((source.script, source))
                    else:
                        sent.append(source)
                self.doc.documentConstantIdentifier = 'https://other.org/'
                context = patch.object(controllerModule, 'currentContext',
                    side_effect=controllerModule.QuickFindError('unsupported')) if destination == 'unsupported' else patch.object(
                    controllerModule, 'currentContext', wraps=text.currentContext)
                with context, patch.object(scriptHandler, 'findScript', side_effect=resolve), patch.object(
                        inputCore.manager, 'emulateGesture', side_effect=emulate, create=True), patch.object(self.controller, 'jump') as jump:
                    original(gesture)
                    self.assertFalse(self.controller._emulating)
                    self.assertIsNot(gesture.script, original)
                    # A cached Quick find closure would enqueue itself forever.
                    for _ in range(3):
                        if not queued:
                            break
                        action, source = queued.pop(0)
                        action(source)
                    self.assertEqual(queued, [])
                    jump.assert_not_called()
                self.assertEqual(len(executed), int(destination == 'nvda'))
                self.assertEqual(len(sent), int(destination != 'nvda'))

    def test_disabled_show_warns_without_snapshot_or_focus_access(self):
        self.enabled[0]=False
        with patch.object(controllerModule,'currentContext',side_effect=AssertionError('Read while disabled')):
            self.controller.show()
        self.assertEqual(messages[-1],'Enable Quick find in Extra Settings first.')

    def test_registered_nvda_shortcuts_are_rejected(self):
        info=types.SimpleNamespace(gestures=['kb:nvda+shift+h'])
        with patch.object(inputCore.manager,'getAllGestureMappings',return_value={'category':{'script':info}}):
            self.assertTrue(controllerModule.shortcutConflict('kb:NVDA+Shift+H'))


with patch.dict(sys.modules, addonHandler=types.SimpleNamespace(initTranslation=lambda:None), gui=types.SimpleNamespace(
        mainFrame=base.gui.mainFrame, guiHelper=types.SimpleNamespace(BoxSizerHelper=base.Helper)),
        inputCore=inputCore, ui=ui, wx=base.wx,
        **{'bookmarkTestPackage.applicationsKey':types.SimpleNamespace(shortcutLabel=lambda key:key or 'No shortcut assigned')}):
    dialogModule = load('quickFindDialog')
sys.modules['bookmarkTestPackage.quickFindDialog'] = dialogModule


class DialogTests(unittest.TestCase):
    def setUp(self):
        StoreTests.setUp(self)
        api.isTypingProtected=lambda:False
        messages.clear()
        self.doc=Document('Başlık\nMenü\nSon',app='firefox',url='https://example.org/')
        self.doc.setFocus()
        self.doc.caret=7
        self.context=text.currentContext()
        self.entry=text.captureLine(self.context,self.doc.source)
        self.bindingUpdates=0
        self.closed=0
        self.navigation=[]
        self.controller=types.SimpleNamespace(_store=self.store,
            refreshBindings=lambda:setattr(self,'bindingUpdates',self.bindingUpdates+1),
            viewClosed=lambda:setattr(self,'closed',self.closed+1),
            fromDialog=lambda entry,context:self.navigation.append((entry,context)))

    def test_editor_cancel_does_not_save_and_name_and_optional_shortcut_commit_together(self):
        editor=dialogModule.AnchorEditor(None,self.controller,self.entry)
        editor.name.SetValue('Alt menü')
        self.assertFalse(self.path.exists())
        with patch.object(dialogModule,'shortcutConflict',return_value=False):
            editor.shortcut='kb:nvda+alt+1'
            editor.onSave(None)
        self.assertEqual(self.store.entries[0]['name'],'Alt menü')
        self.assertEqual(self.store.entries[0]['shortcut'],'kb:nvda+alt+1')
        self.assertEqual(self.bindingUpdates,1)

    def test_capture_displays_normalized_shortcut_and_remove_button_follows_assignment(self):
        editor=dialogModule.AnchorEditor(None,self.controller,self.entry)
        self.assertFalse(editor.clearButton.visible)
        self.assertEqual(editor.shortcutValue.GetValue(),'No shortcut assigned')
        class Capture:
            identifier='kb:1+alt+nvda'
            def __init__(self,*args,**kwargs): pass
            def beginCapture(self): return True
            def ShowModal(self): return base.wx.ID_OK
            def stopCapture(self): pass
            def Destroy(self): pass
        with patch.dict(sys.modules,**{'bookmarkTestPackage.keyCapture':types.SimpleNamespace(KeyCaptureDialog=Capture)}), \
                patch.object(dialogModule,'shortcutConflict',return_value=False):
            editor.onKey(None)
        self.assertEqual(editor.shortcut,'kb:1+alt+nvda')
        self.assertEqual(editor.shortcutValue.GetValue(),'kb:1+alt+nvda')
        self.assertTrue(editor.clearButton.visible)
        self.assertEqual(editor.keyButton.label,'Change shortcut')
        editor.onClear(None)
        self.assertEqual(editor.shortcut,'')
        self.assertEqual(editor.shortcutValue.GetValue(),'No shortcut assigned')
        self.assertFalse(editor.clearButton.visible)
        self.assertEqual(editor.keyButton.label,'Assign shortcut')
        self.assertFalse(self.path.exists(),'Clearing an unsaved edit wrote to disk')

    def test_reopening_saved_record_keeps_readonly_shortcut_in_keyboard_tab_order(self):
        identity=self.store.saveAnchor(dict(self.entry,shortcut='kb:alt+h+nvda'))
        reopened=storeModule.QuickFindStore(self.path)
        saved=reopened.load()[0]
        self.controller._store=reopened
        editor=dialogModule.AnchorEditor(None,self.controller,saved,editing=True)
        self.assertEqual(editor.shortcutValue.GetValue(),'kb:alt+h+nvda')
        self.assertTrue(editor.shortcutValue.AcceptsFocusFromKeyboard())
        self.assertEqual(editor.shortcutValue.style,base.wx.TE_READONLY)
        self.assertFalse(editor.shortcutValue.focused)
        self.assertTrue(editor.name.focused)
        self.assertEqual(editor.keyButton.label,'Change shortcut')
        self.assertTrue(editor.clearButton.visible)

    def test_list_filters_other_documents_removes_record_and_routes_enter(self):
        self.store.saveAnchor(self.entry)
        self.store.saveAnchor(dict(self.entry,docKey='web:other'))
        dialog=dialogModule.QuickFindDialog(None,self.controller,self.context,self.entry,None)
        self.assertEqual(len(dialog.visible),1)
        dialog.onGo(None)
        self.assertEqual(self.navigation[0][0]['docKey'],self.context.key)
        dialog.onRemove(None)
        self.assertEqual(len(self.store.entries),1)
        self.assertEqual(self.store.entries[0]['docKey'],'web:other')
        dialog.onClose(None); dialog._notifyClosed()
        self.assertEqual(self.closed,1)

if __name__ == '__main__': unittest.main(verbosity=2)
