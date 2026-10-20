"""Optional keypad navigation using NVDA's normal key emulation.

No hooks, global gesture-map edits or Num Lock changes are made. Looking up
the option for every gesture also follows configuration profile changes.
"""
import addonHandler
import inputCore
import keyboardHandler
import winUser

addonHandler.initTranslation()

_NAVIGATION_KEYS = {
    "numpad1": "end",
    "numpad2": "downArrow",
    "numpad3": "pageDown",
    "numpad4": "leftArrow",
    "numpad6": "rightArrow",
    "numpad7": "home",
    "numpad8": "upArrow",
    "numpad9": "pageUp",
}
_MODIFIERS = frozenset(("control", "shift", "alt", "windows"))


class NumpadNavigation:
    def __init__(self, enabled):
        self.enabled = enabled
        self.active = True
        self._emulating = False
        self._scripts = {}

    def getScript(self, gesture):
        if not self.active or self._emulating or not self.enabled():
            return None
        if not isinstance(gesture, keyboardHandler.KeyboardInputGesture):
            return None
        name = gesture.mainKeyName.lower()
        # Windows can report a numeric-key identity for Shift+keypad keys.
        # Accept that identity only with Num Lock OFF and Shift present below.
        numericName = name.startswith("numlocknumpad")
        if numericName:
            name = name[len("numlock"):]
        target = _NAVIGATION_KEYS.get(name)
        if target is None:
            return None
        modifiers = tuple(name.lower() for name in gesture.modifierNames)
        # In particular, leave every NVDA+key combination alone.
        if any(name not in _MODIFIERS for name in modifiers):
            return None
        if winUser.getKeyState(winUser.VK_NUMLOCK) & 1:
            return None
        if numericName and "shift" not in modifiers:
            return None
        targetName = "+".join((*sorted(modifiers), target))
        if targetName not in self._scripts:
            def navigate(source, targetName=targetName):
                self._emulating = True
                try:
                    # A queued command may outlive an Apply, profile switch,
                    # Num Lock change or add-on termination. Resolve the
                    # original gesture normally if it is no longer enabled.
                    if not self.active or not self.enabled() or winUser.getKeyState(winUser.VK_NUMLOCK) & 1:
                        inputCore.manager.emulateGesture(source)
                        return
                    emulated = keyboardHandler.KeyboardInputGesture.fromName(targetName)
                    inputCore.manager.emulateGesture(emulated)
                finally:
                    self._emulating = False

            navigate.__name__ = "script_numpadNavigation"
            # Input help describes the effective key without executing it.
            label = keyboardHandler.KeyboardInputGesture.fromName(targetName).displayName
            navigate.__doc__ = _("Emulates pressing {key} on the system keyboard").format(key=label)
            self._scripts[targetName] = navigate
        return self._scripts[targetName]

    def terminate(self):
        self.active = False
        self._scripts.clear()
