"""Keyboard-only management of saved text locations and optional shortcuts."""
import re
import addonHandler
import gui
import inputCore
import ui
import wx
from gui import guiHelper
from .applicationsKey import shortcutLabel
from .bookmarkStore import BookmarkError
from .quickFindStore import validShortcut
from .quickFind import failureMessage, shortcutConflict
from .quickFindAnchor import occurrences, neighbours

addonHandler.initTranslation()


class KeyboardReadOnlyText(wx.TextCtrl):
    """Keep a single-line read-only value in Windows' Tab traversal."""
    def AcceptsFocusFromKeyboard(self):
        return True


class AnchorEditor(wx.Dialog):
    def __init__(self, parent, controller, entry, editing=False):
        super().__init__(parent, title=_("Edit saved location") if editing else _("Add current line"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.controller = controller
        self.entry = dict(entry)
        self.editing = editing
        self.savedId = None
        self.shortcut = entry["shortcut"]
        outer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=outer)
        self.name = helper.addLabeledControl(_("Location name"), wx.TextCtrl, value=entry["name"], size=(600, -1))
        helper.addLabeledControl(_("Saved line"), wx.TextCtrl, value=entry["text"], style=wx.TE_READONLY)
        self.shortcutValue = helper.addLabeledControl(_("Assigned shortcut"), KeyboardReadOnlyText,
            value=shortcutLabel(self.shortcut), style=wx.TE_READONLY)
        self.keyButton = helper.addItem(wx.Button(self, label=self._keyButtonLabel()))
        self.keyButton.Bind(wx.EVT_BUTTON, self.onKey)
        self.clearButton = helper.addItem(wx.Button(self, label=_("Remove assigned shortcut")))
        self.clearButton.Bind(wx.EVT_BUTTON, self.onClear)
        self.clearButton.Show(bool(self.shortcut))
        save = helper.addItem(wx.Button(self, wx.ID_OK, label=_("Save location")))
        save.Bind(wx.EVT_BUTTON, self.onSave)
        save.SetDefault()
        helper.addItem(wx.Button(self, wx.ID_CANCEL, label=_("Cancel")))
        self.SetSizerAndFit(outer)
        self.SetMinSize(self.GetSize())
        self.CentreOnParent()
        self._updateShortcut()
        self.name.SetFocus()

    def _keyButtonLabel(self):
        return _("Change shortcut") if self.shortcut else _("Assign shortcut")

    def _updateShortcut(self):
        self.keyButton.SetLabel(self._keyButtonLabel())
        self.shortcutValue.ChangeValue(shortcutLabel(self.shortcut))
        self.clearButton.Show(bool(self.shortcut))
        self.Layout()
        self.Fit()

    def onKey(self, event):
        from .keyCapture import KeyCaptureDialog
        dialog = KeyCaptureDialog(self, prompt=_("Press the shortcut for this saved location. Press Escape to cancel."))
        error = None
        selected = None
        try:
            if not dialog.beginCapture():
                ui.message(_("Another key capture is already in progress."))
                return
            if dialog.ShowModal() == wx.ID_OK and dialog.identifier:
                identifier = inputCore.normalizeGestureIdentifier(dialog.identifier)
                if not validShortcut(identifier):
                    error = _("Use a letter, number or function key with NVDA, Alt or Control. Standard editing and navigation keys cannot be assigned.")
                elif shortcutConflict(identifier):
                    error = failureMessage("conflict")
                else:
                    selected = identifier
        finally:
            dialog.stopCapture()
            dialog.Destroy()
        if error:
            wx.MessageBox(error, _("Quick find"), wx.OK | wx.ICON_ERROR, self)
            self.keyButton.SetFocus()
        elif selected:
            self.shortcut = selected
            self._updateShortcut()
            self.shortcutValue.SetFocus()

    def onClear(self, event):
        self.shortcut = ""
        self.keyButton.SetFocus()
        self._updateShortcut()

    def onSave(self, event):
        entry = dict(self.entry, name=self.name.GetValue().strip(), shortcut=self.shortcut)
        try:
            if self.shortcut and shortcutConflict(self.shortcut):
                raise BookmarkError("conflict")
            self.savedId = self.controller._store.saveAnchor(entry, entry["id"] if self.editing else None)
        except BookmarkError as error:
            wx.MessageBox(failureMessage(str(error)), _("Quick find"), wx.OK | wx.ICON_ERROR, self)
            return
        self.controller.refreshBindings()
        self.EndModal(wx.ID_OK)


class QuickFindDialog(wx.Dialog):
    def __init__(self, parent, controller, context, anchor, captureError):
        super().__init__(parent, title=_("Quick find"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.controller, self.context = controller, context
        self.anchor, self.captureError = anchor, captureError
        self._closed = False
        outer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=outer)
        helper.addItem(wx.StaticText(self, label=_("Current document: {document}").format(document=context.title)))
        if context.sampled:
            helper.addItem(wx.StaticText(self, label=_("This document is identified by its title and text sample because its full path is unavailable.")))
        self.search = helper.addLabeledControl(_("Search saved locations"), wx.TextCtrl)
        self.rows = helper.addLabeledControl(_("Saved locations in this document"), wx.ListBox,
            choices=[], size=(700, 280), style=wx.LB_SINGLE | wx.LB_HSCROLL)
        self.line = helper.addLabeledControl(_("Saved line"), wx.TextCtrl, value="", style=wx.TE_READONLY)
        self.empty = helper.addItem(wx.StaticText(self, label=_("There are no matching saved locations in this document.")))
        self.goButton = helper.addItem(wx.Button(self, label=_("Go to location")))
        self.addButton = helper.addItem(wx.Button(self, label=_("Add current line")))
        self.editButton = helper.addItem(wx.Button(self, label=_("Edit saved location")))
        self.removeButton = helper.addItem(wx.Button(self, label=_("Remove saved location")))
        self.closeButton = helper.addItem(wx.Button(self, wx.ID_CANCEL, label=_("Close")))
        self.search.Bind(wx.EVT_TEXT, lambda event:self.refresh())
        self.rows.Bind(wx.EVT_LISTBOX, self.onSelection)
        self.rows.Bind(wx.EVT_LISTBOX_DCLICK, self.onGo)
        self.rows.Bind(wx.EVT_CHAR_HOOK, self.onListKey)
        self.goButton.Bind(wx.EVT_BUTTON, self.onGo)
        self.addButton.Bind(wx.EVT_BUTTON, self.onAdd)
        self.editButton.Bind(wx.EVT_BUTTON, self.onEdit)
        self.removeButton.Bind(wx.EVT_BUTTON, self.onRemove)
        self.closeButton.Bind(wx.EVT_BUTTON, lambda event:self.Close())
        self.Bind(wx.EVT_CLOSE, self.onClose)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.onDestroy)
        self.refresh()
        self.SetSizerAndFit(outer)
        self.SetMinSize(self.GetSize())
        self.CentreOnScreen()

    def selected(self):
        index = self.rows.GetSelection()
        return self.visible[index] if 0 <= index < len(self.visible) else None

    def refresh(self, selectedId=None):
        query = self.search.GetValue().casefold()
        self.visible = [e for e in self.controller._store.entries if e["docKey"] == self.context.key
                        and query in (e["name"] + " " + e["text"]).casefold()]
        labels = [_("{name} — line {line} — {shortcut}").format(name=e["name"], line=e.get("lineNumber", "?"),
                   shortcut=shortcutLabel(e["shortcut"])) for e in self.visible]
        self.rows.Set(labels)
        if self.visible:
            self.rows.SetSelection(next((i for i,e in enumerate(self.visible) if e["id"] == selectedId),0))
        self.empty.Show(not bool(self.visible))
        self.onSelection(None)
        self.Layout()

    def onSelection(self, event):
        selected = self.selected()
        self.line.ChangeValue(selected["text"] if selected else "")
        for button in (self.goButton, self.editButton, self.removeButton):
            button.Enable(bool(selected))

    def focusList(self):
        (self.rows if self.visible else self.addButton).SetFocus()

    def edit(self, entry, editing=False):
        dialog = AnchorEditor(self, self.controller, entry, editing)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.search.ChangeValue("")
                self.refresh(dialog.savedId)
                ui.message(_("Quick find location saved."))
        finally:
            dialog.Destroy()
        self.focusList()

    def onAdd(self, event):
        if self.anchor is None:
            ui.message(failureMessage(self.captureError or "capture"))
            return
        self.edit(self.anchor)

    def onEdit(self, event):
        entry = self.selected()
        if entry:
            self.edit(entry, True)

    def onRemove(self, event):
        entry = self.selected()
        if not entry:
            return
        try:
            self.controller._store.remove(entry["id"])
        except BookmarkError as error:
            ui.message(failureMessage(str(error)))
            return
        self.controller.refreshBindings()
        self.refresh(); self.focusList()
        ui.message(_("Quick find location removed."))

    def onGo(self, event):
        entry = self.selected()
        if entry:
            self.controller.fromDialog(dict(entry), self.context)

    def onListKey(self, event):
        if not (event.ControlDown() or event.AltDown() or event.ShiftDown()):
            if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
                self.onGo(None)
                return
            if event.GetKeyCode() == wx.WXK_DELETE:
                self.onRemove(None)
                return
        event.Skip()

    def _notifyClosed(self):
        if not self._closed:
            self._closed = True
            self.controller.viewClosed()

    def onClose(self, event):
        self._notifyClosed(); self.Destroy()

    def onDestroy(self, event):
        if event.GetEventObject() is self:
            self._notifyClosed()
        event.Skip()


def chooseMatch(context, source, entry, choices):
    positions = occurrences(source, entry["text"])
    labels = []
    for index in choices:
        offset = positions[index]
        before, after = neighbours(source, offset, len(entry["text"]))
        line = len(re.findall(r"\r\n|\r|\n",source[:offset])) + 1
        labels.append(_("Match {number}, line {line}: {before} [{text}] {after}").format(
            number=index+1,line=line,before=before[-80:],text=entry["text"][:100],after=after[:80]))
    gui.mainFrame.prePopup()
    dialog = None
    try:
        dialog = wx.SingleChoiceDialog(gui.mainFrame,
            _("Several locations match. Choose the intended location; no movement has been made."),
            _("Choose matching location"), labels)
        if dialog.ShowModal() == wx.ID_OK:
            selected = dialog.GetSelection()
            if 0 <= selected < len(choices):
                return choices[selected]
        return None
    finally:
        if dialog is not None:
            dialog.Destroy()
        gui.mainFrame.postPopup()
