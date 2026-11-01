"""Announce selected item count in File Explorer when pressing Ctrl+A."""
import addonHandler
import api
import core
import inputCore
import keyboardHandler
import ui
from controlTypes import Role, State
from logHandler import log

addonHandler.initTranslation()


def _isDesktop(focus):
    """Check if focus is on the Windows Desktop."""
    if focus is None:
        return False
    try:
        root = getattr(focus, "rootNVDAObject", None)
        if root is not None and getattr(root, "windowClassName", "") in ("Progman", "WorkerW"):
            return True
        className = getattr(focus, "windowClassName", "") or ""
        if className in ("Progman", "WorkerW"):
            return True
        parent = getattr(focus, "parent", None)
        while parent is not None:
            if getattr(parent, "windowClassName", "") in ("Progman", "WorkerW"):
                return True
            parent = getattr(parent, "parent", None)
    except Exception:
        pass
    return False


def _isExplorerFocus(focus):
    """Verify that focus is in File Explorer or Desktop, and not in an active text-editing field."""
    if focus is None:
        return False
    try:
        if api.isTypingProtected():
            return False
    except Exception:
        pass
    app = getattr(focus, "appModule", None)
    appName = getattr(app, "appName", "").lower() if app else ""
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
    if role in (Role.LIST, Role.TREEVIEW):
        return focus
    container = getattr(focus, "selectionContainer", None)
    if container is not None and getattr(container, "role", None) in (Role.LIST, Role.TREEVIEW):
        return container
    parent = getattr(focus, "parent", None)
    if parent is not None and getattr(parent, "role", None) in (Role.LIST, Role.TREEVIEW):
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
        import ctypes
        user32 = ctypes.windll.user32
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
        pass
    return 0


def _getDesktopSelection(focus):
    """Query selected items count on the Windows Desktop."""
    # 1. Authoritative Win32 message: LVM_GETSELECTEDCOUNT (0x1032 = 4146)
    # Directly queries the SysListView32 control in Windows, completely bypassing MSAA accSelection limits.
    try:
        import ctypes
        user32 = ctypes.windll.user32
        lvHwnd = _getSysListViewHwnd(focus)
        if lvHwnd:
            c = user32.SendMessageW(lvHwnd, 0x1032, 0, 0)
            if c >= 0:
                log.info(f"DesktopSelection: SendMessage LVM_GETSELECTEDCOUNT={c} on lvHwnd {hex(lvHwnd)}")
                return c
    except Exception as e:
        log.error(f"DesktopSelection: Win32 LVM_GETSELECTEDCOUNT failed: {e}")

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
                    c = len(sel)
                    log.info(f"DesktopSelection: UIASelectionPattern count={c}")
                    return c
        except Exception:
            pass

    # 3. Fallback to NVDA's getSelectedItemsCount / selectedItems
    for obj in candidates:
        try:
            c = obj.getSelectedItemsCount()
            if c is not None and c >= 0:
                log.info(f"DesktopSelection: obj.getSelectedItemsCount()={c}")
                return c
        except Exception:
            pass
        try:
            items = getattr(obj, "selectedItems", None)
            if items is not None:
                log.info(f"DesktopSelection: obj.selectedItems length={len(items)}")
                return len(items)
        except Exception:
            pass

    return None


def _getDesktopTotal(focus):
    """Query total item count on the Windows Desktop."""
    # 1. Authoritative Win32 message: LVM_GETITEMCOUNT (0x1004 = 4100)
    try:
        import ctypes
        user32 = ctypes.windll.user32
        lvHwnd = _getSysListViewHwnd(focus)
        if lvHwnd:
            tot = user32.SendMessageW(lvHwnd, 0x1004, 0, 0)
            if tot >= 0:
                log.info(f"DesktopTotal: SendMessage LVM_GETITEMCOUNT={tot} on lvHwnd {hex(lvHwnd)}")
                return tot
    except Exception:
        pass

    # 2. NVDA positionInfo
    pos = getattr(focus, "positionInfo", None)
    if isinstance(pos, dict):
        total = pos.get("similarItemsInGroup")
        if total is not None and total >= 0:
            return total

    # 3. Container childCount
    container = _findListContainer(focus)
    if container is not None:
        try:
            count = container.childCount
            if count is not None and count >= 0:
                return count
        except Exception:
            pass

    return None


def _getExplorerHwnds(focus):
    """Collect possible HWNDs representing the active File Explorer window."""
    hwnds = set()
    if focus is None:
        return hwnds
    try:
        import ctypes
        user32 = ctypes.windll.user32
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
        pass
    hwnds.discard(0)
    return hwnds


def _getSelectedCount(focus):
    """Query selected items count in File Explorer folder or Desktop."""
    # If on Desktop, NEVER query Shell.Windows() (which only manages CabinetWClass folders)
    if _isDesktop(focus):
        log.info("ExplorerSelection: Detected Desktop, using desktop selection query...")
        return _getDesktopSelection(focus)

    targetHwnds = _getExplorerHwnds(focus)

    # In File Explorer folder windows (CabinetWClass), query Shell COM matching window handle
    try:
        import comtypes.client
        from comtypes import CoInitialize, CoUninitialize

        CoInitialize()
        try:
            shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
            windows = shell.Windows()
            for window in windows:
                try:
                    wHwnd = int(window.HWND)
                    if wHwnd in targetHwnds:
                        doc = getattr(window, "Document", None)
                        if doc is not None:
                            sel = getattr(doc, "SelectedItems", None)
                            if callable(sel):
                                sel = sel()
                            if sel is not None:
                                c = int(sel.Count)
                                log.info(f"ExplorerSelection: Shell COM (matched HWND {hex(wHwnd)}) count={c}")
                                return c
                except Exception:
                    continue
            # If no target HWND matched, check if exactly 1 Explorer window is open
            if windows.Count == 1:
                try:
                    doc = getattr(windows.Item(0), "Document", None)
                    if doc is not None:
                        sel = getattr(doc, "SelectedItems", None)
                        if callable(sel):
                            sel = sel()
                        if sel is not None:
                            c = int(sel.Count)
                            log.info(f"ExplorerSelection: Shell COM (single window) count={c}")
                            return c
                except Exception:
                    pass
        finally:
            CoUninitialize()
    except Exception as e:
        log.error(f"ExplorerSelection: Shell COM query failed: {e}")

    # Fallback to container methods
    return _getDesktopSelection(focus)


def _getTotalCount(focus):
    """Estimate total item count in the group or folder."""
    if _isDesktop(focus):
        return _getDesktopTotal(focus)

    targetHwnds = _getExplorerHwnds(focus)

    # 1. Shell COM for folder windows
    try:
        import comtypes.client
        from comtypes import CoInitialize, CoUninitialize

        CoInitialize()
        try:
            shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
            windows = shell.Windows()
            for window in windows:
                try:
                    wHwnd = int(window.HWND)
                    if wHwnd in targetHwnds:
                        doc = getattr(window, "Document", None)
                        if doc is not None:
                            folder = getattr(doc, "Folder", None)
                            if folder is not None:
                                items = getattr(folder, "Items", None)
                                if callable(items):
                                    items = items()
                                if items is not None:
                                    tot = int(items.Count)
                                    log.info(f"ExplorerTotal: Shell COM (matched HWND {hex(wHwnd)}) count={tot}")
                                    return tot
                except Exception:
                    continue
            if windows.Count == 1:
                try:
                    doc = getattr(windows.Item(0), "Document", None)
                    if doc is not None:
                        folder = getattr(doc, "Folder", None)
                        if folder is not None:
                            items = getattr(folder, "Items", None)
                            if callable(items):
                                items = items()
                            if items is not None:
                                return int(items.Count)
                except Exception:
                    pass
        finally:
            CoUninitialize()
    except Exception:
        pass

    # 2. NVDA positionInfo or childCount
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
            pass
    return None


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
            mods = set(m.lower() for m in getattr(gesture, "modifierNames", ()))
            if mods == {"control"}:
                return True
        return False

    def _onGesture(self, gesture):
        if not self._active or not self._enabled():
            return True
        manager = inputCore.manager
        if manager.isInputHelpActive or getattr(manager, "_captureFunc", None):
            return True
        if not self._isCtrlA(gesture):
            return True
        focus = api.getFocusObject()
        if not _isExplorerFocus(focus):
            return True

        # Do NOT swallow Ctrl+A! Return True so Windows processes Ctrl+A natively!
        self._generation += 1
        token = self._generation
        log.info("ExplorerSelection: Ctrl+A detected, letting Windows process it natively...")
        core.callLater(150, self._pollSelection, token, 0)
        return True

    def _pollSelection(self, token, attempt):
        if not self._active or token != self._generation or not self._enabled():
            return
        focus = api.getFocusObject()
        if not _isExplorerFocus(focus):
            return

        count = _getSelectedCount(focus)
        total = _getTotalCount(focus)
        log.info(f"ExplorerSelection: poll attempt={attempt}, count={count}, total={total}")

        # If count could not be retrieved, or if count < total while total > 0,
        # Windows may still be selecting items. Retry briefly (up to 4 times, ~350ms total).
        if (count is None or (total is not None and total > 0 and count < total)) and attempt < 4:
            core.callLater(60, self._pollSelection, token, attempt + 1)
            return

        # Determine effective selection count
        finalCount = count
        if (finalCount is None or finalCount == 0) and total is not None and total > 0:
            # Ctrl+A selects all items; if count couldn't be queried or reported 0 while total items exist, use total
            finalCount = total

        if finalCount is None or finalCount == 0 or total == 0:
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
