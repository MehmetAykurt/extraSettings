"""Web-only caret movement, bounded work, controls and translation integration."""
import threading
import time
import types
import unittest
from unittest.mock import patch
import check_translation as base

class Action:
    def __init__(self): self.handlers = []
    def register(self, handler): self.handlers.append(handler)
    def unregister(self, handler): self.handlers.remove(handler)
    def notify(self):
        for handler in tuple(self.handlers): handler()

names = ('BUTTON CHECKBOX RADIOBUTTON EDITABLETEXT COMBOBOX LISTBOX SLIDER SPINBUTTON GRAPHIC LINK '
         'SEPARATOR PROGRESSBAR MATH TABLE TABLECELL TABLEROW COLUMNHEADER ROWHEADER DOCUMENT PARAGRAPH '
         'SECTION REGION ARTICLE FRAME INTERNALFRAME GROUPING TEXTFRAME STATICTEXT UNKNOWN').split()
role = types.SimpleNamespace(**{name: name for name in names})
state = types.SimpleNamespace(FOCUSABLE='focusable')
base.stub('controlTypes', OutputReason=types.SimpleNamespace(CARET='caret', QUERY='query', ONLYCACHE='cache'), Role=role, State=state)
events = Action()
base.stub('inputCore', decide_executeGesture=events)
waiting = [False]; sayAll = [False]
base.stub('scriptHandler', isScriptWaiting=lambda: waiting[0], willSayAllResume=lambda gesture: sayAll[0])
base.textInfos = base.sys.modules['textInfos']
base.textInfos.POSITION_SELECTION = 'selection'
base.textInfos.DocumentWithPageTurns = type('PageTurns', (), {})
base.stub('browseMode', BrowseModeTreeInterceptor=type('Browse', (), {}))
web = base.load('translationTestPackage.webCycle', 'webCycle.py')
spoken = []
def speakTextInfo(info, **kwargs):
    spoken.append((info.index, info.text, kwargs))
    if info.document.failSpeech: raise RuntimeError('speech failure')
speech = base.stub('speech', speakTextInfo=speakTextInfo)

class Cursor:
    def _caretMovementScriptHelper(self, gesture, unit, direction=None, posConstant='selection',
                                    posUnit=None, posUnitEnd=False, extraDetail=False, handleSymbols=False):
        self.originalCalls += 1
        if self.failOriginal: raise RuntimeError('original failure')
        self.caret = max(0, min(len(self.lines)-1, self.caret + (direction or 0)))
        self.commits.append(self.caret)
        return 'original'
base.stub('cursorManager', CursorManager=Cursor)
skip = base.load('translationTestPackage.skipBlankLines', 'skipBlankLines.py')

class Info:
    def __init__(self, document, index, collapsed=True):
        self.document = self.obj = document
        self.index, self.isCollapsed = index, collapsed
        self.NVDAObjectAtStart = document.rootNVDAObject
    def copy(self): return Info(self.document, self.index, self.isCollapsed)
    def expand(self, unit): assert unit == 'line'; self.isCollapsed = False
    def collapse(self, end=False): self.isCollapsed = True
    def compareEndPoints(self, other, endpoint):
        if self.document.failCompare: raise NotImplementedError
        return self.index - other.index
    def move(self, unit, direction):
        assert threading.get_ident() == base.MAIN_THREAD
        self.document.moves += 1
        if self.document.raiseMove: raise RuntimeError('provider failed')
        if self.document.lieMove: return direction
        new = self.index + direction
        if not 0 <= new < len(self.document.lines): return 0
        self.index = new; return direction
    @property
    def text(self):
        assert threading.get_ident() == base.MAIN_THREAD
        self.document.reads += 1
        return self.document.lines[self.index]
    def getTextWithFields(self):
        self.document.fieldReads += 1
        if self.document.failFields: raise RuntimeError('fields unavailable')
        return self.document.fields.get(self.index, [self.document.lines[self.index]])

class Document(Cursor):
    __module__ = 'virtualBuffers.gecko_ia2'
    def __init__(self, lines, caret=0):
        self.lines, self.caret = lines, caret
        self.passThrough = self.selected = False
        self.rootNVDAObject = types.SimpleNamespace(isProtected=False)
        self.moves = self.reads = self.fieldReads = self.originalCalls = 0
        self.failOriginal = self.failCompare = self.raiseMove = self.lieMove = self.failFields = self.failSpeech = False
        self.commits = []; self.fields = {}
    def makeTextInfo(self, position): return Info(self, self.caret, not self.selected)
    @property
    def selection(self): return self.makeTextInfo('selection')
    @selection.setter
    def selection(self, info): self.caret = info.index; self.commits.append(info.index)

def field(name, **extra): return types.SimpleNamespace(command='controlStart', field=dict(role=getattr(role,name), **extra))
def end(): return types.SimpleNamespace(command='controlEnd', field={})

class SkipTests(unittest.TestCase):
    def setUp(self):
        self.enabled = True
        base.delays.clear(); base.messages.clear(); spoken.clear()
        waiting[0] = sayAll[0] = False
        self.focus = object()
        base.api.getFocusObject = lambda: self.focus
        base.api.isTypingProtected = lambda: False
        base.api.getCaretObject = lambda: None
        self.controller = skip.SkipBlankLines(lambda: self.enabled)
    def tearDown(self):
        self.controller.terminate()
        self.assertEqual(events.handlers, [])
    def move(self, document, direction=1, **kwargs):
        gesture = types.SimpleNamespace(mainKeyName='downArrow' if direction == 1 else 'upArrow', modifierNames=())
        return document._caretMovementScriptHelper(gesture, 'line', direction, **kwargs)
    def drain(self):
        count = 0
        while base.delays:
            func, args = base.delays.pop(0); func(*args); count += 1
            self.assertLess(count, 10000)
    def test_normal_line_is_read_once_without_blank_scan_or_field_lookup(self):
        doc = Document(['one','two','three'])
        self.move(doc)
        self.assertEqual(doc.caret, 1)
        self.assertEqual(doc.moves, 1)
        self.assertEqual(doc.fieldReads, 0)
        self.assertEqual(len(spoken), 1)
        self.assertEqual(doc.commits, [1])
        self.assertEqual(base.delays, [])
    def test_whitespace_runs_skip_both_directions_with_one_real_caret_commit(self):
        doc = Document(['one','',' ','\t','\u00a0','two'])
        self.move(doc)
        self.assertEqual(doc.caret, 5)
        self.assertEqual(doc.commits, [5])
        self.assertEqual([row[1] for row in spoken], ['two'])
        self.move(doc,-1)
        self.assertEqual(doc.caret, 0)
        self.assertEqual(doc.commits, [5,0])
    def test_long_runs_yield_in_bounded_slices_without_stopping_at_blank(self):
        doc = Document(['start']+['']*10000+['finish'])
        self.move(doc)
        self.assertIsNotNone(self.controller._pending)
        self.assertEqual(doc.caret, 0)
        self.assertLessEqual(doc.moves, skip.SLICE_STEPS)
        while base.delays:
            previous = doc.moves
            func, args = base.delays.pop(0); func(*args)
            self.assertLessEqual(doc.moves-previous, skip.SLICE_STEPS)
        self.assertEqual(doc.caret, 10001)
        self.assertEqual(doc.commits, [10001])
        self.assertEqual([row[1] for row in spoken], ['finish'])
    def test_time_budget_yields_before_step_limit(self):
        doc = Document(['start']+['']*20+['finish'])
        times = iter([0,.009])
        self.controller._clock = lambda: next(times)
        self.move(doc)
        self.assertEqual(doc.moves, 1)
        self.assertEqual(doc.caret, 0)
        self.controller._clock = time.perf_counter
        self.drain(); self.assertEqual(doc.caret,21)
    def test_new_input_focus_change_caret_change_disable_and_terminate_cancel_pending(self):
        for action in ('input','focus','caret','disable','terminate'):
            with self.subTest(action=action):
                if action == 'terminate': pass
                self.enabled = True
                doc = Document(['start']+['']*300+['finish'])
                self.move(doc)
                self.assertTrue(base.delays)
                if action == 'input': self.assertTrue(self.controller._onGesture(object()))
                elif action == 'focus': self.focus = object()
                elif action == 'caret': doc.caret = 1
                elif action == 'disable': self.enabled = False; self.controller.applySettings()
                else: self.controller.terminate()
                self.drain()
                self.assertNotEqual(doc.caret,301)
                self.assertEqual(doc.commits, [])
                if action == 'terminate':
                    # Avoid unregistering twice in tearDown.
                    self.controller = skip.SkipBlankLines(lambda: False)
    def test_blank_tail_and_head_do_not_move_caret_to_blank_or_loop(self):
        for lines, caret, direction in ((['one','',''],0,1), (['','','one'],2,-1)):
            doc = Document(lines,caret)
            self.move(doc,direction); self.drain()
            self.assertEqual(doc.caret,caret)
            self.assertEqual(doc.commits,[])
        self.assertEqual(len(base.messages),2)
        self.assertFalse(spoken)
    def test_controls_images_links_and_real_empty_table_cells_are_not_skipped(self):
        for name in ('BUTTON','EDITABLETEXT','GRAPHIC','LINK','TABLECELL','SEPARATOR'):
            doc = Document(['start','','finish'])
            doc.fields[1] = [field('DOCUMENT'),field(name),end(),end()]
            self.move(doc)
            self.assertEqual(doc.caret,1,name)
        doc = Document(['start','','finish'])
        doc.fields[1] = [field('UNKNOWN', states={'focusable'}),end()]
        self.move(doc); self.assertEqual(doc.caret,1)
    def test_layout_table_whitespace_skips_but_controls_inside_layout_are_preserved(self):
        for name, expected in (('TABLECELL',2),('BUTTON',1),('GRAPHIC',1),('UNKNOWN',1)):
            doc = Document(['start','','finish'])
            extra = {'states':{'focusable'}} if name == 'UNKNOWN' else {}
            doc.fields[1] = [field('TABLE',**{'table-layout':True}),field(name,**extra),end(),end()]
            self.move(doc); self.assertEqual(doc.caret,expected,name)
    def test_source_fields_and_provider_failures_do_not_hide_content(self):
        doc = Document(['start','','finish']); doc.fields[1] = ['accessible text']
        self.move(doc); self.assertEqual(doc.caret,1)
        doc = Document(['start','','finish']); doc.failFields = True
        self.move(doc); self.assertEqual(doc.caret,1)
        doc = Document(['start','','finish']); doc.raiseMove = True
        self.move(doc); self.assertEqual(doc.originalCalls,1)
        doc = Document(['start','','finish']); doc.failCompare = True
        self.move(doc); self.assertEqual(doc.originalCalls,1)
    def test_no_progress_provider_cannot_loop(self):
        doc = Document(['start','','finish']); doc.lieMove = True
        self.move(doc); self.assertEqual(doc.originalCalls,1)
        self.assertEqual(doc.moves,1)
    def test_disabled_native_focus_mode_modifiers_selection_and_other_units_use_original(self):
        for action in ('disabled','native','focus','modifier','selection','paragraph','page','home','sayAll','waiting','protected'):
            doc = Document(['start','','finish'])
            gesture = types.SimpleNamespace(mainKeyName='downArrow',modifierNames=())
            unit, direction, options = 'line',1,{}
            self.enabled = True; waiting[0] = sayAll[0] = False; base.api.isTypingProtected = lambda: False
            if action=='disabled': self.enabled=False
            elif action=='native': doc.rootNVDAObject=object(); doc=type('Native',(Cursor,),dict(doc.__dict__))()
            elif action=='focus': doc.passThrough=True
            elif action=='modifier': gesture.modifierNames=('shift',)
            elif action=='selection': doc.selected=True
            elif action=='paragraph': unit='paragraph'
            elif action=='page': gesture.mainKeyName='pageDown'
            elif action=='home': options['posConstant']='first'
            elif action=='sayAll': sayAll[0]=True
            elif action=='waiting': waiting[0]=True
            elif action=='protected': base.api.isTypingProtected=lambda:True
            result = doc._caretMovementScriptHelper(gesture,unit,direction,**options)
            self.assertEqual(result,'original',action)
            self.assertEqual(doc.originalCalls,1,action)
            self.assertEqual(doc.moves,0,action)
    def test_original_failure_is_never_replayed_twice(self):
        doc = Document(['one','two']); doc.passThrough=True; doc.failOriginal=True
        with self.assertRaises(RuntimeError): self.move(doc)
        self.assertEqual(doc.originalCalls,1)
    def test_only_target_line_reaches_actual_automatic_translation_controller(self):
        class Callback:
            def __init__(self, func): self.func=func
        base.stub('speech.commands',CallbackCommand=Callback)
        canceled=Action(); base.stub('speech.extensions',speechCanceled=canceled)
        speech.SpeechMode=types.SimpleNamespace(talk='talk')
        speech.getState=lambda:types.SimpleNamespace(speechMode='talk')
        speech.speak=lambda sequence:None; speech.cancelSpeech=canceled.notify
        module=base.load('translationTestPackage.automaticTranslation','automaticTranslation.py')
        fetched=[]
        auto=module.AutomaticTranslation(lambda:dict(lineTranslation=True,automaticTranslation=True,translationTarget='tr'),
            lambda text,target:fetched.append(text) or 'translated:'+text)
        try:
            auto.toggle()
            doc=Document(['start','',' ','finish'])
            self.move(doc)
            deadline=time.monotonic()+1
            while not fetched and time.monotonic()<deadline: time.sleep(.002)
            self.assertEqual(fetched,['finish'])
            self.assertEqual(doc.caret,3)
        finally:auto.terminate()

if __name__=='__main__':unittest.main(verbosity=2)
