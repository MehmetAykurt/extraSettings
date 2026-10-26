"""FIFO, line identity, cancellation, privacy and speech boundary regression tests."""
import queue
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

class Callback:
    def __init__(self, func): self.func = func

originalCalls = []
objectCalls = []
def original(info, **kwargs):
    originalCalls.append((info, kwargs))
    return 'original-return'

canceled = Action()
spoken = []
def originalObject(obj, **kwargs):
    objectCalls.append((obj, kwargs))
    return 'original-object'
def objectSpeech(obj, reason, prefix):
    objectCalls.append(('sequence', obj))
    return [obj.name, 'düğmesi']
speech = base.stub('speech', speakTextInfo=original, speak=lambda seq: spoken.append(seq),
    speakObject=originalObject, getObjectSpeech=objectSpeech,
    getState=lambda: types.SimpleNamespace(speechMode='talk'),
    SpeechMode=types.SimpleNamespace(talk='talk'), cancelSpeech=canceled.notify)
base.stub('speech.commands', CallbackCommand=Callback)
base.stub('speech.extensions', speechCanceled=canceled)
reason = types.SimpleNamespace(QUERY='query', CARET='caret', ONLYCACHE='cache', FOCUS='focus')
base.stub('controlTypes', OutputReason=reason)
auto = base.load('translationTestPackage.automaticTranslation', 'automaticTranslation.py')

class Info:
    def __init__(self, obj, index, text, protected=False):
        self.obj, self.index, self.value = obj, index, text
        self.NVDAObjectAtStart = types.SimpleNamespace(isProtected=protected)
    def copy(self): return Info(self.obj, self.index, self.value, self.NVDAObjectAtStart.isProtected)
    def expand(self, unit): assert unit == 'line'
    def compareEndPoints(self, other, which): return self.index - other.index
    @property
    def text(self):
        assert threading.get_ident() == base.MAIN_THREAD
        return self.value

class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.conf = dict(lineTranslation=True, translationTarget='tr', automaticTranslation=True)
        self.fetches = []
        self.controller = None
        self.document = types.SimpleNamespace(isProtected=False)
        originalCalls.clear(); spoken.clear(); base.messages.clear(); base.delays.clear()
        base.api.isTypingProtected = lambda: False
        base.api.getCaretObject = lambda: None
        base.api.getFocusObject = lambda: None
        speech.speakTextInfo = original
        speech.speakObject = originalObject
        objectCalls.clear()
        while not base.callbacks.empty(): base.callbacks.get_nowait()
    def make(self, fetch=None, start=True):
        def fetchDefault(text, target):
            self.fetches.append((text, target))
            return 'translated:' + text
        def settings():
            assert threading.get_ident() == base.MAIN_THREAD
            return self.conf
        self.controller = auto.AutomaticTranslation(settings, fetch or fetchDefault)
        if start and self.conf['lineTranslation'] and self.conf['automaticTranslation']:
            self.controller.toggle()
        return self.controller
    def tearDown(self):
        if self.controller:
            self.controller.terminate()
            worker = self.controller._worker
            if worker: worker.join(1)
        self.assertEqual(canceled.handlers, [])
        self.assertIs(speech.speakObject, originalObject)
    def info(self, index, text=None): return Info(self.document, index, text or str(index))
    def dispatch(self):
        while not base.callbacks.empty():
            func, args = base.callbacks.get_nowait(); func(*args)
    def until(self, predicate):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            self.dispatch()
            if predicate(): return
            time.sleep(.002)
        self.fail('worker/callback did not finish')
    def complete(self):
        spoken[-1][-1].func()
        pending, base.delays[:] = base.delays[:], []
        for func, args in pending: func(*args)
    def test_rapid_rows_are_sent_and_spoken_in_order_with_no_drops(self):
        controller = self.make()
        for i in range(1, 5): self.assertTrue(controller.capture(self.info(i)))
        self.until(lambda: len(controller._ready) == 4)
        self.assertEqual([text for text, target in self.fetches], ['1','2','3','4'])
        self.assertEqual(len(spoken), 1, 'Second row interrupted first row')
        for expected in ['translated:1', 'translated:2', 'translated:3', 'translated:4']:
            self.assertEqual(spoken[-1][0], expected)
            self.complete()
        self.assertEqual(controller._rows, 0)
        self.assertEqual(controller._characters, 0)
    def test_identical_adjacent_lines_preserved_same_position_not_requeued_and_return_is_read(self):
        controller = self.make()
        controller.capture(self.info(1, 'same')); controller.capture(self.info(1, 'same'))
        controller.capture(self.info(2, 'same')); controller.capture(self.info(1, 'same'))
        self.until(lambda: len(controller._ready) == 3)
        self.assertEqual(self.fetches, [('same','tr')], 'Cached result requested twice')
        self.assertEqual(controller._rows, 3)
    def test_line_speech_only_is_replaced_and_original_cache_updated(self):
        controller = self.make()
        value = speech.speakTextInfo(self.info(1), unit='line', reason='caret')
        self.assertEqual(value, 'original-return')
        self.assertEqual(originalCalls[-1][1]['reason'], 'cache')
        speech.speakTextInfo(self.info(2), unit='character', reason='caret')
        self.assertEqual(originalCalls[-1][1]['reason'], 'caret')
        speech.speakTextInfo(self.info(3), unit='line', reason='query')
        self.assertEqual(originalCalls[-1][1]['reason'], 'query')
        self.assertEqual(controller._rows, 1)
    def test_focus_controls_are_translated_in_order_and_same_control_deduplicated(self):
        controller = self.make()
        first = types.SimpleNamespace(name='Save', isProtected=False)
        second = types.SimpleNamespace(name='Open', isProtected=False)
        speech.speakObject(first, reason='focus')
        speech.speakObject(first, reason='focus')
        speech.speakObject(second, reason='focus')
        speech.speakObject(first, reason='focus')
        self.until(lambda: len(controller._ready) == 3)
        self.assertEqual(self.fetches, [('Save', 'tr'), ('Open', 'tr')])
        self.assertEqual([call[0] for call in objectCalls], ['sequence'] * 4)
        self.assertEqual(controller._rows, 3)
        for text in ('Save', 'Open', 'Save'):
            self.assertEqual(spoken[-1][0], 'translated:' + text)
            self.assertEqual(spoken[-1][1], 'düğmesi')
            self.complete()
    def test_disabled_query_and_protected_objects_use_original_without_reading_sequence(self):
        controller = self.make(start=False)
        obj = types.SimpleNamespace(name='Save', isProtected=False)
        self.assertEqual(speech.speakObject(obj, reason='focus'), 'original-object')
        controller.toggle()
        self.assertEqual(speech.speakObject(obj, reason='query'), 'original-object')
        obj.isProtected = True
        self.assertEqual(speech.speakObject(obj, reason='focus'), 'original-object')
        self.assertFalse(any(call[0] == 'sequence' for call in objectCalls))
        self.assertFalse(self.fetches)
    def test_toggle_on_focused_button_captures_name_without_a_caret(self):
        obj = types.SimpleNamespace(name='Save', value=None, description=None, isProtected=False)
        base.api.getFocusObject = lambda: obj
        controller = self.make()
        self.until(lambda: bool(controller._ready))
        self.assertEqual(self.fetches, [('Save', 'tr')])
    def test_localized_role_is_not_sent_and_changed_state_is_not_deduplicated(self):
        def fetch(text, target):
            self.fetches.append((text, target))
            return text if 'düğmesi' in text else 'Kaydet'
        controller = self.make(fetch)
        obj = types.SimpleNamespace(name='Save', isProtected=False)
        with patch.object(speech, 'getObjectSpeech', side_effect=[
                ['Save', 'düğmesi', 'kullanılabilir'], ['Save', 'düğmesi', 'kullanılamaz']]):
            speech.speakObject(obj, reason='focus')
            speech.speakObject(obj, reason='focus')
        self.until(lambda: len(controller._ready) == 2)
        self.assertEqual(self.fetches, [('Save', 'tr')])
        self.assertEqual(spoken[-1][:-1], ['Kaydet', 'düğmesi', 'kullanılabilir'])
        self.complete()
        self.assertEqual(spoken[-1][:-1], ['Kaydet', 'düğmesi', 'kullanılamaz'])
    def test_cancel_replays_interrupted_row_and_ignores_old_callback(self):
        controller = self.make()
        controller.capture(self.info(1)); controller.capture(self.info(2))
        self.until(lambda: len(controller._ready) == 2)
        oldCallback = spoken[-1][-1]
        canceled.notify()
        oldCallback.func()
        self.assertEqual(controller._rows, 2)
        pending, base.delays[:] = base.delays[:], []
        for func, args in pending: func(*args)
        self.assertEqual([seq[0] for seq in spoken], ['translated:1','translated:1'])
        self.complete()
        self.assertEqual(spoken[-1][0], 'translated:2')
    def test_disable_clears_queue_cache_and_discards_in_flight_result(self):
        entered, release = threading.Event(), threading.Event()
        def fetch(text, target): entered.set(); release.wait(2); return text
        controller = self.make(fetch)
        controller.capture(self.info(1)); self.assertTrue(entered.wait(1))
        controller.capture(self.info(2)); controller.toggle()
        self.assertFalse(controller._enabled)
        self.assertFalse(controller._pending); self.assertFalse(controller._ready)
        self.assertFalse(controller._cache); self.assertIsNone(controller._lastInfo)
        release.set(); time.sleep(.03); self.dispatch()
        self.assertEqual(spoken, [])
        self.assertEqual(controller._cache, {})
    def test_target_change_ends_old_generation(self):
        controller = self.make()
        controller.capture(self.info(1))
        self.until(lambda: bool(controller._ready))
        oldCallback = spoken[-1][-1]
        self.conf['translationTarget'] = 'de'; controller.applySettings()
        oldCallback.func()
        self.assertEqual(controller._rows, 0)
        self.assertFalse(controller._cache)
        controller.capture(self.info(2))
        self.until(lambda: bool(controller._ready))
        self.assertEqual(self.fetches[-1], ('2','de'))
    def test_protected_and_blank_lines_are_not_sent(self):
        controller = self.make()
        self.assertFalse(controller.capture(self.info(1, ' ')))
        self.assertFalse(controller.capture(Info(self.document, 2, 'secret', True)))
        base.api.isTypingProtected = lambda: True
        self.assertFalse(controller.capture(self.info(3, 'secret')))
        self.assertEqual(self.fetches, [])
    def test_full_queue_pauses_without_deleting_accepted_rows(self):
        controller = self.make()
        with patch.object(auto, 'MAX_ROWS', 2):
            controller.capture(self.info(1)); controller.capture(self.info(2))
            self.assertFalse(controller.capture(self.info(3)))
        self.assertTrue(controller._paused)
        self.until(lambda: len(controller._ready) == 2)
        self.assertEqual([text for text, target in self.fetches], ['1','2'])
        self.assertTrue(any('queue is full' in text for text in base.messages))
    def test_network_failure_holds_failing_and_following_rows_until_closed(self):
        def fail(text, target): raise auto.TranslationFailure('timeout')
        controller = self.make(fail)
        controller.capture(self.info(1)); controller.capture(self.info(2))
        self.until(lambda: controller._paused)
        self.assertEqual([entry[1] for entry in controller._pending], ['1', '2'])
        self.assertEqual(controller._rows, 2)
        controller.toggle()
        self.assertEqual(controller._rows, 0)
        self.assertFalse(controller._pending)
    def test_disabled_translation_does_not_enable_and_terminate_restores_hook(self):
        self.conf['lineTranslation'] = False
        controller = self.make()
        controller.toggle()
        self.assertFalse(controller._enabled)
        speech.speakTextInfo(self.info(1), unit='line', reason='caret')
        self.assertEqual(originalCalls[-1][1]['reason'], 'caret')
        controller.terminate()
        self.assertIs(speech.speakTextInfo, original)
        self.controller = None

    def test_manual_option_does_not_control_or_clear_automatic_queue(self):
        self.conf['manualTranslation'] = False
        controller = self.make()
        controller.capture(self.info(1))
        self.until(lambda: bool(controller._ready))
        self.assertTrue(controller._enabled)
        generation = controller._generation
        self.conf['manualTranslation'] = True
        controller.applySettings()
        self.assertEqual(controller._generation, generation)
        self.assertEqual(controller._rows, 1)
        self.conf['lineTranslation'] = False
        controller.applySettings()
        self.assertFalse(controller._enabled)
        self.assertFalse(controller._ready)
        self.assertFalse(controller._cache)
        self.assertTrue(self.conf['automaticTranslation'])

    def test_unchecked_permission_blocks_shortcut_without_reading_or_changing_settings(self):
        self.conf['automaticTranslation'] = False
        controller = self.make(start=False)
        before = self.conf.copy()
        with patch.object(controller, 'captureCurrentLine', side_effect=AssertionError('Disabled option read caret')):
            controller.toggle()
        self.assertEqual(self.conf, before)
        self.assertFalse(controller._enabled)
        self.assertIsNone(controller._worker)
        self.assertIn('Enable automatic translation', base.messages[-1])

    def test_checked_permission_needs_shortcut_and_does_not_change_on_toggle(self):
        controller = self.make(start=False)
        self.assertFalse(controller._enabled, 'Saved permission started a session at startup')
        controller.applySettings()
        self.assertFalse(controller._enabled)
        before = self.conf.copy()
        controller.toggle()
        self.assertTrue(controller._enabled)
        self.assertEqual(self.conf, before)
        controller.capture(self.info(1))
        self.until(lambda: bool(controller._ready))
        controller.toggle()
        self.assertFalse(controller._enabled)
        self.assertEqual(self.conf, before)
        self.assertFalse(controller._ready)
        self.assertFalse(controller._cache)
        controller.applySettings()
        self.assertFalse(controller._enabled, 'Applying unchanged settings restarted a stopped session')

    def test_unchecking_permission_stops_session_and_rechecking_does_not_restart_it(self):
        controller = self.make()
        controller.capture(self.info(1))
        self.until(lambda: bool(controller._ready))
        self.conf['automaticTranslation'] = False
        controller.applySettings()
        self.assertFalse(controller._enabled)
        self.assertFalse(controller._ready)
        self.assertFalse(controller._cache)
        self.conf['automaticTranslation'] = True
        controller.applySettings()
        self.assertFalse(controller._enabled)
        controller.toggle()
        self.assertTrue(controller._enabled)

if __name__ == '__main__': unittest.main(verbosity=2)
