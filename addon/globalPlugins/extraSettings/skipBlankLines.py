"""Skip genuinely empty web lines, using bounded, main-thread TextInfo walks.

Only ordinary single-line Up/Down browse-mode movements are handled. A probe
range moves while the real caret stays in place; commit once at the target.
No keyboard emulation, whole-document copy, persistent cache or polling.
"""
from dataclasses import dataclass
from functools import wraps
import time

import addonHandler
import api
import core
import cursorManager
import inputCore
import scriptHandler
import speech
import textInfos
import ui
from controlTypes import OutputReason, Role, State

from .webCycle import isWebDocument

addonHandler.initTranslation()

SLICE_SECONDS = 0.008
SLICE_STEPS = 256
_CONTENT_ROLES = frozenset(getattr(Role, name) for name in (
    'BUTTON', 'CHECKBOX', 'RADIOBUTTON', 'EDITABLETEXT', 'COMBOBOX', 'LISTBOX',
    'SLIDER', 'SPINBUTTON', 'GRAPHIC', 'LINK', 'SEPARATOR', 'PROGRESSBAR', 'MATH',
    'TABLE', 'TABLECELL', 'TABLEROW', 'COLUMNHEADER', 'ROWHEADER',
) if hasattr(Role, name))
_CONTAINER_ROLES = frozenset(getattr(Role, name) for name in (
    'DOCUMENT', 'PARAGRAPH', 'SECTION', 'REGION', 'ARTICLE', 'FRAME', 'INTERNALFRAME',
    'GROUPING', 'TEXTFRAME', 'STATICTEXT',
) if hasattr(Role, name))
_LAYOUT_ROLES = frozenset(getattr(Role, name) for name in (
    'TABLE', 'TABLECELL', 'TABLEROW', 'COLUMNHEADER', 'ROWHEADER',
) if hasattr(Role, name))


def isBlankLine(info):
    """Empty text is insufficient: preserve controls, images and real tables."""
    if info.text.strip():
        return False
    try:
        layoutTable = 0
        for item in info.getTextWithFields():
            if isinstance(item, str):
                if item.strip():
                    return False
                continue
            if item.command == 'controlEnd':
                if layoutTable:
                    layoutTable -= 1
                continue
            if item.command != 'controlStart':
                continue
            field = item.field
            role = field.get('role')
            if layoutTable:
                layoutTable += 1
                if role in _CONTENT_ROLES and role not in _LAYOUT_ROLES:
                    return False
                if role == getattr(Role, 'TABLE', None) and not field.get('table-layout'):
                    return False
                if role not in _CONTAINER_ROLES and role not in _LAYOUT_ROLES and State.FOCUSABLE in field.get('states', ()):
                    return False
                continue
            if role == getattr(Role, 'TABLE', None) and field.get('table-layout'):
                layoutTable = 1
                continue
            if role in _CONTENT_ROLES:
                return False
            if role not in _CONTAINER_ROLES and State.FOCUSABLE in field.get('states', ()):
                return False
        return True
    except Exception:
        # Unknown provider data is preserved rather than silently skipped.
        return False


@dataclass
class _Search:
    document: object
    originalInfo: object
    probe: object
    focus: object
    direction: int
    fallback: object
    skipped: bool = False


class SkipBlankLines:
    def __init__(self, enabled, clock=None):
        self._enabled = enabled
        self._clock = clock or time.perf_counter
        self._active = True
        self._pending = None
        self._original = cursorManager.CursorManager._caretMovementScriptHelper
        original = self._original

        @wraps(original)
        def move(document, gesture, unit, direction=None, posConstant=textInfos.POSITION_SELECTION,
                 posUnit=None, posUnitEnd=False, extraDetail=False, handleSymbols=False):
            def fallback():
                return original(document, gesture, unit, direction, posConstant,
                                posUnit, posUnitEnd, extraDetail, handleSymbols)
            if not self._active or not self._enabled():
                return fallback()
            self._pending = None
            search = None
            try:
                bypass = (unit != textInfos.UNIT_LINE or direction not in (-1, 1)
                        or posConstant != textInfos.POSITION_SELECTION or posUnit is not None
                        or posUnitEnd or extraDetail or handleSymbols
                        or gesture.mainKeyName.lower() not in ('uparrow', 'downarrow')
                        or gesture.modifierNames or document.passThrough
                        or isinstance(document, textInfos.DocumentWithPageTurns)
                        or not isWebDocument(document)
                        or scriptHandler.isScriptWaiting() or scriptHandler.willSayAllResume(gesture)
                        or api.isTypingProtected())
                if not bypass:
                    info = document.makeTextInfo(textInfos.POSITION_SELECTION)
                    if info.isCollapsed:
                        # Verify comparison support before taking over a key.
                        info.compareEndPoints(info, 'startToStart')
                        probe = info.copy()
                        probe.expand(textInfos.UNIT_LINE)
                        probe.collapse()
                        search = _Search(document, info, probe, api.getFocusObject(), direction, fallback)
            except Exception:
                search = None
            if search is None:
                return fallback()
            self._pending = search
            return self._advance(search)

        self._wrapper = move
        cursorManager.CursorManager._caretMovementScriptHelper = move
        inputCore.decide_executeGesture.register(self._onGesture)

    def _onGesture(self, gesture):
        # This can run on the keyboard thread. Invalidation only; no NVDA
        # objects, config or TextInfo access from that thread.
        if self._pending is not None:
            self._pending = None
        return True

    def applySettings(self):
        if not self._enabled():
            self._pending = None

    def _valid(self, search):
        if self._pending is not search or not self._active or not self._enabled():
            return False
        try:
            if (search.document.passThrough or api.isTypingProtected()
                    or api.getFocusObject() != search.focus):
                return False
            current = search.document.makeTextInfo(textInfos.POSITION_SELECTION)
            return (current.compareEndPoints(search.originalInfo, 'startToStart') == 0
                    and current.compareEndPoints(search.originalInfo, 'endToEnd') == 0)
        except Exception:
            return False

    def _advance(self, search):
        if not self._valid(search):
            if self._pending is search:
                self._pending = None
            return
        started = self._clock()
        try:
            for step in range(SLICE_STEPS):
                if self._pending is not search:
                    return
                previous = search.probe.copy()
                moved = search.probe.move(textInfos.UNIT_LINE, search.direction)
                if moved == 0 or search.probe.compareEndPoints(previous, 'startToStart') == 0:
                    self._pending = None
                    if search.skipped:
                        ui.message(_("No more non-blank lines in this direction."))
                        return
                    return search.fallback()
                line = search.probe.copy()
                line.expand(textInfos.UNIT_LINE)
                if not isBlankLine(line):
                    if not self._valid(search):
                        if self._pending is search:
                            self._pending = None
                        return
                    # Like NVDA, announce before setting selection: moving
                    # focus can change a dynamic document's text offsets.
                    self._pending = None
                    speech.speakTextInfo(line, unit=textInfos.UNIT_LINE, reason=OutputReason.CARET)
                    search.document.selection = search.probe.copy()
                    return
                search.skipped = True
                if self._clock() - started >= SLICE_SECONDS:
                    break
        except Exception:
            if self._pending is search:
                self._pending = None
                return search.fallback()
            return
        # A new key/focus/caret change invalidates the next slice. Retain just
        # the current range, not the contents of previously examined lines.
        if self._pending is search:
            core.callLater(0, self._advance, search)

    def terminate(self):
        self._active = False
        self._pending = None
        inputCore.decide_executeGesture.unregister(self._onGesture)
        if cursorManager.CursorManager._caretMovementScriptHelper is self._wrapper:
            cursorManager.CursorManager._caretMovementScriptHelper = self._original
