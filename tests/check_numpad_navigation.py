"""Behavior checks using NVDA 2026.2's real keyboard parser methods.

NVDA itself is not importable outside its runtime. Load the relevant methods
from the audited source snapshot, supplying only its Windows/runtime adapters.
No key is sent to the user's desktop by these tests.
"""
import ast
import builtins
import gettext
import importlib.util
import itertools
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon"
SOURCE = Path(__file__).parent / "fixtures/nvda-source"
builtins._ = lambda value: value

def module(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    sys.modules[name] = result
    return result

state = {"enabled": False, "numlock": False}
win = module("winUser", **{
    "VK_LCONTROL": 0xA2, "VK_RCONTROL": 0xA3, "VK_CONTROL": 0x11,
    "VK_LSHIFT": 0xA0, "VK_RSHIFT": 0xA1, "VK_SHIFT": 0x10,
    "VK_LMENU": 0xA4, "VK_RMENU": 0xA5, "VK_MENU": 0x12,
    "VK_LWIN": 0x5B, "VK_RWIN": 0x5C, "VK_NUMLOCK": 0x90,
    "VK_CAPITAL": 0x14, "VK_SCROLL": 0x91,
    "VK_DIVIDE": 0x6F, "VK_MULTIPLY": 0x6A, "VK_SUBTRACT": 0x6D, "VK_ADD": 0x6B,
    "getKeyState": lambda vk: int(state["numlock"]) if vk == 0x90 else 0,
    "VkKeyScanEx": lambda char, hkl: (0, ord(char)),
})
spec = importlib.util.spec_from_file_location("vkCodes", SOURCE / "vkCodes.py")
codes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codes)
sys.modules["vkCodes"] = codes

class AutoProperties:
    def __getattr__(self, name):
        getter = getattr(type(self), "_get_" + name, None)
        if getter:
            return getter(self)
        raise AttributeError(name)

tree = ast.parse((SOURCE / "keyboardHandler.py").read_text(encoding="utf-8-sig"))
keyboardClass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "KeyboardInputGesture")
keepMethods = {"getVkName", "__init__", "_generalizeModifiers", "_get_mainKeyName",
               "_get_modifierNames", "_get_identifiers", "_get__keyNamesInDisplayOrder", "fromName"}
keyboardClass.bases = [ast.Name(id="AutoProperties", ctx=ast.Load())]
keyboardClass.body = [n for n in keyboardClass.body if
    isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NORMAL_MODIFIER_KEYS" for t in n.targets)
    or isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in keepMethods]
ns = dict(AutoProperties=AutoProperties, winUser=win, vkCodes=codes, VK_WIN="windows", VK_NVDA="NVDA",
          config=types.SimpleNamespace(conf={"keyboard": {"keyboardLayout": "desktop"}}),
          getNVDAModifierKeys=lambda: [(0x2D, True)], _ModifierT=tuple[int, bool])
ns["getInputHkl"] = lambda: 0
exec(compile(ast.fix_missing_locations(ast.Module(body=[keyboardClass], type_ignores=[])),
             "NVDA-2026.2-keyboard-parser", "exec"), ns)
Keyboard = ns["KeyboardInputGesture"]
Keyboard.isNVDAModifierKey = property(lambda self: self.mainVkIsModifier if hasattr(self, "mainVkIsModifier") else False)
Keyboard.displayName = property(lambda self: "+".join(self._keyNamesInDisplayOrder))
ns["isNVDAModifierKey"] = lambda vk, ext: vk in (0x14, 0x2D)
module("keyboardHandler", KeyboardInputGesture=Keyboard)
module("addonHandler", initTranslation=lambda: None)
sent = []
manager = types.SimpleNamespace(emulateGesture=lambda gesture: sent.append(gesture))
module("inputCore", manager=manager)
spec = importlib.util.spec_from_file_location("testedNumpadNavigation", ADDON / "globalPlugins/extraSettings/numpadNavigation.py")
navmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(navmod)

NAV = {1: "end", 2: "downArrow", 3: "pageDown", 4: "leftArrow",
       6: "rightArrow", 7: "home", 8: "upArrow", 9: "pageUp"}
NAV_VK = {1: 0x23, 2: 0x28, 3: 0x22, 4: 0x25, 6: 0x27, 7: 0x24, 8: 0x26, 9: 0x21}

def keypad(number, modifiers=(), numeric=False):
    base = Keyboard.fromName("+".join((*modifiers, "numLockNumpad" + str(number) if numeric else "numpad" + str(number))))
    base.scanCode = {1: 0x4F, 2: 0x50, 3: 0x51, 4: 0x4B, 6: 0x4D, 7: 0x47, 8: 0x48, 9: 0x49}.get(number, 0)
    return base

class NavigationTests(unittest.TestCase):
    def setUp(self):
        state.update(enabled=True, numlock=False)
        sent.clear()
        manager.emulateGesture = lambda gesture: sent.append(gesture)
        self.nav = navmod.NumpadNavigation(lambda: state["enabled"])

    def test_all_eight_keys_with_all_modifier_combinations(self):
        mods = ("control", "shift", "alt", "windows")
        for count in range(5):
            for combination in itertools.combinations(mods, count):
                for number in NAV:
                    with self.subTest(number=number, modifiers=combination):
                        original = keypad(number, combination)
                        action = self.nav.getScript(original)
                        self.assertIsNotNone(action)
                        action(original)
                        result = sent.pop()
                        self.assertEqual(result.vkCode, NAV_VK[number])
                        self.assertTrue(result.isExtended)
                        self.assertEqual(set(result.modifierNames), set(combination))

    def test_disabled_and_live_profile_changes(self):
        state["enabled"] = False
        self.assertIsNone(self.nav.getScript(keypad(7)))
        state["enabled"] = True
        self.assertIsNotNone(self.nav.getScript(keypad(7)))
        state["enabled"] = False
        self.assertIsNone(self.nav.getScript(keypad(7)))

    def test_numlock_on_preserves_digits_and_shift_navigation(self):
        state["numlock"] = True
        for number in NAV:
            for numeric in (False, True):
                self.assertIsNone(self.nav.getScript(keypad(number, numeric=numeric)))
                self.assertIsNone(self.nav.getScript(keypad(number, ("shift",), numeric=numeric)))

    def test_numeric_identity_only_with_numlock_off_and_shift(self):
        for number in NAV:
            self.assertIsNone(self.nav.getScript(keypad(number, numeric=True)))
            original = keypad(number, ("shift",), numeric=True)
            self.nav.getScript(original)(original)
            self.assertEqual(sent.pop().modifierNames, ["shift"])

    def test_nvda_combinations_and_non_keypad_keys_are_untouched(self):
        for number in NAV:
            for mods in (("NVDA",), ("NVDA", "control"), ("NVDA", "shift")):
                self.assertIsNone(self.nav.getScript(keypad(number, mods)))
        for name in ("7", "1", "home", "end", "numpadInsert", "numpad5", "numpadDelete",
                     "numpadPlus", "numpadMinus", "numpadMultiply", "numpadDivide", "numpadEnter"):
            self.assertIsNone(self.nav.getScript(Keyboard.fromName(name)))
        self.assertIsNone(self.nav.getScript(object()))

    def test_input_help_lookup_and_repeat_do_not_send_keys(self):
        original = keypad(7)
        action = self.nav.getScript(original)
        self.assertIn("home", action.__doc__)
        self.assertEqual(sent, [])
        self.assertIs(action, self.nav.getScript(keypad(7)))
        for _ in range(10):
            action(original)
        self.assertEqual(len(sent), 10)

    def test_queued_commands_after_disable_numlock_or_termination(self):
        for change in ("disable", "numlock", "terminate"):
            with self.subTest(change=change):
                self.setUp()
                original = keypad(7)
                action = self.nav.getScript(original)
                if change == "disable": state["enabled"] = False
                elif change == "numlock": state["numlock"] = True
                else: self.nav.terminate()
                action(original)
                self.assertIs(sent.pop(), original)
                if change == "terminate":
                    self.assertIsNone(self.nav.getScript(original))

    def test_reentry_and_failure_restore_normal_resolution(self):
        def nested(gesture):
            self.assertIsNone(self.nav.getScript(keypad(7)))
            raise RuntimeError("simulated emulation failure")
        manager.emulateGesture = nested
        original = keypad(7)
        with self.assertRaises(RuntimeError):
            self.nav.getScript(original)(original)
        self.assertFalse(self.nav._emulating)
        self.assertIsNotNone(self.nav.getScript(original))

    def test_existing_applications_substitute_keeps_priority(self):
        source = ast.parse((ADDON / "globalPlugins/extraSettings/__init__.py").read_text(encoding="utf-8-sig"))
        pluginClass = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "GlobalPlugin")
        getScript = next(n for n in pluginClass.body if isinstance(n, ast.FunctionDef) and n.name == "getScript")
        class Base:
            def getScript(self, gesture): return "original"
        pluginNode = ast.ClassDef(name="GlobalPlugin", bases=[ast.Name(id="Base", ctx=ast.Load())],
                                 keywords=[], body=[getScript], decorator_list=[])
        scope = {"Base": Base}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[pluginNode], type_ignores=[])), "plugin-routing", "exec"), scope)
        plugin = scope["GlobalPlugin"]()
        plugin._numpadNavigation = self.nav
        plugin._applicationsKey = types.SimpleNamespace(matches=lambda gesture: True)
        plugin.script_applicationsKey = "applications"
        self.assertEqual(plugin.getScript(keypad(7)), "applications")
        plugin._applicationsKey.matches = lambda gesture: False
        self.assertTrue(callable(plugin.getScript(keypad(7))))
        state["enabled"] = False
        self.assertEqual(plugin.getScript(keypad(7)), "original")

class SettingsRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.callbacks = []
        self.scheduled = []
        callbacks = self.callbacks
        class Action:
            def register(self, handler): callbacks.append(handler)
            def unregister(self, handler): callbacks.remove(handler)
        class Base:
            def __init__(self, *args, **kwargs): pass
            def terminate(self): pass
        class Dialog:
            categoryClasses = []
            _instances = []
            def __init__(self, parent=None, initialCategory=None):
                self.parent = parent
                self.initialCategory = initialCategory
        class Feature:
            isViewing = False
            def __init__(self, *args, **kwargs): pass
            def terminate(self): pass
        self.dialog = Dialog
        self.constructor = Dialog.__init__
        source = ast.parse((ADDON / "globalPlugins/extraSettings/__init__.py").read_text(encoding="utf-8-sig"))
        declarations = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                        and n.name in ("_movePanelToEnd", "GlobalPlugin")]
        self.panel = type("ExtraSettingsPanel", (), {})
        self.other = type("OtherPanel", (), {})
        scope = dict(globalPluginHandler=types.SimpleNamespace(GlobalPlugin=Base),
                     globalVars=types.SimpleNamespace(appArgs=types.SimpleNamespace(configPath='test-config')),
                     prepareDataDirectory=lambda path: None, BookmarkError=RuntimeError,
                     NVDASettingsDialog=Dialog, ExtraSettingsPanel=self.panel,
                     config=types.SimpleNamespace(conf=types.SimpleNamespace(spec={})),
                     CONFIG_SECTION="extraSettings", CONFIG_SPEC={},
                     SpeechHistory=Feature, WebCycle=Feature, SkipBlankLines=Feature, TextStatistics=Feature, ClipboardInspector=Feature, Bookmarks=Feature, QuickFind=Feature, ApplicationsKey=Feature,
                     ClipboardAnnouncements=Feature, NumpadNavigation=Feature, LineTranslation=Feature, AutomaticTranslation=Feature,
                     core=types.SimpleNamespace(postNvdaStartup=Action(),
                         callLater=lambda delay, handler: self.scheduled.append(handler)),
                     script=lambda **kwargs: lambda func: func, _=lambda value: value)
        exec(compile(ast.fix_missing_locations(ast.Module(body=declarations, type_ignores=[])),
                     "extraSettings-registration", "exec"), scope)
        self.pluginClass = scope["GlobalPlugin"]
        self.scope = scope

    def test_storage_failure_does_not_block_plugin_or_settings_registration(self):
        errors = []
        def fail(path):
            raise RuntimeError('write')
        self.scope['prepareDataDirectory'] = fail
        self.scope['log'] = types.SimpleNamespace(error=errors.append)
        plugin = self.pluginClass()
        self.assertEqual(len(errors), 1)
        self.assertIs(self.dialog.__init__, self.constructor)
        self.callbacks[0]()
        self.assertIn(self.panel, self.dialog.categoryClasses)
        plugin.terminate()

    def test_dialog_constructor_is_not_changed_and_startup_orders_category(self):
        plugin = self.pluginClass()
        self.assertIs(self.dialog.__init__, self.constructor)
        self.dialog.categoryClasses.append(self.other)
        self.callbacks[0]()
        self.assertEqual(self.dialog.categoryClasses, [self.other, self.panel])
        opened = self.dialog("parent", self.other)
        self.assertEqual(opened.parent, "parent")
        self.assertIs(opened.initialCategory, self.other)
        plugin.terminate()
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.dialog.categoryClasses, [self.other])
        self.scheduled[0]()
        self.assertEqual(self.dialog.categoryClasses, [self.other])
        self.assertIs(self.dialog.__init__, self.constructor)

    def test_startup_or_reload_does_not_reorder_an_open_dialog(self):
        plugin = self.pluginClass()
        self.dialog.categoryClasses.append(self.other)
        self.dialog._instances.append(self.dialog())
        before = self.dialog.categoryClasses.copy()
        self.callbacks[0]()
        self.scheduled[0]()
        self.assertEqual(self.dialog.categoryClasses, before)
        plugin.terminate()

if __name__ == "__main__":
    unittest.main(verbosity=2)
