"""A configurable substitute for the physical Applications key."""
import addonHandler
import core
import inputCore
import keyboardHandler
import ui
import winUser

addonHandler.initTranslation()


def shortcutLabel(identifier):
    if not identifier:
        return _("No shortcut assigned")
    try:
        return inputCore.getDisplayTextForGestureIdentifier(identifier)[1]
    except LookupError:
        return identifier.partition(":")[2]


def capturedIdentifier(gesture):
    """Keep the generic keyboard identifier, independent of NVDA layout."""
    if not isinstance(gesture, keyboardHandler.KeyboardInputGesture) or gesture.isModifier:
        return None
    return next((item for item in reversed(gesture.normalizedIdentifiers) if item.startswith("kb:")), None)


class ApplicationsKey:
    def __init__(self, settings):
        self.settings = settings
        self.active = True
        self._generation = 0

    def matches(self, gesture):
        settings = self.settings()
        return bool(self.active and settings["replaceApplicationsKey"]
                    and settings["applicationsGesture"]
                    and "kb:applications" not in gesture.normalizedIdentifiers
                    and settings["applicationsGesture"] in gesture.normalizedIdentifiers)

    def execute(self, gesture):
        # Sending while Ctrl/Alt/Shift/Windows is still held can open a different
        # menu. Wait asynchronously; never release physical keys artificially.
        self._generation += 1
        token = self._generation
        modifiers = tuple(vk for vk, extended in gesture.modifiers)
        foreground = winUser.getForegroundWindow()
        self._sendWhenReleased(token, modifiers, foreground, 0)

    def _sendWhenReleased(self, token, modifiers, foreground, attempts):
        if not self.active or token != self._generation:
            return
        if not self.settings()["replaceApplicationsKey"] or winUser.getForegroundWindow() != foreground:
            return
        if any(winUser.getKeyState(vk) & 0x8000 for vk in modifiers):
            if attempts >= 100:
                ui.message(_("Release the shortcut keys and try again."))
                return
            core.callLater(20, self._sendWhenReleased, token, modifiers, foreground, attempts + 1)
            return
        # Emulate through NVDA so document/application handlers for the actual
        # Applications key are retained. Keyboard.send handles native fallback.
        inputCore.manager.emulateGesture(keyboardHandler.KeyboardInputGesture.fromName("applications"))

    def terminate(self):
        self.active = False
        self._generation += 1
