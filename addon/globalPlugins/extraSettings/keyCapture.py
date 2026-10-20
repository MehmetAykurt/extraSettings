"""Short-lived modal capture, following NVDA's Input Gestures captor."""
import addonHandler
import inputCore
import ui
import wx
from gui import guiHelper

from .applicationsKey import capturedIdentifier

addonHandler.initTranslation()


def capturePrompt():
    return _("Please press the shortcut you want to use instead of the Applications key. Press Escape to cancel.")


class KeyCaptureDialog(wx.Dialog):
    def __init__(self, parent, prompt=None):
        super().__init__(parent, title=_("Set key"))
        self.identifier = None
        self._finished = False
        self._captor = self._capture
        self._prompt = prompt or capturePrompt()
        sizer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=sizer)
        helper.addItem(wx.StaticText(self, label=self._prompt))
        self.cancelButton = helper.addItem(wx.Button(self, wx.ID_CANCEL, label=_("Cancel")))
        self.cancelButton.Bind(wx.EVT_BUTTON, self.onCancel)
        self.Bind(wx.EVT_CLOSE, self.onCancel)
        self.Bind(wx.EVT_ACTIVATE, self.onActivate)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.onDestroy)
        self.SetSizerAndFit(sizer)

    def beginCapture(self):
        if inputCore.manager._captureFunc:
            return False
        inputCore.manager._captureFunc = self._captor
        wx.CallAfter(self._announce)
        return True

    def _announce(self):
        if self and not self.IsBeingDeleted() and self.IsModal() and not self._finished:
            ui.message(self._prompt)

    def stopCapture(self):
        # Do not clear a captor installed by another component in the meantime.
        if inputCore.manager._captureFunc is self._captor:
            inputCore.manager._captureFunc = None

    def _capture(self, gesture):
        if self._finished:
            return False
        identifier = capturedIdentifier(gesture)
        if identifier is None:
            return False
        self._finished = True
        self.stopCapture()
        wx.CallAfter(self._finish, None if identifier == "kb:escape" else identifier)
        return False

    def _finish(self, identifier):
        if not self or self.IsBeingDeleted():
            return
        self.identifier = identifier
        self.EndModal(wx.ID_OK if identifier else wx.ID_CANCEL)

    def onCancel(self, event):
        self._finished = True
        self.stopCapture()
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()

    def onActivate(self, event):
        if not event.GetActive() and self.IsModal() and not self._finished:
            self.onCancel(event)
        event.Skip()

    def onDestroy(self, event):
        if event.GetEventObject() is self:
            self.stopCapture()
        event.Skip()
