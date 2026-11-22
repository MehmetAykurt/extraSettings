"""Record everything NVDA sends to the synthesizer, without replacing any speech function."""
import addonHandler
import api
import gui
import ui
from logHandler import log
from speech.extensions import pre_speech

from .historyBuffer import HistoryBuffer

addonHandler.initTranslation()


class SpeechHistory:
    def __init__(self, settings):
        self._settings = settings
        self.buffer = HistoryBuffer(settings()["historyLimit"])
        self._active = True
        self._viewActive = False
        self._dialog = None
        # pre_speech fires for what is really handed to the synthesizer
        # (menus, notifications, focus changes, typing, reading), so history
        # matches what the user hears. Speech canceled before it is sent is
        # never heard and therefore never recorded.
        pre_speech.register(self._onSpeech)

    def _onSpeech(self, *args, **kwargs):
        speechSequence = kwargs.get("speechSequence", args[0] if args else None)
        if speechSequence is None or not self._active or self._viewActive:
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

    def _syncBuffer(self):
        """Apply the current limit and recording state to the buffer only."""
        settings = self._settings()
        self.buffer.resize(settings["historyLimit"])
        if not settings["recordHistory"]:
            self.buffer.clear()

    def _callDialog(self, method):
        """Call a wx method on the dialog; False if wx already deleted it."""
        if self._dialog is None:
            return False
        try:
            getattr(self._dialog, method)()
        except RuntimeError:
            # The user already closed the window: wx has deleted the C++ object.
            log.debug("ExtraSettings: speech history dialog was already destroyed")
            self._dialog = None
            self._viewActive = False
            return False
        return True

    def applySettings(self):
        self._syncBuffer()
        if not self._settings()["recordHistory"]:
            self._callDialog("Close")

    def entries(self):
        # Refreshing a dialog must never close that same dialog, so only the
        # buffer is synchronised here; applySettings() handles the window.
        self._syncBuffer()
        return self.buffer.snapshot(newestFirst=self._settings()["historyOrder"] == "newestFirst")

    def entryItems(self):
        """Like entries(), but as (id, text) pairs so the dialog can track marks."""
        self._syncBuffer()
        return self.buffer.snapshotItems(newestFirst=self._settings()["historyOrder"] == "newestFirst")

    def show(self):
        if not self._active:
            return
        if not self._settings()["recordHistory"]:
            ui.message(_("Enable speech history in Extra Settings first."))
            return
        # If the window was already gone, fall through and open a new one.
        if self._dialog is not None and self._callDialog("Raise") and self._callDialog("SetFocus"):
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
            log.error("ExtraSettings: speech history could not be opened", exc_info=True)
            self._dialog = None
            self._viewActive = False
            ui.message(_("Could not open speech history."))

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
        pre_speech.unregister(self._onSpeech)
        self._callDialog("Destroy")
        self._dialog = None
        self._viewActive = False
        self.buffer.clear()
