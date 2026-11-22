"""Import a chosen backup and request a clean NVDA restart on completion."""
from pathlib import Path
import threading

import addonHandler
import config
import globalVars
import ui
import wx
from gui import guiHelper

from .backup import BackupCanceled, ImportRollbackError, importBackup

addonHandler.initTranslation()


class TransferDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=_("Import / export"))
        self.action = None
        outer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=outer)
        self.choices = helper.addItem(wx.RadioBox(
            self, label=_("Operation"),
            choices=[_("Export settings"), _("Import settings")],
            majorDimension=1, style=wx.RA_SPECIFY_COLS))
        self.choices.SetSelection(0)
        ok = helper.addItem(wx.Button(self, wx.ID_OK, label=_("OK")))
        helper.addItem(wx.Button(self, wx.ID_CANCEL, label=_("Cancel")))
        ok.SetDefault()
        ok.Bind(wx.EVT_BUTTON, self.onOK)
        self.SetSizerAndFit(outer)
        self.CentreOnParent()

    def onOK(self, event):
        self.action = "export" if self.choices.GetSelection() == 0 else "import"
        self.EndModal(wx.ID_OK)


class ImportDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title=_("Import NVDA settings"))
        self._busy = False
        self._closed = False
        self._cancel = threading.Event()
        self._configPath = Path(globalVars.appArgs.configPath).resolve()
        outer = wx.BoxSizer(wx.VERTICAL)
        helper = guiHelper.BoxSizerHelper(self, sizer=outer)
        self.path = helper.addLabeledControl(_("Backup ZIP file"), wx.TextCtrl, value="", size=(550, -1))
        self.browseButton = helper.addItem(wx.Button(self, label=_("Browse...")))
        self.browseButton.Bind(wx.EVT_BUTTON, self.onBrowse)
        note = helper.addItem(wx.StaticText(self, label=_(
            "Files in the ZIP replace matching settings and add-on files automatically. "
            "NVDA restarts after a successful import. Pending changes in the settings window are discarded.")))
        note.Wrap(550)
        self.status = helper.addItem(wx.StaticText(self, label=""))
        self.okButton = helper.addItem(wx.Button(self, wx.ID_OK, label=_("OK")))
        self.cancelButton = helper.addItem(wx.Button(self, wx.ID_CANCEL, label=_("Cancel")))
        self.okButton.SetDefault()
        self.okButton.Bind(wx.EVT_BUTTON, self.onImport)
        self.cancelButton.Bind(wx.EVT_BUTTON, self.onCancel)
        self.Bind(wx.EVT_CLOSE, self.onCancel)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.onDestroy)
        self.SetSizerAndFit(outer)
        self.SetMinSize(self.GetSize())
        self.CentreOnParent()

    def onBrowse(self, event):
        dialog = wx.FileDialog(self, message=_("Choose an NVDA backup"),
                               wildcard=_("ZIP files (*.zip)|*.zip"),
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.path.SetValue(dialog.GetPath())
        finally:
            dialog.Destroy()
        self.path.SetFocus()

    def onImport(self, event):
        if self._busy:
            return
        filename = self.path.GetValue().strip().strip('"')
        if not filename:
            ui.message(_("Choose a backup ZIP file first."))
            self.path.SetFocus()
            return
        self._cancel.clear()
        self._busy = True
        for control in (self.path, self.browseButton, self.okButton):
            control.Enable(False)
        self.status.SetLabel(_("Importing backup..."))
        self.Layout()
        try:
            threading.Thread(target=self._worker, args=(filename,),
                             name="extraSettings-import", daemon=True).start()
        except RuntimeError:
            self._done(None, "error", None)
            return
        ui.message(_("Importing backup..."))

    def _worker(self, filename):
        count, error, recovery = None, None, None
        try:
            count = importBackup(self._configPath, filename, self._cancel)
        except BackupCanceled:
            error = "canceled"
        except ImportRollbackError as failure:
            error, recovery = "recovery", failure.recoveryPath
        except ValueError:
            error = "invalid"
        except Exception:
            error = "error"
        try:
            wx.CallAfter(self._done, count, error, recovery)
        except RuntimeError:
            pass

    def _done(self, count, error, recovery):
        if self._closed:
            return
        self._busy = False
        if count is not None:
            # Prevent shutdown from overwriting imported files with the
            # configuration currently cached in memory. This is not saved.
            config.conf["general"]["saveConfigurationOnExit"] = False
            ui.message(_("Backup imported. NVDA is restarting."))
            self._closed = True
            self.EndModal(wx.ID_OK)
            return
        for control in (self.path, self.browseButton, self.okButton):
            control.Enable(True)
        if error == "canceled":
            message = _("Import canceled. Previous files were restored.")
        elif error == "recovery":
            message = _("Import failed. Some files could not be restored. Recovery files are in: {path}").format(path=recovery)
        elif error == "invalid":
            message = _("This ZIP is not a supported NVDA backup. No changes were applied.")
        else:
            message = _("The backup could not be imported. Previous files were restored. Check file access and try again.")
        self.status.SetLabel(message)
        self.Layout()
        ui.message(message)
        self.cancelButton.SetFocus()

    def onCancel(self, event):
        if self._busy:
            self._cancel.set()
            self.status.SetLabel(_("Canceling import..."))
            self.Layout()
            ui.message(_("Canceling import..."))
            return
        self._closed = True
        self.EndModal(wx.ID_CANCEL)

    def onDestroy(self, event):
        if event.GetEventObject() is self:
            self._closed = True
            self._cancel.set()
        event.Skip()
