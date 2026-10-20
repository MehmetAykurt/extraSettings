"""On-demand bookmark window lifecycle. No speech or keyboard hooks."""
import addonHandler
import globalVars
import gui
import ui
from .bookmarkStore import BookmarkStore, BookmarkError
from .storage import dataPath

addonHandler.initTranslation()


def errorMessage(code):
    return {
        "name": _("Enter a bookmark name."),
        "url": _("Enter a complete http or https web address."),
        "target": _("Enter a folder path or web address."),
        "folder": _("Enter an absolute folder path, such as C:\\Documents."),
        "duplicate": _("This location is already bookmarked."),
        "changed": _("Bookmarks changed elsewhere. Close and reopen the list."),
        "damaged": _("The bookmarks file is damaged or unsupported. It has not been changed."),
        "read": _("Bookmarks could not be read."),
        "write": _("Bookmarks could not be saved. Your previous records have been kept."),
    }.get(code, _("The bookmark could not be opened. Check its address or folder path."))


class Bookmarks:
    def __init__(self, enabled):
        self._enabled = enabled
        self._active = True
        self._dialog = None

    def show(self):
        if not self._active:
            return
        if not self._enabled():
            ui.message(_("Enable bookmarks in Extra Settings first."))
            return
        if self._dialog is not None:
            self._dialog.Raise(); self._dialog.focusList()
            return
        from .bookmarkContext import currentLocation
        try:
            location = currentLocation()
        except Exception:
            location = None
        try:
            store = BookmarkStore(dataPath(globalVars.appArgs.configPath, "extraSettingsBookmarks.json"))
            store.load()
        except BookmarkError as error:
            ui.message(errorMessage(str(error)))
            return
        from .bookmarksDialog import BookmarksDialog
        gui.mainFrame.prePopup()
        try:
            self._dialog = BookmarksDialog(gui.mainFrame, self, store, location)
            self._dialog.Show(); self._dialog.Raise(); self._dialog.focusList()
        finally:
            gui.mainFrame.postPopup()

    def applySettings(self):
        if not self._enabled() and self._dialog is not None:
            self._dialog.Close()

    def viewClosed(self):
        self._dialog = None

    def terminate(self):
        self._active = False
        if self._dialog is not None:
            self._dialog.Destroy()
            self._dialog = None
