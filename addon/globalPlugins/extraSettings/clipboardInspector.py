"""Open the current clipboard on demand without monitoring or modifying it."""
import threading

import addonHandler
import api
import ui
import wx

from .clipboardData import ClipboardTooLarge, makePreview, readSnapshot

addonHandler.initTranslation()


class ClipboardInspector:
    def __init__(self, enabled):
        self._enabled = enabled
        self._active = True
        self._generation = 0
        self._pending = None

    def show(self):
        if not self._active:
            return
        if not self._enabled():
            ui.message(_("Enable clipboard inspection in Extra Settings first."))
            return
        if self._pending is not None:
            ui.message(_("Clipboard inspection is already in progress."))
            return
        token = object()
        self._pending = token
        worker = threading.Thread(target=self._read, args=(token, self._generation),
                                  name='extraSettings-clipboardInspector', daemon=True)
        try:
            worker.start()
        except Exception:
            self._pending = None
            ui.message(_("The clipboard could not be read. Please try again."))

    def _read(self, token, generation):
        try:
            result = makePreview(readSnapshot())
            error = None
        except ClipboardTooLarge:
            result = None
            error = _("The clipboard content is too large to inspect.")
        except Exception:
            result = None
            error = _("The clipboard could not be read. Please try again.")
        wx.CallAfter(self._done, token, generation, result, error)

    def _done(self, token, generation, result, error):
        if self._pending is not token:
            return
        self._pending = None
        if not self._active or generation != self._generation or not self._enabled():
            return
        if api.isTypingProtected():
            return
        if error:
            ui.message(error)
            return
        ui.browseableMessage(result.content, title=_("Inspect clipboard"),
                             isHtml=result.isHtml, closeButton=True)

    def applySettings(self):
        if not self._enabled():
            self._generation += 1

    def terminate(self):
        self._active = False
        self._generation += 1
        self._pending = None
