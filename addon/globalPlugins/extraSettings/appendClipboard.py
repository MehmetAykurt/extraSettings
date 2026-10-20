"""Append an accessible text selection without issuing Ctrl+C."""
from ctypes import get_last_error, set_last_error

import addonHandler
import api
import gui
import textInfos
import ui
import winUser

from .clipboardAnnouncements import _enumClipboardFormats, _hasClipboardFormat

addonHandler.initTranslation()


class NonTextClipboard(ValueError):
    pass


def selectedText():
    """Query on NVDA's main thread, including browse-mode selections."""
    try:
        protected = api.isTypingProtected()
        focus = api.getFocusObject()
    except Exception:
        return None
    if protected:
        return None
    containers = []
    try:
        containers.append(api.getCaretObject())
    except Exception:
        pass
    if not any(item is focus for item in containers):
        containers.append(focus)
    known = False
    for container in containers:
        try:
            container.invalidateCache()
        except Exception:
            pass
        try:
            selection = container.makeTextInfo(textInfos.POSITION_SELECTION)
            if selection.isCollapsed:
                known = True
                continue
            text = selection.text
            if isinstance(text, str):
                if text:
                    return text
                known = True
        except Exception:
            continue
    return "" if known else None


def combinedText(previous, selection):
    if not previous:
        return selection
    separator = "" if previous.endswith(("\r", "\n")) or selection.startswith(("\r", "\n")) else "\r\n"
    return previous + separator + selection


def appendText(selection):
    """Read and write under one clipboard lock; never emulate Copy."""
    with winUser.openClipboard(gui.mainFrame.Handle):
        if _hasClipboardFormat(winUser.CF_UNICODETEXT):
            previous = winUser.getClipboardData(winUser.CF_UNICODETEXT)
        else:
            set_last_error(0)
            firstFormat = _enumClipboardFormats(0)
            if firstFormat:
                raise NonTextClipboard
            if get_last_error():
                raise OSError("Clipboard enumeration failed")
            previous = ""
        combined = combinedText(previous, selection)
        winUser.emptyClipboard()
        try:
            winUser.setClipboardData(winUser.CF_UNICODETEXT, combined)
        except OSError:
            # Retain the old plain text if writing the combined text fails.
            if previous:
                try:
                    winUser.setClipboardData(winUser.CF_UNICODETEXT, previous)
                except OSError:
                    pass
            raise
        if winUser.getClipboardData(winUser.CF_UNICODETEXT) != combined:
            raise OSError("Clipboard verification failed")


def appendSelection():
    text = selectedText()
    if text is None:
        ui.message(_("The selected text could not be read."))
        return
    if not text:
        ui.message(_("No selected text was found to append."))
        return
    try:
        appendText(text)
    except NonTextClipboard:
        ui.message(_("The clipboard contains non-text data. Copy text first."))
    except OSError:
        ui.message(_("Could not append to the clipboard."))
    else:
        ui.message(_("Added to clipboard."))
