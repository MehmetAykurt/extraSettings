"""Main-thread NVDA text access. Persistent records never contain COM objects."""
from dataclasses import dataclass
import ntpath
from urllib.parse import urlsplit, unquote
import api
import textInfos
from .quickFindAnchor import capture, clean, digest, MAX_DOCUMENT


class QuickFindError(Exception):
    pass


@dataclass
class DocumentContext:
    container: object
    focus: object
    foreground: object
    key: str
    title: str
    sampled: bool


def _value(obj, attribute, default=None):
    try:
        return getattr(obj, attribute, default)
    except Exception:
        return default


def _path(value):
    if not isinstance(value, str):
        return None
    value = value.strip().strip('"')
    if value.startswith("file:"):
        uri = urlsplit(value)
        value = unquote(uri.path).replace("/", "\\")
        value = ("\\\\" + uri.netloc + value) if uri.netloc else value.lstrip("\\")
    drive, tail = ntpath.splitdrive(value)
    if drive and (tail.startswith(("\\", "/")) or (drive.startswith("\\\\") and not tail)):
        return ntpath.normcase(ntpath.normpath(value))
    return None


def currentContext():
    try:
        if api.isTypingProtected():
            raise QuickFindError("protected")
        container = api.getCaretObject()
        focus = api.getFocusObject()
        foreground = api.getForegroundObject()
        if not container or _value(container, "isProtected", False):
            raise QuickFindError("protected")
        root = _value(container, "rootNVDAObject", container)
        app = _value(_value(focus, "appModule"), "appName", "").casefold()
        title = clean(_value(root, "name", "") or _value(foreground, "name", "") or app)
        for obj, attr in ((container, "documentConstantIdentifier"), (container, "documentURL"),
                          (root, "documentURL"), (root, "value")):
            address = _value(obj, attr)
            if isinstance(address, str):
                parsed = urlsplit(address)
                if parsed.scheme.casefold() in ("http", "https") and parsed.hostname:
                    # Preserve the path, query and fragment; only root / is equivalent.
                    key = parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), path=parsed.path or "/").geturl()
                    return DocumentContext(container, focus, foreground, "web:" + key, title or address, False)
                path = _path(address)
                if path:
                    return DocumentContext(container, focus, foreground, "file:" + path, title or path, False)
        if app not in ("notepad", "winword", "acrobat", "acrord32", "foxitreader", "foxitpdfreader", "sumatrapdf"):
            raise QuickFindError("unsupported")
        word = _value(root, "WinwordDocumentObject") or _value(focus, "WinwordDocumentObject")
        if word is not None:
            try:
                path = _path(word.FullName) if word.Path else None
                if path:
                    return DocumentContext(container, focus, foreground, "file:" + path, title, False)
            except Exception:
                pass
        # Some text providers expose a full path in the document description.
        for obj in (root, focus, foreground):
            for attr in ("description", "name"):
                path = _path(_value(obj, attr))
                if path:
                    return DocumentContext(container, focus, foreground, "file:" + path, title, False)
        # If the application hides its path, title alone is unsafe. Include
        # small text samples from BOTH ends, without reading the entire file.
        first = container.makeTextInfo(textInfos.POSITION_FIRST)
        first.move(textInfos.UNIT_CHARACTER, 128, endPoint="end")
        last = container.makeTextInfo(textInfos.POSITION_LAST)
        last.move(textInfos.UNIT_CHARACTER, -128, endPoint="start")
        sample = first.text + "\0" + last.text
        if not sample.strip("\0\r\n \t"):
            raise QuickFindError("empty")
        title = clean(_value(foreground, "name", "") or title).lstrip("* ")
        key = "sample:" + digest(app + "\0" + title + "\0" + sample)
        return DocumentContext(container, focus, foreground, key, title, True)
    except QuickFindError:
        raise
    except Exception:
        raise QuickFindError("unsupported") from None


def snapshot(context):
    try:
        source = context.container.makeTextInfo(textInfos.POSITION_ALL).text
        if not isinstance(source, str):
            raise QuickFindError("unsupported")
        if len(source) > MAX_DOCUMENT:
            raise QuickFindError("large")
        return source
    except QuickFindError:
        raise
    except Exception:
        raise QuickFindError("unsupported") from None


def captureLine(context, source):
    try:
        line = context.container.makeTextInfo(textInfos.POSITION_CARET)
        line.expand(textInfos.UNIT_LINE)
        obj = _value(line, "NVDAObjectAtStart")
        if _value(obj, "isProtected", False):
            raise QuickFindError("protected")
        term = line.text.strip()
        start = line.copy(); start.collapse()
        prefix = context.container.makeTextInfo(textInfos.POSITION_FIRST)
        prefix.setEndPoint(start, "endToStart")
        entry = capture(source, term, len(prefix.text), context.key, context.title)
        return entry
    except QuickFindError:
        raise
    except ValueError as error:
        raise QuickFindError(str(error)) from None
    except Exception:
        raise QuickFindError("capture") from None


def findOccurrence(context, term, ordinal):
    """Yield control between native searches; never move the real caret here."""
    info = context.container.makeTextInfo(textInfos.POSITION_FIRST)
    index = -1
    previous = None
    while True:
        prefix = info.copy()
        prefix.move(textInfos.UNIT_CHARACTER, len(term) + 1, endPoint="end")
        if prefix.text[:len(term)].casefold() != term.casefold():
            if not info.find(term, caseSensitive=False):
                raise QuickFindError("missing")
        if previous is not None and info.compareEndPoints(previous, "startToStart") <= 0:
            raise QuickFindError("unsupported")
        index += 1
        if index == ordinal:
            return info
        previous = info.copy()
        info.collapse()
        # Word find includes the start position; browse-mode find normally
        # excludes it. Advance explicitly and probe the new start first, so
        # adjacent matches and both provider behaviours work consistently.
        if not info.move(textInfos.UNIT_CHARACTER, 1):
            raise QuickFindError("missing")
        yield None


def positionOffset(context, info):
    prefix = context.container.makeTextInfo(textInfos.POSITION_FIRST)
    prefix.setEndPoint(info, "endToStart")
    return len(prefix.text)


def moveCaret(context, info):
    info.collapse()
    if hasattr(context.container, "passThrough") and hasattr(context.container, "_set_selection"):
        import controlTypes
        context.container.passThrough = False
        context.container._set_selection(info, reason=controlTypes.OutputReason.QUICKNAV)
    else:
        info.updateCaret()
        import review
        review.handleCaretMove(info)
        import braille
        braille.handler.handleCaretMove(context.container)
    line = info.copy()
    line.expand(textInfos.UNIT_LINE)
    return clean(line.text)
