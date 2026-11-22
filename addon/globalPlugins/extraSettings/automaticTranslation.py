"""Capture line announcements synchronously; translate and speak in FIFO order.

Only the NVDA main thread touches TextInfo, settings or speech. A single lazy
worker owns HTTP work. No polling of the caret and no text is written to disk.
"""
from collections import OrderedDict, deque
import threading

import addonHandler
import api
import core
import speech
from speech.commands import CallbackCommand
from speech.extensions import speechCanceled
from controlTypes import OutputReason
import textInfos
import ui
import wx

from .lineTranslation import failureMessage, focusedText, _property, _protected
from .translationService import fetchTranslation, translationPair, TranslationFailure, LANGUAGE_CODES, MAX_TEXT

addonHandler.initTranslation()
MAX_ROWS = 1000
MAX_CHARACTERS = 1000000
CACHE_SIZE = 256


class AutomaticTranslation:
    def __init__(self, settings, fetch=None):
        self._settings = settings
        self._fetch = fetch or fetchTranslation
        self._condition = threading.Condition()
        self._active = True
        self._generation = 0
        self._enabled = False
        self._allowed = False
        self._paused = False
        self._target = None
        self._source = None
        self._pending = deque()
        self._ready = deque()
        self._cache = OrderedDict()
        self._lastInfo = None
        self._lastFocus = None
        self._lastText = None
        self._rows = self._characters = 0
        self._speaking = False
        self._speechTicket = 0
        self._retryScheduled = False
        self._worker = None
        self._original = speech.speakTextInfo
        self._wrapper = self._speakTextInfo
        speech.speakTextInfo = self._wrapper
        speechCanceled.register(self._onCanceled)
        self.applySettings()

    def applySettings(self):
        settings = self._settings()
        allowed = bool(settings['lineTranslation'] and settings.get('automaticTranslation', False))
        source, target = translationPair(settings)
        if (allowed, source, target) == (self._allowed, self._source, self._target):
            return
        # A change to the permitted mode or target ends the current session.
        # A new session starts only through the shortcut.
        self._clear()
        self._allowed = allowed
        self._source = source
        self._target = target

    def toggle(self):
        if not self._active:
            return
        self.applySettings()
        if not self._settings()['lineTranslation']:
            ui.message(_("Enable translation in Extra Settings first."))
            return
        if not self._settings().get('automaticTranslation', False):
            ui.message(_("Enable automatic translation in Extra Settings first."))
            return
        if self._source not in LANGUAGE_CODES or self._target not in LANGUAGE_CODES:
            ui.message(failureMessage('language'))
            return
        if self._enabled:
            self._clear()
        else:
            self._enabled = True
        ui.message(_("Automatic translation on.") if self._enabled else _("Automatic translation off."))
        if self._enabled:
            self.captureCurrentLine()

    def _clear(self):
        wasSpeaking = self._speaking
        self._enabled = False
        self._paused = False
        self._speechTicket += 1
        self._speaking = False
        self._retryScheduled = False
        with self._condition:
            self._generation += 1
            self._pending.clear()
            self._cache.clear()
            self._condition.notify_all()
        self._ready.clear()
        self._lastInfo = self._lastText = None
        self._lastFocus = None
        self._rows = self._characters = 0
        if wasSpeaking:
            speech.cancelSpeech()

    def _check(self):
        if not self._active:
            return False
        self.applySettings()
        if not self._enabled or self._paused:
            return False
        try:
            if api.isTypingProtected():
                return False
            getFocus = _property(api, "getFocusObject")
            if getFocus is not None and _protected(getFocus()):
                return False
        except Exception:
            return False
        return True

    def captureCurrentLine(self):
        if not self._check():
            return False
        try:
            getFocus = _property(api, "getFocusObject")
            focus = getFocus() if getFocus else None
            role = _property(_property(focus, "role"), "name", "")
            tree = _property(focus, "treeInterceptor")
            inBrowseMode = tree is not None and not _property(tree, "passThrough", True)
            if focus is not None and role not in ("EDITABLETEXT", "DOCUMENT", "TERMINAL", "RICHEDIT") and not inBrowseMode:
                return self.captureFocus(focus)
            obj = api.getCaretObject()
            if obj is None or _protected(obj):
                return False
            return self.capture(obj.makeTextInfo(textInfos.POSITION_CARET))
        except Exception:
            return False

    def captureFocus(self, focus=None):
        """Queue focused control text; document lines keep their existing path."""
        if not self._check():
            return False
        try:
            focus = focus if focus is not None else api.getFocusObject()
            if self._lastFocus is not None and self._lastFocus != focus:
                self._lastFocus = self._lastText = None
            role = _property(_property(focus, "role"), "name", "")
            if role in ("EDITABLETEXT", "DOCUMENT", "TERMINAL", "RICHEDIT"):
                self._lastFocus = None
                return False
            tree = _property(focus, "treeInterceptor")
            if tree is not None and not _property(tree, "passThrough", True):
                return False
            text = focusedText(focus)
            if self._lastFocus == focus and self._lastText == text:
                return True
            accepted = self._enqueue(text)
            if accepted:
                self._lastFocus, self._lastText = focus, text
                self._lastInfo = None
            return accepted
        except TranslationFailure as error:
            if error.reason == "length":
                self._pause("length")
            return False
        except Exception:
            return False

    def capture(self, info):
        if not self._check():
            return False
        try:
            if _protected(info.obj):
                return False
            line = info.copy()
            line.expand(textInfos.UNIT_LINE)
            try:
                if _protected(line.NVDAObjectAtStart):
                    return False
            except (AttributeError, NotImplementedError):
                pass
            text = line.text.strip()
            same = False
            if self._lastInfo is not None and line.obj == self._lastInfo.obj:
                try:
                    same = (line.compareEndPoints(self._lastInfo, 'startToStart') == 0
                            and line.compareEndPoints(self._lastInfo, 'endToEnd') == 0
                            and text == self._lastText)
                except (NotImplementedError, AttributeError, TypeError):
                    pass
            if same:
                return bool(text)
            self._lastInfo, self._lastText = line, text
            self._lastFocus = None
        except Exception:
            # If positions cannot be compared, do not silently deduplicate two
            # different lines just because their text happens to be identical.
            return False
        return self._enqueue(text)

    def _enqueue(self, text):
        if not text:
            return False
        if len(text) > MAX_TEXT:
            self._pause('length')
            return False
        if self._rows >= MAX_ROWS or self._characters + len(text) > MAX_CHARACTERS:
            self._pause('capacity')
            return False
        with self._condition:
            if self._worker is None or not self._worker.is_alive():
                try:
                    self._worker = threading.Thread(target=self._run, name='ExtraSettingsAutomaticTranslation', daemon=True)
                    self._worker.start()
                except RuntimeError:
                    self._worker = None
                    self._pause('unavailable')
                    return False
            self._rows += 1
            self._characters += len(text)
            self._pending.append((self._generation, text, self._source, self._target))
            self._condition.notify_all()
        return True

    def _speakTextInfo(self, info, useCache=True, formatConfig=None, unit=None,
                       reason=OutputReason.QUERY, _prefixSpeechCommand=None,
                       onlyInitialFields=False, suppressBlanks=False, priority=None):
        if unit == textInfos.UNIT_LINE and reason == OutputReason.CARET and self.capture(info):
            # Preserve NVDA's formatting/control-field cache while replacing
            # just this line's original announcement with its translation.
            reason = OutputReason.ONLYCACHE
        return self._original(info, useCache=useCache, formatConfig=formatConfig, unit=unit,
            reason=reason, _prefixSpeechCommand=_prefixSpeechCommand,
            onlyInitialFields=onlyInitialFields, suppressBlanks=suppressBlanks, priority=priority)

    def _run(self):
        while True:
            with self._condition:
                while self._active and not self._pending:
                    self._condition.wait()
                if not self._active:
                    return
                generation, text, source, target = self._pending.popleft()
                key = (text, source, target)
                result = self._cache.get(key)
            error = None
            if result is None:
                try:
                    result = self._fetch(text, target, source)
                    if not isinstance(result, str) or not result.strip():
                        raise TranslationFailure('response')
                except TranslationFailure as failure:
                    error = failure.reason
                except Exception:
                    error = 'unavailable'
            with self._condition:
                if not self._active:
                    return
                if generation == self._generation:
                    if not error:
                        self._cache[key] = result
                        self._cache.move_to_end(key)
                        while len(self._cache) > CACHE_SIZE:
                            self._cache.popitem(last=False)
                    else:
                        # Hold this and following rows until the user closes
                        # the session; no failing row is silently skipped.
                        self._pending.appendleft((generation, *key))
                    try:
                        wx.CallAfter(self._finish, generation, len(text), result, error)
                    except RuntimeError:
                        return
                    if error:
                        self._condition.wait_for(lambda: not self._active or generation != self._generation)
            del text, source, target, result, key, error

    def _finish(self, generation, length, result, error):
        if not self._active or not self._enabled or generation != self._generation:
            return
        if error:
            self._pause(error)
            return
        self._ready.append((length, result))
        self._pump()

    def _pump(self):
        if not self._active or not self._enabled or self._speaking or not self._ready:
            return
        try:
            getFocus = _property(api, "getFocusObject")
            if (api.isTypingProtected() or (getFocus is not None and _protected(getFocus()))
                    or speech.getState().speechMode != speech.SpeechMode.talk):
                if not self._retryScheduled:
                    self._retryScheduled = True
                    core.callLater(200, self._retryPump, self._generation)
                return
        except Exception:
            return
        self._speaking = True
        self._speechTicket += 1
        ticket = self._speechTicket
        speech.speak([self._ready[0][1], CallbackCommand(lambda: self._spoken(ticket))])

    def _retryPump(self, generation):
        if generation != self._generation:
            return
        self._retryScheduled = False
        self._pump()

    def _spoken(self, ticket):
        if not self._active or ticket != self._speechTicket or not self._speaking:
            return
        self._speaking = False
        length, result = self._ready.popleft()
        self._rows -= 1
        self._characters -= length
        core.callLater(0, self._pump)

    def _onCanceled(self):
        if self._speaking:
            self._speaking = False
            self._speechTicket += 1
            # Retain the interrupted row; continue once navigation settles.
            core.callLater(200, self._pump)

    def _pause(self, reason):
        self._paused = True
        message = (_("The automatic translation queue is full.") if reason == 'capacity'
                   else failureMessage(reason))
        ui.message(message + ' ' + _("Automatic translation paused. Turn it off and on to start a new session."))

    def terminate(self):
        self._active = False
        self._clear()
        speechCanceled.unregister(self._onCanceled)
        if speech.speakTextInfo is self._wrapper:
            speech.speakTextInfo = self._original
