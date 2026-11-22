"""Accessible, keyboard-operated view of a memory-only speech snapshot."""
import addonHandler
import api
import ui
import wx

from gui import guiHelper
from logHandler import log

from .historySelection import ENTRY_SEPARATOR, MarkedEntries, buildCopyText, copyCount

addonHandler.initTranslation()

# Read names directly from the stored text. Native checklist names may contain
# a second state description, in addition to the accessible checked state.
try:
    from gui.nvdaControls import CustomCheckListBox, ListCtrlAccessible

    class _HistoryListAccessible(ListCtrlAccessible):
        def GetName(self, childId):
            if 1 <= childId <= self.Window.GetCount():
                return wx.ACC_OK, self.Window.GetString(childId - 1)
            return super().GetName(childId)

    class _CheckList(CustomCheckListBox):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.SetAccessible(_HistoryListAccessible(self))

    _NATIVE_STATE_EVENTS = True
except ImportError:
    log.debug("ExtraSettings: CustomCheckListBox is unavailable; using wx.CheckListBox", exc_info=True)
    _CheckList = wx.CheckListBox
    _NATIVE_STATE_EVENTS = False


class SpeechHistoryDialog(wx.Dialog):
    def __init__(self, parent, history):
        super().__init__(parent, title=_("Speech history"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.history = history
        self.items = []
        self.marked = MarkedEntries()
        self._closed = False
        outer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=outer)
        # A check list: Space marks or unmarks the focused entry. NVDA reads
        # the checked state, and the order of marking decides the copy order.
        self.rows = helper.addLabeledControl(
            _("History entries"), _CheckList,
            choices=[], size=(650, 340), style=wx.LB_SINGLE | wx.LB_HSCROLL,
        )
        self.emptyMessage = helper.addItem(wx.StaticText(self, label=_("There are no history entries yet.")))
        self.copyButton = helper.addItem(wx.Button(self, label=_("Copy selected entries")))
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
        self.rows.Bind(wx.EVT_CHECKLISTBOX, self.onCheck)
        self.SetSizerAndFit(outer)
        self.SetMinSize(self.GetSize())
        self.CentreOnScreen()
        self.refresh()

    def refresh(self):
        self.items = self.history.entryItems()
        self.rows.Set([text for entryId, text in self.items])
        # Marks follow entries by id, so they survive new entries shifting the
        # rows; entries evicted by the line limit lose their mark.
        self.marked.prune({entryId for entryId, text in self.items})
        for index, (entryId, text) in enumerate(self.items):
            if entryId in self.marked:
                self.rows.Check(index, True)
        populated = bool(self.items)
        self.rows.Enable(populated)
        self.emptyMessage.Show(not populated)
        self.copyButton.Enable(populated)
        self.copyAllButton.Enable(populated)
        self.clearButton.Enable(populated)
        self.Layout()
        if populated:
            newest = 0 if self.history._settings()["historyOrder"] == "newestFirst" else len(self.items) - 1
            self.rows.SetSelection(newest)

    def focusNewest(self):
        if self.items:
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

    def onCheck(self, event):
        index = event.GetInt()
        if 0 <= index < len(self.items):
            checked = self.rows.IsChecked(index)
            self.marked.setMarked(self.items[index][0], checked)
            if not _NATIVE_STATE_EVENTS:
                ui.message(_("Checked") if checked else _("Not checked"))
        # Allow CustomCheckListBox.notifyIAccessible to emit STATECHANGE.
        # Without Skip, binding this handler hides the control's own handler.
        event.Skip()

    def _focusedId(self):
        index = self.rows.GetSelection()
        if index == wx.NOT_FOUND or not 0 <= index < len(self.items):
            return None
        return self.items[index][0]

    def _copy(self, text, count=1):
        if api.copyToClip(text):
            if count > 1:
                ui.message(_("{count} entries copied.").format(count=count))
            else:
                ui.message(_("Copied."))
        else:
            ui.message(_("Could not copy to the clipboard."))

    def onCopy(self, event):
        # Marked entries are copied in the order they were marked; with no
        # marks the focused entry is copied, as before.
        focusedId = self._focusedId()
        text = buildCopyText(self.items, self.marked, focusedId)
        if text is not None:
            self._copy(text, copyCount(self.items, self.marked, focusedId))

    def onCopyAll(self, event):
        if self.items:
            self._copy(ENTRY_SEPARATOR.join(text for entryId, text in self.items), len(self.items))

    def onClear(self, event):
        self.history.buffer.clear()
        self.marked.clear()
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
