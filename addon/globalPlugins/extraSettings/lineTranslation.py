"""Translate accessible line or focused-control text using one background worker."""
from collections import OrderedDict
import threading
import time

import addonHandler
import api
import core
import controlTypes
import textInfos
import ui
import wx

from .translationService import fetchTranslation, translationPair, TranslationFailure, LANGUAGE_CODES, MAX_TEXT

addonHandler.initTranslation()

CACHE_SECONDS = 120
CACHE_ENTRIES = 32
REQUEST_INTERVAL = 0.5


def _property(obj, name, default=None):
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _protected(obj):
    return bool(_property(obj, "isProtected", False)
                or _property(_property(obj, "role"), "name") == "PASSWORDEDIT"
                or controlTypes.State.PROTECTED in (_property(obj, "states", set()) or ()))


def focusedText(focus):
    """Read just this object's exposed text, without traversing other controls."""
    if focus is None:
        raise TranslationFailure("caret")
    if _protected(focus):
        raise TranslationFailure("protected")
    parts = []
    for attribute in ("name", "value", "description"):
        value = _property(focus, attribute)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    if not parts:
        raise TranslationFailure("empty")
    return _boundedText("\n".join(parts))


def _boundedText(text):
    if not isinstance(text, str):
        raise TranslationFailure("caret")
    text = text.strip()
    if not text:
        raise TranslationFailure("empty")
    if len(text) > MAX_TEXT:
        raise TranslationFailure("length")
    return text


def _lineText(container):
    if container is None:
        raise TranslationFailure("caret")
    if _protected(container):
        raise TranslationFailure("protected")
    try:
        container.invalidateCache()
    except Exception:
        pass
    info = container.makeTextInfo(textInfos.POSITION_CARET)
    info.expand(textInfos.UNIT_LINE)
    if _protected(_property(info, "NVDAObjectAtStart")):
        raise TranslationFailure("protected")
    return _boundedText(info.text)


def currentLine():
    """Resolve the current accessible content on NVDA's main thread."""
    try:
        if api.isTypingProtected():
            raise TranslationFailure("protected")
        focus = api.getFocusObject()
        if focus is None:
            raise TranslationFailure("caret")
        if _protected(focus):
            raise TranslationFailure("protected")
    except TranslationFailure:
        raise
    except Exception:
        raise TranslationFailure("caret") from None
    role = _property(_property(focus, "role"), "name", "")
    # A focused button may coexist with a stale caret in its document.
    # Prefer control text before asking the global caret in that case.
    textControl = role in ("EDITABLETEXT", "PASSWORDEDIT", "DOCUMENT", "TERMINAL", "RICHEDIT")
    browse = _property(focus, "treeInterceptor")
    inBrowseMode = browse is not None and not _property(browse, "passThrough", True)
    if not textControl and not inBrowseMode:
        try:
            return focusedText(focus)
        except TranslationFailure as error:
            if error.reason != "empty":
                raise
        container = focus
    else:
        try:
            container = api.getCaretObject()
        except Exception:
            container = focus
    try:
        return _lineText(container)
    except TranslationFailure as error:
        if error.reason in ("protected", "length"):
            raise
        if textControl and error.reason == "empty":
            raise
    except Exception:
        pass
    return focusedText(focus)


def failureMessage(reason):
    messages = {
        "protected": _("Protected fields cannot be translated."),
        "caret": _("The current text or control could not be read."),
        "empty": _("There is no accessible text to translate here."),
        "length": _("This content is too long to translate. The limit is 5000 characters."),
        "language": _("Choose a supported source and target language in Extra Settings."),
        "limited": _("The translation service is limiting access. Try again later."),
        "timeout": _("The translation service did not respond in time."),
        "response": _("The translation service returned an unreadable response."),
        "unavailable": _("The translation request could not be completed. Please try again."),
    }
    return messages.get(reason, messages["unavailable"])


class LineTranslation:
    def __init__(self, settings, fetch=None, clock=None):
        self._settings = settings
        self._fetch = fetch or fetchTranslation
        self._clock = clock or time.monotonic
        self._condition = threading.Condition()
        self._active = True
        self._serial = 0
        self._pending = None
        self._current = None
        self._worker = None
        self._lastNetwork = float("-inf")
        self._cache = OrderedDict()
        self._waiting = False
        self._retryAfter = 0
        self._lastSettings = self._settingsKey()

    def _settingsKey(self):
        settings = self._settings()
        return bool(settings["lineTranslation"] and settings.get("manualTranslation", True)), *translationPair(settings)

    def toggleDirection(self):
        if not self._active:
            return
        settings = self._settings()
        if not settings["lineTranslation"]:
            ui.message(_("Enable translation in Extra Settings first."))
            return
        reversedDirection = not settings.get("reverseTranslation", False)
        settings["reverseTranslation"] = reversedDirection
        self.applySettings()
        ui.message(_("Reverse translation on.") if reversedDirection else _("Reverse translation off."))

    def applySettings(self):
        """Only called on the main thread; never access config in the worker."""
        if hasattr(self, 'automatic'):
            self.automatic.applySettings()
        settings = self._settingsKey()
        if settings == self._lastSettings:
            return
        self._lastSettings = settings
        with self._condition:
            self._serial += 1
            self._pending = None
            self._waiting = False
        self._cache.clear()

    def translateCurrentLine(self):
        if not self._active:
            return
        self.applySettings()
        enabled, source, target = self._lastSettings
        if not enabled:
            ui.message(_("Enable line translation in Extra Settings first.") if self._settings()["lineTranslation"]
                       else _("Enable translation in Extra Settings first."))
            return
        if source not in LANGUAGE_CODES or target not in LANGUAGE_CODES:
            ui.message(failureMessage("language"))
            return
        try:
            text = currentLine()
        except TranslationFailure as error:
            # Invalidate an earlier request even when this line cannot be read.
            with self._condition:
                self._serial += 1
                self._pending = None
                self._waiting = False
            ui.message(failureMessage(error.reason))
            return
        self._request(text, source, target)

    def _request(self, text, source, target):
        key = (text, source, target)
        now = self._clock()
        # Also purge expired text so cache retention is bounded in time when
        # the feature is used again. Nothing is persisted to disk.
        for expired in [key for key, (when, result) in self._cache.items() if now - when >= CACHE_SECONDS]:
            del self._cache[expired]
        cached = self._cache.get(key)
        with self._condition:
            if not self._active:
                return
            if not cached and ((self._pending and self._pending[1:] == key)
                               or (self._current and self._current[0] == self._serial and self._current[1:] == key)):
                ui.message(_("Translation is already in progress."))
                return
            self._serial += 1
            token = self._serial
            self._pending = None
            self._waiting = False
            if cached:
                self._cache.move_to_end(key)
                ui.message(cached[1])
                return
            if now < self._retryAfter:
                ui.message(failureMessage("limited"))
                return
            if self._worker is None or not self._worker.is_alive():
                try:
                    self._worker = threading.Thread(target=self._run, name="ExtraSettingsTranslation", daemon=True)
                    self._worker.start()
                except RuntimeError:
                    self._worker = None
                    ui.message(failureMessage("unavailable"))
                    return
            self._pending = (token, *key)
            self._waiting = True
            self._condition.notify()
        core.callLater(700, self._announceWaiting, token)

    def _announceWaiting(self, token):
        if self._waiting and token == self._serial and self._canAnnounce():
            ui.message(_("Translating..."))

    def _canAnnounce(self):
        if not self._active or not self._lastSettings[0]:
            return False
        try:
            getFocus = _property(api, "getFocusObject")
            return (self._settingsKey() == self._lastSettings and not api.isTypingProtected()
                    and (getFocus is None or not _protected(getFocus())))
        except Exception:
            # Focus/config can be unavailable during shutdown or a reset.
            return False

    def _run(self):
        while True:
            with self._condition:
                while self._active:
                    if self._pending is None:
                        self._condition.wait()
                        continue
                    pause = REQUEST_INTERVAL - (self._clock() - self._lastNetwork)
                    if pause > 0:
                        self._condition.wait(pause)
                        continue
                    break
                if not self._active:
                    return
                request = self._current = self._pending
                self._pending = None
                self._lastNetwork = self._clock()
            token, text, source, target = request
            result, error = None, None
            try:
                result = self._fetch(text, target, source)
            except TranslationFailure as failure:
                error = failure.reason
            except Exception:
                # Do not log exception representations, URLs or source text.
                error = "unavailable"
            with self._condition:
                self._current = None
                if not self._active:
                    return
                try:
                    wx.CallAfter(self._finish, token, (text, source, target), result, error)
                except RuntimeError:
                    # wx may already be shutting down; do not leak a worker
                    # exception or retain text in a dormant thread.
                    return
            del request, text, source, target, result, error

    def _finish(self, token, key, result, error):
        # wx dispatch ensures that config, the cache, APIs and speech are only
        # accessed on the main thread. Late/obsolete results are discarded.
        if not self._active or token != self._serial:
            return
        self._waiting = False
        if not self._canAnnounce():
            return
        if error:
            if error == "limited":
                self._retryAfter = self._clock() + 30
            ui.message(failureMessage(error))
            return
        if not isinstance(result, str) or not result.strip():
            ui.message(failureMessage("response"))
            return
        self._cache[key] = (self._clock(), result)
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)
        ui.message(result)

    def terminate(self):
        if hasattr(self, 'automatic'):
            self.automatic.terminate()
        with self._condition:
            self._active = False
            self._serial += 1
            self._pending = None
            self._waiting = False
            self._condition.notify_all()
        self._cache.clear()
