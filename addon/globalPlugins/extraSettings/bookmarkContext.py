"""Read the active location on demand; never use or modify the clipboard."""
from .bookmarkStore import normalize, BookmarkError


def currentLocation():
    import api
    focus = api.getFocusObject()
    if focus is None or api.isTypingProtected():
        return None
    tree = getattr(focus, "treeInterceptor", None)
    root = getattr(tree, "rootNVDAObject", None)
    if root is not None:
        for obj, attribute in ((tree, "documentConstantIdentifier"), (root, "value")):
            try:
                value = normalize("web", getattr(obj, attribute, "") or "")
                return dict(name=getattr(root, "name", "") or value, kind="web", target=value)
            except (BookmarkError, RuntimeError, AttributeError):
                continue
    if getattr(getattr(focus, "appModule", None), "appName", "").lower() != "explorer":
        return None
    # Shell automation supplies the folder path, including localized Explorer
    # windows, without sending keys or changing the address bar.
    import ctypes
    from ctypes import wintypes
    import comtypes.client
    from comtypes import CoInitialize, CoUninitialize
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    foreground = user32.GetForegroundWindow()
    CoInitialize()
    try:
        shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
        candidates = set()
        for window in shell.Windows():
            try:
                if int(window.HWND) == foreground:
                    path = normalize("folder", window.Document.Folder.Self.Path)
                    candidates.add(path)
            except Exception:
                continue
        # Some Explorer versions expose all tabs under one HWND. Do not guess.
        if len(candidates) == 1:
            path = candidates.pop()
            import ntpath
            return dict(name=ntpath.basename(path) or path, kind="folder", target=path)
    finally:
        CoUninitialize()
    return None
