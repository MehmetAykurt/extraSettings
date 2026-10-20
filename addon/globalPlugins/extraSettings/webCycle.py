"""Wrap NVDA quick navigation, preserving its commands and reporting logic.

Audited against NVDA 2026.2 browseMode.py. No keyboard bindings are added.
Only iterators used by the current quick-nav call are temporarily decorated;
element lists, table-cell navigation and other iterator callers are unaffected.
"""
from contextlib import contextmanager
from functools import wraps
from collections import deque

import addonHandler
import browseMode
import textInfos
import ui
from controlTypes import OutputReason

addonHandler.initTranslation()


def _announceWrap(direction):
    # Translators: Quick navigation has wrapped past the last/first match.
    message = _("Wrapped to beginning.") if direction == "next" else _("Wrapped to end.")
    ui.message(message)


def _fromDocumentEdge(factory, direction):
    """Use the backend's document-order enumeration for BOTH directions.

    pos=None means enumerate the document, not necessarily search backwards
    from its end. Some backends ignore direction in this mode; others return
    no backwards results. Take the last forward match for reverse wrapping.
    Keep only one item in memory, and never move the caret while searching.
    """
    items = iter(factory("next", None))
    if direction == "next":
        first = next(items, None)
        if first is not None:
            yield first
    else:
        last = deque(items, maxlen=1)
        if last:
            yield last[0]


def isWebDocument(document):
    """Recognise web accessibility backends, including embedded web views."""
    prefixes = (
        "virtualBuffers.gecko_ia2", "virtualBuffers.MSHTML",
        "NVDAObjects.IAccessible.ia2Web", "NVDAObjects.IAccessible.MSHTML",
        "NVDAObjects.UIA.web",
    )
    root = document.rootNVDAObject
    return any(
        cls.__module__.startswith(prefixes)
        for obj in (document, root)
        for cls in type(obj).__mro__
    )


@contextmanager
def cyclingIterators(document, onWrap):
    """Wrap only an exhausted outermost search, at most once per command."""
    depth = 0
    missing = object()
    saved = {}
    restored = False

    def restore():
        nonlocal restored
        if restored:
            return
        restored = True
        for name, previous in saved.items():
            if previous is missing:
                document.__dict__.pop(name, None)
            else:
                setattr(document, name, previous)

    def search(initial, restart):
        nonlocal depth
        outermost = depth == 0
        wrapped = False
        depth += 1
        try:
            iterator = iter(initial())
            try:
                first = next(iterator)
            except StopIteration:
                if outermost:
                    iterator = iter(restart())
                    try:
                        first = next(iterator)
                        wrapped = True
                    except StopIteration:
                        return
                else:
                    return
            if outermost:
                # NVDA reports and moves AFTER obtaining its first result.
                # Restore now: focus/report code may query the same iterators
                # for unrelated reasons, and must never receive wrapping.
                restore()
                if wrapped:
                    onWrap()
                yield first
                return
            yield first
            yield from iterator
        finally:
            if outermost:
                restore()
            depth -= 1

    originalNodes = document._iterNodesByType
    originalBlocks = document._iterNotLinkBlock
    originalParagraphs = document._iterSimilarParagraph

    def nodes(itemType, direction="next", pos=None):
        if direction not in ("next", "previous"):
            return originalNodes(itemType, direction, pos)
        return search(
            lambda: originalNodes(itemType, direction, pos),
            lambda: _fromDocumentEdge(lambda searchDirection, pos: originalNodes(
                itemType, searchDirection, pos,
            ), direction),
        )

    def blocks(direction="next", pos=None):
        return search(
            lambda: originalBlocks(direction, pos),
            lambda: _fromDocumentEdge(originalBlocks, direction),
        )

    def paragraphs(kind, paragraphFunction, desiredValue, direction, pos):
        def restart():
            # Unlike semantic node iterators, paragraph iterators require a
            # TextInfo, and exclude the paragraph at the starting position.
            # Include the boundary paragraph explicitly so paragraph 1 is
            # not skipped, even when it starts at document offset zero.
            target = desiredValue
            if target is None:
                current = pos.copy()
                current.collapse()
                current.expand(textInfos.UNIT_PARAGRAPH)
                target = paragraphFunction(current)
            boundary = document.makeTextInfo(
                textInfos.POSITION_FIRST if direction == "next" else textInfos.POSITION_LAST,
            )
            boundary.expand(textInfos.UNIT_PARAGRAPH)
            if not boundary.isCollapsed and paragraphFunction(boundary) == target:
                yield browseMode.TextInfoQuickNavItem(
                    kind, document, boundary.copy(), outputReason=OutputReason.CARET,
                )
            yield from originalParagraphs(kind, paragraphFunction, target, direction, boundary)

        return search(
            lambda: originalParagraphs(kind, paragraphFunction, desiredValue, direction, pos),
            restart,
        )

    replacements = {
        "_iterNodesByType": nodes,
        "_iterNotLinkBlock": blocks,
        "_iterSimilarParagraph": paragraphs,
    }
    try:
        for name, replacement in replacements.items():
            saved[name] = document.__dict__.get(name, missing)
            setattr(document, name, replacement)
        yield
    finally:
        restore()


class WebCycle:
    def __init__(self, isEnabled):
        self._enabled = isEnabled
        self._active = True
        self._original = browseMode.BrowseModeTreeInterceptor._quickNavScript
        original = self._original

        @wraps(original)
        def quickNav(document, gesture, itemType, direction, errorMessage, readUnit):
            if (
                not self._active or not self._enabled()
                or direction not in ("next", "previous")
                or document.passThrough or not isWebDocument(document)
            ):
                return original(document, gesture, itemType, direction, errorMessage, readUnit)
            with cyclingIterators(document, lambda: _announceWrap(direction)):
                return original(document, gesture, itemType, direction, errorMessage, readUnit)

        self._wrapper = quickNav
        browseMode.BrowseModeTreeInterceptor._quickNavScript = quickNav

    def terminate(self):
        self._active = False
        if browseMode.BrowseModeTreeInterceptor._quickNavScript is self._wrapper:
            browseMode.BrowseModeTreeInterceptor._quickNavScript = self._original
