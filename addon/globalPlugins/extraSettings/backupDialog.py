"""Keyboard-accessible selection and asynchronous configuration export."""
from pathlib import Path
import threading

import addonHandler
import config
import globalVars
import ui
import wx
from gui import guiHelper

from .backup import BackupCanceled, EmptyBackup, documentsDirectory, exportBackup

addonHandler.initTranslation()

try:
    from gui.nvdaControls import CustomCheckListBox as CheckList
except ImportError:
    CheckList = wx.CheckListBox


class BackupDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=_("Export NVDA settings"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._busy = False
        self._closed = False
        self._cancel = threading.Event()
        self._configPath = Path(globalVars.appArgs.configPath).resolve()
        self._settings = [
            ("settings", _("NVDA settings")),
            ("profiles", _("Configuration profiles")),
            ("gestures", _("Custom input gestures")),
            ("dictionaries", _("Speech dictionaries")),
            ("bookmarks", _("Extra Settings bookmarks")),
            ("quickFind", _("Extra Settings Quick find locations")),
        ]
        self._addons = []
        for addon in addonHandler.getAvailableAddons():
            if getattr(addon, "isPendingRemove", False):
                continue
            path = Path(addon.path)
            if path.parent.resolve() != self._configPath / "addons":
                continue
            self._addons.append((path.name, str(addon.manifest["summary"])))
        self._addons.sort(key=lambda item: item[1].casefold())
        outer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=outer)
        self.settingsList = helper.addLabeledControl(
            _("Settings to include"), CheckList, choices=[label for key, label in self._settings],
            size=(600, 170), style=wx.LB_SINGLE | wx.LB_HSCROLL)
        for index, (key, label) in enumerate(self._settings):
            self.settingsList.Check(index, key != "settings")
        self.settingsList.SetSelection(0)
        self.addonsList = helper.addLabeledControl(
            _("Installed add-ons"), CheckList, choices=[label for key, label in self._addons],
            size=(600, 200), style=wx.LB_SINGLE | wx.LB_HSCROLL)
        if self._addons:
            self.addonsList.SetSelection(0)
        else:
            self.addonsList.Enable(False)
        note = helper.addItem(wx.StaticText(self, label=_(
            "Selected add-on installation folders are included. Settings stored elsewhere "
            "are not included by selecting an add-on. Selected files may contain API keys or other personal data.")))
        note.Wrap(600)
        self.status = helper.addItem(wx.StaticText(self, label=""))
        self.okButton = helper.addItem(wx.Button(self, wx.ID_OK, label=_("OK")))
        self.cancelButton = helper.addItem(wx.Button(self, wx.ID_CANCEL, label=_("Cancel")))
        self.okButton.SetDefault()
        for control in (self.settingsList, self.addonsList):
            control.Bind(wx.EVT_CHECKLISTBOX, self.onCheck)
        self.okButton.Bind(wx.EVT_BUTTON, self.onExport)
        self.cancelButton.Bind(wx.EVT_BUTTON, self.onCancel)
        self.Bind(wx.EVT_CLOSE, self.onCancel)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.onDestroy)
        self.SetSizerAndFit(outer)
        self.SetMinSize(self.GetSize())
        self.CentreOnParent()

    def onCheck(self, event):
        control = event.GetEventObject()
        index = event.GetInt()
        if 0 <= index < control.GetCount():
            ui.message(_("Checked") if control.IsChecked(index) else _("Not checked"))

    def onExport(self, event):
        if self._busy:
            return
        settings = [key for index, (key, label) in enumerate(self._settings)
                    if self.settingsList.IsChecked(index)]
        addons = [key for index, (key, label) in enumerate(self._addons)
                  if self.addonsList.IsChecked(index)]
        if not settings and not addons:
            ui.message(_("Select at least one setting or add-on."))
            self.settingsList.SetFocus()
            return
        try:
            # Save NVDA's applied configuration before reading files.
            # Pending changes in the parent settings panel require Apply.
            if "settings" in settings or "profiles" in settings:
                config.conf.save()
        except Exception:
            ui.message(_("NVDA settings could not be saved. Export was not started."))
            return
        self._cancel.clear()
        self._busy = True
        self.settingsList.Enable(False)
        self.addonsList.Enable(False)
        self.okButton.Enable(False)
        self.status.SetLabel(_("Creating backup..."))
        self.Layout()
        try:
            threading.Thread(target=self._worker, args=(settings, addons),
                             name="extraSettings-backup", daemon=True).start()
        except RuntimeError:
            self._done(None, "error")
            return
        ui.message(_("Creating backup..."))

    def _worker(self, settings, addons):
        try:
            path = exportBackup(self._configPath, documentsDirectory(), settings, addons, self._cancel)
            error = None
        except BackupCanceled:
            path, error = None, "canceled"
        except EmptyBackup:
            path, error = None, "empty"
        except Exception:
            path, error = None, "error"
        try:
            wx.CallAfter(self._done, path, error)
        except RuntimeError:
            pass

    def _done(self, path, error):
        if self._closed:
            return
        self._busy = False
        self.settingsList.Enable(True)
        self.addonsList.Enable(bool(self._addons))
        self.okButton.Enable(True)
        if path is not None:
            message = _("Backup saved: {path}").format(path=path)
        elif error == "canceled":
            message = _("Backup canceled.")
        elif error == "empty":
            message = _("No files were found for the selected items.")
        else:
            message = _("The backup could not be created. No complete ZIP was saved. Please try again.")
        self.status.SetLabel(message)
        self.Layout()
        ui.message(message)
        self.cancelButton.SetFocus()

    def onCancel(self, event):
        if self._busy:
            self._cancel.set()
            self.status.SetLabel(_("Canceling backup..."))
            self.Layout()
            ui.message(_("Canceling backup..."))
            return
        self._closed = True
        self.EndModal(wx.ID_CANCEL)

    def onDestroy(self, event):
        if event.GetEventObject() is self:
            self._closed = True
            self._cancel.set()
        event.Skip()
