"""Accessible, keyboard-operated view of a memory-only speech snapshot."""
import addonHandler
import api
import ui
import wx

from gui import guiHelper

addonHandler.initTranslation()


class SpeechHistoryDialog(wx.Dialog):
    def __init__(self, parent, history):
        super().__init__(parent, title=_("Speech history"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.history = history
        self._closed = False
        outer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=outer)
        self.rows = helper.addLabeledControl(
            _("History entries"), wx.ListBox,
            choices=[], size=(650, 340), style=wx.LB_SINGLE | wx.LB_HSCROLL,
        )
        self.emptyMessage = helper.addItem(wx.StaticText(self, label=_("There are no history entries yet.")))
        self.copyButton = helper.addItem(wx.Button(self, label=_("Copy selected entry")))
        self.copyAllButton = helper.addItem(wx.Button(self, label=_("Copy all")))
        self.clearButton = helper.addItem(wx.Button(self, label=_("Clear history")))
        self.closeButton = helper.addItem(wx.Button(self, wx.ID_CANCEL, label=_("Close")))
        self.copyButton.Bind(wx.EVT_BUTTON, self.onCopy)
        self.copyAllButton.Bind(wx.EVT_BUTTON, self.onCopyAll)
        self.clearButton.Bind(wx.EVT_BUTTON, self.onClear)
        self.closeButton.Bind(wx.EVT_BUTTON, lambda event: self.Close())
        self.Bind(wx.EVT_CLOSE, self.onClose)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.onDestroy)
        self.Bind(wx.EVT_ACTIVATE, self.onActivate)
        self.rows.Bind(wx.EVT_CHAR_HOOK, self.onListKey)
        self.SetSizerAndFit(outer)
        self.SetMinSize(self.GetSize())
        self.CentreOnScreen()
        self.refresh()

    def refresh(self):
        self.entries = self.history.entries()
        self.rows.Set(self.entries)
        populated = bool(self.entries)
        self.rows.Enable(populated)
        self.emptyMessage.Show(not populated)
        self.copyButton.Enable(populated)
        self.copyAllButton.Enable(populated)
        self.clearButton.Enable(populated)
        self.Layout()
        if populated:
            newest = 0 if self.history._settings()["historyOrder"] == "newestFirst" else len(self.entries) - 1
            self.rows.SetSelection(newest)

    def focusNewest(self):
        if self.entries:
            self.rows.SetFocus()
        else:
            self.closeButton.SetFocus()

    def onActivate(self, event):
        self.history.viewActivated(event.GetActive())
        if event.GetActive():
            self.refresh()
        event.Skip()

    def onListKey(self, event):
        if event.ControlDown() and not event.AltDown() and not event.ShiftDown() and event.GetKeyCode() == ord("C"):
            self.onCopy(None)
        else:
            event.Skip()

    def _copy(self, text):
        if api.copyToClip(text):
            ui.message(_("Copied."))
        else:
            ui.message(_("Could not copy to the clipboard."))

    def onCopy(self, event):
        index = self.rows.GetSelection()
        if index != wx.NOT_FOUND:
            self._copy(self.entries[index])

    def onCopyAll(self, event):
        if self.entries:
            self._copy("\r\n".join(self.entries))

    def onClear(self, event):
        self.history.buffer.clear()
        self.refresh()
        self.closeButton.SetFocus()
        ui.message(_("Speech history cleared."))

    def _notifyClosed(self):
        if not self._closed:
            self._closed = True
            self.history.viewClosed()

    def onClose(self, event):
        self._notifyClosed()
        self.Destroy()

    def onDestroy(self, event):
        if event.GetEventObject() is self:
            self._notifyClosed()
        event.Skip()
