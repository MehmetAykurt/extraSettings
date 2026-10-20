"""Observe queued NVDA speech without replacing any speech function."""
import addonHandler
import api
import gui
import ui
from speech.extensions import pre_speechQueued

from .historyBuffer import HistoryBuffer

addonHandler.initTranslation()


class SpeechHistory:
    def __init__(self, settings):
        self._settings = settings
        self.buffer = HistoryBuffer(settings()["historyLimit"])
        self._active = True
        self._viewActive = False
        self._dialog = None
        pre_speechQueued.register(self._onSpeech)

    def _onSpeech(self, speechSequence, **kwargs):
        if not self._active or self._viewActive:
            return
        settings = self._settings()
        if not settings["recordHistory"]:
            self.buffer.clear()
            return
        # Follow NVDA's protected typing policy without inspecting the text.
        if api.isTypingProtected():
            return
        self.buffer.resize(settings["historyLimit"])
        self.buffer.append(speechSequence)

    def applySettings(self):
        settings = self._settings()
        self.buffer.resize(settings["historyLimit"])
        if not settings["recordHistory"]:
            self.buffer.clear()
            if self._dialog is not None:
                self._dialog.Close()

    def entries(self):
        self.applySettings()
        return self.buffer.snapshot(newestFirst=self._settings()["historyOrder"] == "newestFirst")

    def show(self):
        if not self._active:
            return
        if not self._settings()["recordHistory"]:
            ui.message(_("Enable speech history in Extra Settings first."))
            return
        if self._dialog is not None:
            self._dialog.Raise()
            self._dialog.SetFocus()
            return
        # Freeze history before creating controls, so opening announcements
        # and spoken history rows never feed back into the same buffer.
        self._viewActive = True
        try:
            from .historyDialog import SpeechHistoryDialog
            gui.mainFrame.prePopup()
            try:
                self._dialog = SpeechHistoryDialog(gui.mainFrame, self)
                self._dialog.Show()
                self._dialog.Raise()
                self._dialog.focusNewest()
            finally:
                gui.mainFrame.postPopup()
        except Exception:
            self._dialog = None
            self._viewActive = False
            raise

    def viewActivated(self, active):
        self._viewActive = bool(active)

    @property
    def isViewing(self):
        return self._viewActive

    def viewClosed(self):
        self._dialog = None
        self._viewActive = False

    def terminate(self):
        if not self._active:
            return
        self._active = False
        pre_speechQueued.unregister(self._onSpeech)
        if self._dialog is not None:
            self._dialog.Destroy()
            self._dialog = None
        self._viewActive = False
        self.buffer.clear()
