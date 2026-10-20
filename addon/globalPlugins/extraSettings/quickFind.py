"""On-demand saved positions and document-specific shortcut resolution."""
from collections.abc import Mapping
import threading
import time
import addonHandler
import api
import core
import globalVars
import gui
import inputCore
import scriptHandler
import textInfos
import ui
import wx
from logHandler import log
from .bookmarkStore import BookmarkError
from .quickFindStore import QuickFindStore, validShortcut
from .storage import dataPath
from .quickFindAnchor import resolve, occurrences, neighbours
from .quickFindText import currentContext, snapshot, captureLine, findOccurrence, moveCaret, positionOffset, QuickFindError

addonHandler.initTranslation()


def failureMessage(code):
    return {
        "protected": _("Protected fields cannot be saved in Quick find."),
        "unsupported": _("Quick find is not available in this document or view."),
        "empty": _("Move to a non-blank text line first."),
        "large": _("Quick find supports documents up to 1000000 characters and lines up to 1000 characters."),
        "capture": _("The current line could not be saved. Return to the document and try again."),
        "matches": _("There are too many matching locations. Use a more distinctive line."),
        "missing": _("The saved location was not found in this document."),
        "invalid": _("Enter a name and a valid shortcut, or leave the shortcut unassigned."),
        "shortcut": _("This shortcut is already assigned in this document."),
        "conflict": _("This shortcut is already used by NVDA or another add-on. Choose another shortcut."),
        "duplicate": _("This location is already saved in Quick find."),
        "changed": _("Quick find records changed elsewhere. Close and reopen the list."),
        "damaged": _("The Quick find file is damaged or unsupported. It has not been changed."),
        "read": _("Quick find records could not be read."),
        "write": _("Quick find records could not be saved. Your previous records have been kept."),
        "moved": _("The document or caret changed. Run Quick find again."),
    }.get(code, _("Quick find could not complete the operation."))


def _infos(value):
    if hasattr(value, "gestures"):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _infos(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _infos(item)


def shortcutConflict(identifier):
    if not identifier:
        return False
    normalized = inputCore.normalizeGestureIdentifier(identifier)
    for info in _infos(inputCore.manager.getAllGestureMappings()):
        if any(inputCore.normalizeGestureIdentifier(gesture) == normalized for gesture in info.gestures):
            return True
    import config
    settings = config.conf["extraSettings"]
    return bool(settings["replaceApplicationsKey"] and settings["applicationsGesture"]
                and inputCore.normalizeGestureIdentifier(settings["applicationsGesture"]) == normalized)


class QuickFind:
    def __init__(self, enabled):
        self._enabled = enabled
        self._active = True
        self._dialog = None
        self._store = None
        self._byGesture = {}
        self._serial = 0
        self._busy = False
        self._loadError = None
        self._emulating = False
        self.applySettings()

    def _load(self):
        store = QuickFindStore(dataPath(globalVars.appArgs.configPath, "extraSettingsQuickFind.json"))
        store.load()
        self._store = store
        self.refreshBindings()

    def refreshBindings(self):
        # This is an internal lookup, never a replacement of NVDA key bindings.
        self._byGesture = {}
        for entry in self._store.entries:
            if entry["shortcut"]:
                key = inputCore.normalizeGestureIdentifier(entry["shortcut"])
                self._byGesture.setdefault(key, []).append(entry)

    def applySettings(self):
        if not self._enabled():
            self._serial += 1
            self._busy = False
            self._byGesture.clear()
            if self._dialog is not None:
                self._dialog.Close()
            return
        try:
            self._load()
            self._loadError = None
        except BookmarkError as error:
            self._byGesture.clear()
            self._loadError = str(error)

    def getScript(self, gesture):
        if not self._active or self._emulating or not self._enabled() or self._dialog is not None:
            return None
        entries = []
        for identifier in gesture.normalizedIdentifiers:
            if identifier in self._byGesture:
                entries = self._byGesture[identifier]
                break
        if not entries:
            return None
        log.debug("ExtraSettings Quick find: saved shortcut queued")
        def navigate(gesture):
            self._dispatchShortcut(gesture, entries)
        navigate.__name__ = "script_quickFindLocation"
        navigate.__doc__ = _("Go to a saved location in the current document")
        return navigate

    def _dispatchShortcut(self, gesture, entries):
        # Script lookup may run on the keyboard input thread. Accessing a
        # browser's COM/UIA document there can fail even though the same
        # document is accessible from NVDA's main thread. Resolve it only
        # when the queued script actually executes, as list navigation does.
        entry = None
        if self._active and self._enabled() and self._dialog is None:
            try:
                context = currentContext()
                entry = next((entry for entry in entries if entry["docKey"] == context.key
                              and any(saved["id"] == entry["id"] and saved["shortcut"] == entry["shortcut"]
                                      for saved in self._store.entries)), None)
            except QuickFindError as error:
                log.debug("ExtraSettings Quick find: shortcut context unavailable (%s)", str(error))
        if entry is not None:
            log.debug("ExtraSettings Quick find: shortcut document matched")
            self.jump(entry)
            return
        log.debug("ExtraSettings Quick find: shortcut forwarded to normal handling")
        # Preserve the normal script/native action outside the saved document.
        # The guard lets normal NVDA resolution run without selecting us again.
        self._emulating = True
        try:
            # InputGesture.script caches the first lookup on the gesture itself.
            # Reusing that cached Quick find script would queue us indefinitely,
            # even though the guard is active during emulateGesture. Resolve the
            # normal action again while this controller is excluded from lookup.
            gesture.script = scriptHandler.findScript(gesture)
            inputCore.manager.emulateGesture(gesture)
        finally:
            self._emulating = False

    def show(self):
        if not self._active:
            return
        if not self._enabled():
            ui.message(_("Enable Quick find in Extra Settings first."))
            return
        if self._dialog is not None:
            self._dialog.Raise(); self._dialog.focusList()
            return
        try:
            self._load()
            context = currentContext()
            source = snapshot(context)
            try:
                anchor = captureLine(context, source)
                captureError = None
            except QuickFindError as error:
                anchor, captureError = None, str(error)
        except (BookmarkError, QuickFindError) as error:
            ui.message(failureMessage(str(error)))
            return
        from .quickFindDialog import QuickFindDialog
        gui.mainFrame.prePopup()
        try:
            self._dialog = QuickFindDialog(gui.mainFrame, self, context, anchor, captureError)
            self._dialog.Show(); self._dialog.Raise(); self._dialog.focusList()
        finally:
            gui.mainFrame.postPopup()

    def viewClosed(self):
        self._dialog = None

    def fromDialog(self, entry, context):
        if self._dialog is not None:
            self._dialog.Close()
        try:
            context.focus.setFocus()
        except Exception:
            try:
                getattr(context.container, "rootNVDAObject", context.container).setFocus()
            except Exception:
                ui.message(failureMessage("moved"))
                return
        self._serial += 1
        serial = self._serial
        def afterFocus():
            if self._active and self._enabled() and serial == self._serial:
                self.jump(entry)
        core.callLater(120, afterFocus)

    def jump(self, entry):
        if not self._active or not self._enabled():
            return
        if self._busy:
            ui.message(_("Quick find is already searching."))
            return
        try:
            context = currentContext()
            if context.key != entry["docKey"]:
                raise QuickFindError("moved")
            source = snapshot(context)
            caret = context.container.makeTextInfo(textInfos.POSITION_CARET)
        except QuickFindError as error:
            ui.message(failureMessage(str(error)))
            return
        except Exception:
            ui.message(failureMessage("unsupported"))
            return
        self._serial += 1
        serial = self._serial
        self._busy = True
        def worker():
            try:
                choices, error = resolve(source, entry), None
            except ValueError as failure:
                choices, error = [], str(failure)
            try:
                wx.CallAfter(self._resolved, serial, context, caret, source, entry, choices, error)
            except RuntimeError:
                pass
        threading.Thread(target=worker, name="extraSettings-quickFind", daemon=True).start()

    def _valid(self, serial, context, caret):
        if not self._active or not self._enabled() or serial != self._serial:
            return False
        try:
            current = currentContext()
            return (current.key == context.key
                    and current.foreground.windowHandle == context.foreground.windowHandle
                    and current.container.makeTextInfo(textInfos.POSITION_CARET).compareEndPoints(caret, "startToStart") == 0)
        except Exception:
            return False

    def _resolved(self, serial, context, caret, source, entry, choices, error):
        if not self._valid(serial, context, caret):
            if serial == self._serial:
                self._busy = False
                log.debug("ExtraSettings Quick find: navigation canceled after context/caret changed")
            return
        if error or not choices:
            self._busy = False
            ui.message(failureMessage(error or "missing"))
            return
        if len(choices) > 1:
            self._busy = False
            from .quickFindDialog import chooseMatch
            chosen = chooseMatch(context, source, entry, choices)
            if chosen is None or not self._active or not self._enabled() or serial != self._serial:
                return
            # The modal selector has closed; restore the document before rechecking.
            try:
                context.focus.setFocus()
            except Exception:
                ui.message(failureMessage("moved"))
                return
            self._busy = True
            core.callLater(120, self._beginNative, serial, context, caret, source, entry, chosen)
            return
        self._beginNative(serial, context, caret, source, entry, choices[0])

    def _beginNative(self, serial, context, caret, source, entry, ordinal):
        if not self._valid(serial, context, caret):
            if serial == self._serial:
                self._busy = False
            return
        iterator = findOccurrence(context, entry["text"], ordinal)
        self._stepNative(serial, context, caret, source, entry, ordinal, iterator)

    def _stepNative(self, serial, context, caret, source, entry, ordinal, iterator):
        if not self._valid(serial, context, caret):
            if serial == self._serial:
                self._busy = False
            return
        deadline = time.monotonic() + .008
        try:
            for _ in range(32):
                try:
                    next(iterator)
                except StopIteration as finished:
                    info = finished.value
                    # Verify the document has not changed since the snapshot.
                    if snapshot(context) != source:
                        raise QuickFindError("moved")
                    expected = occurrences(source, entry["text"])[ordinal]
                    if positionOffset(context, info) != expected:
                        raise QuickFindError("moved")
                    self._busy = False
                    ui.message(moveCaret(context, info) or entry["name"])
                    log.debug("ExtraSettings Quick find: caret moved to verified location")
                    return
                if time.monotonic() >= deadline:
                    break
            core.callLater(1, self._stepNative, serial, context, caret, source, entry, ordinal, iterator)
        except Exception as error:
            self._busy = False
            ui.message(failureMessage(str(error) if isinstance(error, QuickFindError) else "unsupported"))

    def terminate(self):
        self._active = False
        self._serial += 1
        self._busy = False
        self._byGesture.clear()
        if self._dialog is not None:
            self._dialog.Destroy()
            self._dialog = None
