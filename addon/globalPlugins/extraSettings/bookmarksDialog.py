"""Accessible bookmark list and name/address editor."""
import os
import threading
import addonHandler
import ui
import wx
from gui import guiHelper
from .bookmarkStore import BookmarkError
from .bookmarks import errorMessage

addonHandler.initTranslation()


def searchKey(value):
    return value.casefold().replace("\u0307", "").replace("ı", "i")


class BookmarkEditor(wx.Dialog):
    def __init__(self, parent, store, entry=None, editing=False):
        super().__init__(parent, title=_("Edit bookmark") if editing else _("Add bookmark"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.store = store
        self.entryId = entry["id"] if editing else None
        self.savedId = None
        entry = entry or {}
        outer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=outer)
        self.name = helper.addLabeledControl(_("Bookmark name"), wx.TextCtrl, value=entry.get("name", ""), size=(520, -1))
        self.kind = helper.addLabeledControl(_("Location type"), wx.Choice, choices=[_("Folder"), _("Web page")])
        self.kind.SetSelection(1 if entry.get("kind") == "web" else 0)
        self.targetLabel = wx.StaticText(self, label=self._targetLabel())
        self.target = wx.TextCtrl(self, value=entry.get("target", ""))
        helper.addItem(guiHelper.associateElements(self.targetLabel, self.target))
        self.kind.Bind(wx.EVT_CHOICE, self.onKindChanged)
        browse = helper.addItem(wx.Button(self, label=_("Browse for folder")))
        browse.Bind(wx.EVT_BUTTON, self.onBrowse)
        save = helper.addItem(wx.Button(self, wx.ID_OK, label=_("Save bookmark")))
        save.Bind(wx.EVT_BUTTON, self.onSave)
        save.SetDefault()
        helper.addItem(wx.Button(self, wx.ID_CANCEL, label=_("Cancel")))
        self.SetSizerAndFit(outer)
        self.SetMinSize(self.GetSize())
        self.CentreOnParent()
        self.name.SetFocus()

    def _targetLabel(self):
        return _("Web address") if self.kind.GetSelection() == 1 else _("Folder path")

    def onKindChanged(self, event):
        self.targetLabel.SetLabel(self._targetLabel())
        self.Layout()

    def onBrowse(self, event):
        dialog = wx.DirDialog(self, message=_("Choose a folder"), style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.kind.SetSelection(0)
                self.onKindChanged(None)
                self.target.SetValue(dialog.GetPath())
        finally:
            dialog.Destroy()

    def onSave(self, event):
        try:
            self.savedId = self.store.save(self.name.GetValue(), "web" if self.kind.GetSelection() == 1 else "folder",
                                          self.target.GetValue(), self.entryId)
        except BookmarkError as error:
            wx.MessageBox(errorMessage(str(error)), _("Bookmarks"), wx.OK | wx.ICON_ERROR, self)
            (self.name if str(error) == "name" else self.target).SetFocus()
            return
        self.EndModal(wx.ID_OK)


class BookmarksDialog(wx.Dialog):
    def __init__(self, parent, controller, store, location):
        super().__init__(parent, title=_("Bookmarks"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.controller = controller
        self.store = store
        self.location = location
        self._closed = False
        self._opening = False
        outer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=outer)
        self.search = helper.addLabeledControl(_("Search bookmarks"), wx.TextCtrl)
        self.rows = helper.addLabeledControl(_("Saved bookmarks"), wx.ListBox, choices=[], size=(650, 280),
                                            style=wx.LB_SINGLE | wx.LB_HSCROLL)
        self.details = helper.addLabeledControl(_("Selected location"), wx.TextCtrl, value="", style=wx.TE_READONLY)
        self.empty = helper.addItem(wx.StaticText(self, label=_("There are no matching bookmarks.")))
        self.openButton = helper.addItem(wx.Button(self, label=_("Open bookmark")))
        self.currentButton = helper.addItem(wx.Button(self, label=_("Add current location")))
        self.addButton = helper.addItem(wx.Button(self, label=_("Add bookmark")))
        self.editButton = helper.addItem(wx.Button(self, label=_("Edit bookmark")))
        self.removeButton = helper.addItem(wx.Button(self, label=_("Remove bookmark")))
        self.closeButton = helper.addItem(wx.Button(self, wx.ID_CANCEL, label=_("Close")))
        self.openButton.Bind(wx.EVT_BUTTON, self.onOpen)
        self.currentButton.Bind(wx.EVT_BUTTON, self.onCurrent)
        self.addButton.Bind(wx.EVT_BUTTON, lambda event: self.edit())
        self.editButton.Bind(wx.EVT_BUTTON, self.onEdit)
        self.removeButton.Bind(wx.EVT_BUTTON, self.onRemove)
        self.closeButton.Bind(wx.EVT_BUTTON, lambda event: self.Close())
        self.search.Bind(wx.EVT_TEXT, self.onSearch)
        self.rows.Bind(wx.EVT_LISTBOX, self.onSelection)
        self.rows.Bind(wx.EVT_LISTBOX_DCLICK, self.onOpen)
        self.rows.Bind(wx.EVT_CHAR_HOOK, self.onListKey)
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
        query = searchKey(self.search.GetValue())
        self.visible = sorted((entry for entry in self.store.entries
                               if query in searchKey(entry["name"] + " " + entry["target"])),
                              key=lambda entry: searchKey(entry["name"]))
        self.rows.Set(["{name} — {kind}".format(name=e["name"], kind=_("Folder") if e["kind"] == "folder" else _("Web page")) for e in self.visible])
        if self.visible:
            self.rows.SetSelection(next((i for i, e in enumerate(self.visible) if e["id"] == selectedId), 0))
        self.empty.Show(not bool(self.visible))
        self.onSelection(None)
        self.Layout()

    def onSearch(self, event):
        self.refresh()

    def onSelection(self, event):
        entry = self.selected()
        self.details.ChangeValue(entry["target"] if entry else "")
        self.openButton.Enable(bool(entry) and not self._opening)
        self.editButton.Enable(bool(entry))
        self.removeButton.Enable(bool(entry))

    def focusList(self):
        (self.rows if self.visible else self.addButton).SetFocus()

    def edit(self, entry=None, editing=False):
        dialog = BookmarkEditor(self, self.store, entry, editing)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.search.ChangeValue("")
                self.refresh(dialog.savedId)
                ui.message(_("Bookmark saved."))
        finally:
            dialog.Destroy()
        self.focusList()

    def onCurrent(self, event):
        if self.location is None:
            ui.message(_("The current location could not be detected. Enter its name and address manually."))
        self.edit(self.location)

    def onEdit(self, event):
        entry = self.selected()
        if entry:
            self.edit(entry, editing=True)

    def onRemove(self, event):
        entry = self.selected()
        if not entry:
            return
        try:
            self.store.remove(entry["id"])
        except BookmarkError as error:
            ui.message(errorMessage(str(error)))
            return
        self.refresh()
        self.focusList()
        ui.message(_("Bookmark removed."))

    def onListKey(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not (event.ControlDown() or event.AltDown() or event.ShiftDown()):
            self.onOpen(None)
        elif event.GetKeyCode() == wx.WXK_DELETE and not (event.ControlDown() or event.AltDown() or event.ShiftDown()):
            self.onRemove(None)
        else:
            event.Skip()

    def onOpen(self, event):
        entry = self.selected()
        if not entry or self._opening:
            return
        if entry["kind"] == "web":
            self._launch(entry["target"], True)
            return
        self._opening = True
        self.onSelection(None)
        # Network folders can be slow. Never block NVDA's main thread on stat.
        target = entry["target"]
        def check():
            try:
                exists = os.path.isdir(target)
            except OSError:
                exists = False
            try:
                wx.CallAfter(self._launch, target, exists)
            except RuntimeError:
                pass
        threading.Thread(target=check, name="extraSettings-bookmark", daemon=True).start()

    def _launch(self, target, exists):
        if self._closed:
            return
        self._opening = False
        self.onSelection(None)
        if not exists:
            ui.message(_("The folder is unavailable. It may have been moved, deleted or disconnected."))
            return
        try:
            os.startfile(target)
        except OSError:
            ui.message(errorMessage("open"))
            return
        self.Close()

    def onClose(self, event):
        self._notifyClosed()
        self.Destroy()

    def _notifyClosed(self):
        if not self._closed:
            self._closed = True
            self.controller.viewClosed()

    def onDestroy(self, event):
        if event.GetEventObject() is self:
            self._notifyClosed()
        event.Skip()
