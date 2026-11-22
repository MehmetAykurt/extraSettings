"""Announce selected item count in File Explorer when pressing Ctrl+A.

Gesture deciders run on the keyboard hook thread, so the gesture handler only
recognises Ctrl+A and defers everything else (focus inspection, COM and Win32
queries, speech) to NVDA's main thread.
"""
import ctypes
from ctypes import wintypes

import addonHandler
import api
import core
import inputCore
import keyboardHandler
import ui
from controlTypes import Role
from logHandler import log

addonHandler.initTranslation()

_DESKTOP_CLASSES = ("Progman", "WorkerW")
_LIST_ROLES = (Role.LIST, Role.TREEVIEW)
_LVM_GETITEMCOUNT = 0x1004
_LVM_GETSELECTEDCOUNT = 0x1032
_SMTO_ABORTIFHUNG = 0x0002
_SEND_TIMEOUT_MS = 300
_POLL_DELAY_MS = 150
_RETRY_DELAY_MS = 60
_MAX_RETRIES = 4

_user32Dll = None


def _user32():
    """Return a private user32 binding with explicit signatures.

    A private WinDLL keeps these signatures from affecting other code that
    uses ctypes.windll.user32.
    """
    global _user32Dll
    if _user32Dll is None:
        dll = ctypes.WinDLL("user32", use_last_error=True)
        dll.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
        ]
        dll.SendMessageTimeoutW.restype = ctypes.c_size_t
        dll.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        dll.FindWindowExW.argtypes = [
            wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ]
        dll.FindWindowExW.restype = wintypes.HWND
        dll.GetParent.argtypes = [wintypes.HWND]
        dll.GetParent.restype = wintypes.HWND
        dll.GetForegroundWindow.restype = wintypes.HWND
        dll.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        dll.GetAncestor.restype = wintypes.HWND
        _user32Dll = dll
    return _user32Dll


def _sendMessage(hwnd, message):
    """Send a message with a timeout so a hung window cannot freeze NVDA.

    Returns the message result, or None on timeout or failure.
    """
    try:
        result = ctypes.c_size_t(0)
        ok = _user32().SendMessageTimeoutW(
            hwnd, message, 0, 0, _SMTO_ABORTIFHUNG, _SEND_TIMEOUT_MS,
            ctypes.pointer(result),
        )
        if not ok:
            log.debug("ExplorerSelection: SendMessageTimeout timed out or failed")
            return None
        return int(result.value)
    except Exception:
        log.debug("ExplorerSelection: SendMessageTimeout raised", exc_info=True)
        return None


def _isDesktop(focus):
    """Check if focus is on the Windows Desktop."""
    if focus is None:
        return False
    try:
        root = getattr(focus, "rootNVDAObject", None)
        if root is not None and getattr(root, "windowClassName", "") in _DESKTOP_CLASSES:
            return True
        if (getattr(focus, "windowClassName", "") or "") in _DESKTOP_CLASSES:
            return True
        parent = getattr(focus, "parent", None)
        while parent is not None:
            if getattr(parent, "windowClassName", "") in _DESKTOP_CLASSES:
                return True
            parent = getattr(parent, "parent", None)
    except Exception:
        log.debug("ExplorerSelection: desktop detection failed", exc_info=True)
    return False


def _isExplorerFocus(focus):
    """Verify that focus is in File Explorer or Desktop, and not in an active text-editing field."""
    if focus is None:
        return False
    try:
        if api.isTypingProtected():
            return False
    except Exception:
        log.debug("ExplorerSelection: typing protection check failed", exc_info=True)
    app = getattr(focus, "appModule", None)
    appName = (getattr(app, "appName", "") or "").lower() if app else ""
    if appName != "explorer":
        return False

    role = getattr(focus, "role", None)
    className = getattr(focus, "windowClassName", "") or ""

    # Exclude active text editing (e.g. renaming a file with F2, typing in search box or address bar)
    if role in (Role.EDITABLETEXT, Role.PASSWORDEDIT) or className in ("Edit", "RichEdit20W", "RichEdit50W"):
        return False

    return True


def _findListContainer(focus):
    """Find the list or tree view container holding the items."""
    role = getattr(focus, "role", None)
    if role in _LIST_ROLES:
        return focus
    container = getattr(focus, "selectionContainer", None)
    if container is not None and getattr(container, "role", None) in _LIST_ROLES:
        return container
    parent = getattr(focus, "parent", None)
    if parent is not None and getattr(parent, "role", None) in _LIST_ROLES:
        return parent
    if container is not None:
        return container
    if parent is not None:
        return parent
    return focus


def _getSysListViewHwnd(focus):
    """Find the SysListView32 window handle for the desktop list."""
    if focus is None:
        return 0
    try:
        user32 = _user32()
        buf = ctypes.create_unicode_buffer(64)

        candidates = []
        for obj in (focus, getattr(focus, "parent", None), getattr(focus, "selectionContainer", None)):
            if obj is not None:
                h = getattr(obj, "windowHandle", 0)
                if h and h not in candidates:
                    candidates.append(h)

        for h in candidates:
            user32.GetClassNameW(h, buf, 64)
            if buf.value == "SysListView32":
                return h
            child = user32.FindWindowExW(h, 0, "SysListView32", None)
            if child:
                return child
            parent = user32.GetParent(h)
            if parent:
                user32.GetClassNameW(parent, buf, 64)
                if buf.value == "SysListView32":
                    return parent
    except Exception:
        log.debug("ExplorerSelection: SysListView32 lookup failed", exc_info=True)
    return 0


def _getDesktopSelection(focus):
    """Query selected items count on the Windows Desktop and in list fallbacks."""
    # 1. Authoritative Win32 message, with a timeout so a hung window cannot freeze NVDA.
    lvHwnd = _getSysListViewHwnd(focus)
    if lvHwnd:
        c = _sendMessage(lvHwnd, _LVM_GETSELECTEDCOUNT)
        if c is not None:
            log.debug(f"ExplorerSelection: LVM_GETSELECTEDCOUNT={c}")
            return c

    container = _findListContainer(focus)
    candidates = [container]
    if focus not in candidates and getattr(focus, "role", None) not in (Role.LISTITEM, Role.TREEVIEWITEM):
        candidates.append(focus)
    parent = getattr(focus, "parent", None)
    if parent is not None and parent not in candidates:
        candidates.append(parent)

    # 2. Try UIA SelectionPattern
    for obj in candidates:
        try:
            pattern = getattr(obj, "UIASelectionPattern", None) or getattr(obj, "UIASelectionPattern2", None)
            if pattern is not None:
                sel = pattern.GetCurrentSelection()
                if sel is not None:
                    return len(sel)
        except Exception:
            log.debug("ExplorerSelection: UIA selection pattern failed", exc_info=True)

    # 3. Fallback to NVDA's getSelectedItemsCount / selectedItems
    for obj in candidates:
        try:
            c = obj.getSelectedItemsCount()
            if c is not None and c >= 0:
                return c
        except Exception:
            log.debug("ExplorerSelection: getSelectedItemsCount failed", exc_info=True)
        try:
            items = getattr(obj, "selectedItems", None)
            if items is not None:
                return len(items)
        except Exception:
            log.debug("ExplorerSelection: selectedItems failed", exc_info=True)

    return None


def _containerTotal(focus):
    """Estimate the item total from NVDA position info or the container."""
    pos = getattr(focus, "positionInfo", None)
    if isinstance(pos, dict):
        total = pos.get("similarItemsInGroup")
        if total is not None and total >= 0:
            return total
    container = _findListContainer(focus)
    if container is not None:
        try:
            count = container.childCount
            if count is not None and count >= 0:
                return count
        except Exception:
            log.debug("ExplorerSelection: childCount failed", exc_info=True)
    return None


def _getDesktopTotal(focus):
    """Query total item count on the Windows Desktop."""
    lvHwnd = _getSysListViewHwnd(focus)
    if lvHwnd:
        total = _sendMessage(lvHwnd, _LVM_GETITEMCOUNT)
        if total is not None:
            return total
    return _containerTotal(focus)


def _getExplorerHwnds(focus):
    """Collect possible HWNDs representing the active File Explorer window."""
    hwnds = set()
    if focus is None:
        return hwnds
    try:
        user32 = _user32()
        fg = user32.GetForegroundWindow()
        if fg:
            hwnds.add(fg)
            hwnds.add(user32.GetAncestor(fg, 2))  # GA_ROOT
        h = getattr(focus, "windowHandle", 0)
        if h:
            hwnds.add(h)
            hwnds.add(user32.GetAncestor(h, 1))  # GA_PARENT
            hwnds.add(user32.GetAncestor(h, 2))  # GA_ROOT
            hwnds.add(user32.GetAncestor(h, 3))  # GA_ROOTOWNER
        root = getattr(focus, "rootNVDAObject", None)
        if root:
            rh = getattr(root, "windowHandle", 0)
            if rh:
                hwnds.add(rh)
                hwnds.add(user32.GetAncestor(rh, 2))
    except Exception:
        log.debug("ExplorerSelection: window handle collection failed", exc_info=True)
    hwnds.discard(0)
    return hwnds


def _documentCounts(window):
    """Return (selected, total) for one Shell window, using None when unknown."""
    selected = total = None
    doc = getattr(window, "Document", None)
    if doc is None:
        return selected, total
    try:
        sel = getattr(doc, "SelectedItems", None)
        if callable(sel):
            sel = sel()
        if sel is not None:
            selected = int(sel.Count)
    except Exception:
        log.debug("ExplorerSelection: Shell selection query failed", exc_info=True)
    try:
        folder = getattr(doc, "Folder", None)
        items = getattr(folder, "Items", None) if folder is not None else None
        if callable(items):
            items = items()
        if items is not None:
            total = int(items.Count)
    except Exception:
        log.debug("ExplorerSelection: Shell item count query failed", exc_info=True)
    return selected, total


def _shellFolderCounts(focus):
    """Ask Shell.Application once for (selected, total); None when unknown."""
    targetHwnds = _getExplorerHwnds(focus)
    try:
        import comtypes.client
        from comtypes import CoInitialize, CoUninitialize

        CoInitialize()
        try:
            shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
            windows = shell.Windows()
            match = None
            for window in windows:
                try:
                    if int(window.HWND) in targetHwnds:
                        match = window
                        break
                except Exception:
                    log.debug("ExplorerSelection: Shell window inspection failed", exc_info=True)
            # If no target HWND matched, accept the only open Explorer window.
            if match is None and windows.Count == 1:
                match = windows.Item(0)
            if match is None:
                return None, None
            return _documentCounts(match)
        finally:
            CoUninitialize()
    except Exception:
        log.debug("ExplorerSelection: Shell COM query failed", exc_info=True)
        return None, None


def _getCounts(focus):
    """Return (selected, total) for the folder or Desktop that has focus."""
    # On the Desktop never query Shell.Windows(), which only lists folder windows.
    if _isDesktop(focus):
        return _getDesktopSelection(focus), _getDesktopTotal(focus)
    selected, total = _shellFolderCounts(focus)
    if selected is None:
        selected = _getDesktopSelection(focus)
    if total is None:
        total = _containerTotal(focus)
    return selected, total


def _describe(focus):
    """Short description of the focus for the diagnostic log (no file names)."""
    app = getattr(focus, "appModule", None)
    return (
        f"app={getattr(app, 'appName', None)!r} role={getattr(focus, 'role', None)!r} "
        f"class={getattr(focus, 'windowClassName', None)!r}"
    )


class ExplorerSelection:
    def __init__(self, enabled):
        self._enabled = enabled
        self._active = True
        self._generation = 0
        inputCore.decide_executeGesture.register(self._onGesture)

    def applySettings(self):
        self._generation += 1

    def _isCtrlA(self, gesture):
        if not isinstance(gesture, keyboardHandler.KeyboardInputGesture):
            return False
        # Match Ctrl+A across all keyboard layouts, desktop/laptop modes, and normalized representations
        if any(ident == "kb:control+a" or ident.endswith(":control+a") for ident in getattr(gesture, "identifiers", ())):
            return True
        if any(ident == "kb:control+a" or ident.endswith(":control+a") for ident in getattr(gesture, "normalizedIdentifiers", ())):
            return True
        if getattr(gesture, "mainKeyName", "").lower() == "a":
            mods = {m.lower() for m in getattr(gesture, "modifierNames", ())}
            if mods == {"control"}:
                return True
        return False

    def _onGesture(self, gesture):
        # Runs on the keyboard hook thread: no NVDA object, COM or Win32
        # queries here. Recognise the key and defer to the main thread.
        if not self._active or not self._enabled():
            return True
        manager = inputCore.manager
        if manager.isInputHelpActive or getattr(manager, "_captureFunc", None):
            return True
        if not self._isCtrlA(gesture):
            return True
        # Never swallow Ctrl+A: return True so Windows processes it natively.
        self._generation += 1
        core.callLater(0, self._begin, self._generation)
        return True

    def _begin(self, token):
        """Main thread: confirm Explorer has focus, then poll for the result."""
        if not self._active or token != self._generation or not self._enabled():
            return
        focus = api.getFocusObject()
        accepted = _isExplorerFocus(focus)
        log.info(f"ExplorerSelection: Ctrl+A seen, {_describe(focus)}, accepted={accepted}")
        if not accepted:
            return
        core.callLater(_POLL_DELAY_MS, self._pollSelection, token, 0)

    def _pollSelection(self, token, attempt):
        if not self._active or token != self._generation or not self._enabled():
            return
        focus = api.getFocusObject()
        if not _isExplorerFocus(focus):
            return

        count, total = _getCounts(focus)
        log.debug(f"ExplorerSelection: poll attempt={attempt}, count={count}, total={total}")

        # If the count is unknown, or lower than the total, Windows may still be
        # selecting items. Retry briefly (up to 4 times, ~350 ms in total).
        # An empty folder (total == 0) needs no retry.
        if total != 0 and (count is None or (total is not None and count < total)) and attempt < _MAX_RETRIES:
            core.callLater(_RETRY_DELAY_MS, self._pollSelection, token, attempt + 1)
            return

        if total == 0 or (count is None and total is None):
            if total == 0:
                ui.message(_("No items to select."))
            else:
                ui.message(_("The number of selected items could not be determined."))
            return

        # Ctrl+A selects all items; if the count is unknown or zero while items exist, use the total.
        finalCount = count
        if (finalCount is None or finalCount == 0) and total is not None and total > 0:
            finalCount = total

        if finalCount == 0:
            ui.message(_("No items to select."))
        elif finalCount == 1:
            ui.message(_("1 item selected."))
        else:
            ui.message(_("{count} items selected.").format(count=finalCount))

    def terminate(self):
        if not self._active:
            return
        self._active = False
        self._generation += 1
        inputCore.decide_executeGesture.unregister(self._onGesture)
