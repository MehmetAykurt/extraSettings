"""Announce standard editing shortcuts without taking over their execution."""
import addonHandler
import api
import core
import inputCore
import scriptHandler
import textInfos
import ui
import winUser
from controlTypes import State
from ctypes import WinDLL, get_last_error, set_last_error
from ctypes.wintypes import UINT, BOOL

addonHandler.initTranslation()

# Exact identifiers deliberately exclude Ctrl+Shift/Alt/Windows variations.
_OPERATIONS = {
    "kb:control+x": "cut",
    "kb:control+c": "copy",
    "kb:control+v": "paste",
}

_user32 = WinDLL("user32", use_last_error=True)
_enumClipboardFormats = _user32.EnumClipboardFormats
_enumClipboardFormats.argtypes = [UINT]
_enumClipboardFormats.restype = UINT
_hasClipboardFormat = _user32.IsClipboardFormatAvailable
_hasClipboardFormat.argtypes = [UINT]
_hasClipboardFormat.restype = BOOL


def hasSelectedData(focus):
    """Inspect selection on NVDA's main thread, before executing cut/copy."""
    textContainers = []
    try:
        textContainers.append(api.getCaretObject())
    except Exception:
        pass
    if not any(container is focus for container in textContainers):
        textContainers.append(focus)
    textSelectionKnown = False
    for textContainer in textContainers:
        try:
            # AutoProperty values can still describe a previous pump cycle.
            textContainer.invalidateCache()
        except Exception:
            pass
        try:
            selection = textContainer.makeTextInfo(textInfos.POSITION_SELECTION)
            if not selection.isCollapsed:
                return True
            textSelectionKnown = True
        except Exception:
            # Non-text controls commonly do not implement selection TextInfo.
            pass
    try:
        if State.SELECTED in focus.states:
            return True
    except Exception:
        pass
    candidates = [focus]
    try:
        container = focus.selectionContainer
        if container is not None and container is not focus:
            candidates.append(container)
    except Exception:
        pass
    for candidate in candidates:
        try:
            if candidate.getSelectedItemsCount(maxCount=1) > 0:
                return True
        except Exception:
            pass
    # For text controls which expose no usable selection API, say that the
    # selection could not be checked, rather than claiming nothing is selected.
    try:
        if not textSelectionKnown and State.EDITABLE in focus.states:
            return None
    except Exception:
        pass
    return False


def hasClipboardData():
    """Return True/False, or None if the clipboard is temporarily unavailable.

    Keep files, images and application formats valid. A text-only clipboard
    holding an empty string is empty; spaces are still valid selected data.
    Inspect text only to determine emptiness, without logging or retaining it.
    """
    try:
        with winUser.openClipboard():
            formats = set()
            previous = 0
            while True:
                set_last_error(0)
                current = _enumClipboardFormats(previous)
                if current == 0:
                    if get_last_error():
                        return None
                    break
                # A malformed or changing enumeration must not hold up a key.
                if current in formats or len(formats) >= 256:
                    return None
                formats.add(current)
                previous = current
            if not formats:
                return False
            if _hasClipboardFormat(winUser.CF_UNICODETEXT):
                if winUser.getClipboardData(winUser.CF_UNICODETEXT):
                    return True
            # ANSI/OEM/Unicode text and locale metadata alone do not make an
            # empty string pasteable. Any other format can carry non-text data.
            return bool(formats - {1, 7, 13, 16})
    except Exception:
        return None


class ClipboardAnnouncements:
    def __init__(self, isEnabled, shouldIgnore=lambda gesture: False):
        self._enabled = isEnabled
        self._shouldIgnore = shouldIgnore
        self._active = True
        self._replayingGesture = None
        inputCore.decide_executeGesture.register(self._onGesture)

    def _onGesture(self, gesture):
        if self._shouldIgnore(gesture):
            return True
        if gesture is self._replayingGesture:
            return True
        if not self._active or not self._enabled():
            return True
        manager = inputCore.manager
        if manager.isInputHelpActive or getattr(manager, "_captureFunc", None):
            return True
        operation = next((
            _OPERATIONS[identifier] for identifier in gesture.identifiers
            if identifier in _OPERATIONS
        ), None)
        if operation is None:
            return True
        focus = api.getFocusObject()
        if focus is None or focus.sleepMode:
            return True
        # Gesture deciders run on the keyboard hook thread. Cross-thread
        # selection queries may fail even for an actual one-character range.
        # Queue an intercepted-command script: selection is checked on NVDA's
        # core thread, before the original command, in normal keyboard order.
        scriptHandler.queueScript(self._execute, gesture)
        return False

    def _execute(self, gesture):
        operation = next(_OPERATIONS[key] for key in gesture.identifiers if key in _OPERATIONS)
        available = None
        shouldAnnounce = self._active and self._enabled()
        if shouldAnnounce:
            try:
                focus = api.getFocusObject()
                shouldAnnounce = focus is not None and not focus.sleepMode
                if shouldAnnounce:
                    available = hasClipboardData() if operation == "paste" else hasSelectedData(focus)
            except Exception:
                # A failed accessibility provider must never swallow the key.
                available = None
        # Always execute once, including if the checkbox was disabled or the
        # add-on terminated after the key was queued. Re-enter NVDA's normal
        # gesture routing so browse-mode copy and application scripts survive.
        self._replayingGesture = gesture
        try:
            inputCore.manager.emulateGesture(gesture)
        finally:
            self._replayingGesture = None
        if shouldAnnounce:
            core.callLater(150, self._announce, operation, available)

    def _announce(self, operation, available):
        if not self._active or not self._enabled():
            return
        # Translators: Brief feedback after the standard editing shortcuts.
        if operation == "paste" and available is None:
            # Translators: The clipboard could not be opened or inspected.
            message = _("The clipboard could not be checked right now.")
        elif available is None:
            # Translators: The text control does not expose selection details.
            message = _("The selection could not be checked.")
        elif not available:
            # Translators: No selected source, or no clipboard data to paste.
            messages = {
                "cut": _("No selected data was found to cut."),
                "copy": _("No selected data was found to copy."),
                "paste": _("No data was found to paste."),
            }
            message = messages[operation]
        else:
            messages = {"cut": _("Cut."), "copy": _("Copied."), "paste": _("Pasted.")}
            message = messages[operation]
        ui.message(message)

    def terminate(self):
        if not self._active:
            return
        self._active = False
        inputCore.decide_executeGesture.unregister(self._onGesture)
