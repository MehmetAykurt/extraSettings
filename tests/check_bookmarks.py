"""Persistence, context, lifecycle and actual dialog behaviour checks."""
import builtins
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addon/globalPlugins/extraSettings'
builtins._ = lambda value: value
package = types.ModuleType('bookmarkTestPackage')
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package

def load(name):
    spec = importlib.util.spec_from_file_location('bookmarkTestPackage.' + name, SOURCE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result

storeModule = load('bookmarkStore')
Store, Error = storeModule.BookmarkStore, storeModule.BookmarkError
context = load('bookmarkContext')
messages = []
gui = types.SimpleNamespace(mainFrame=types.SimpleNamespace(prePopup=lambda: None, postPopup=lambda: None))
globalVars = types.SimpleNamespace(appArgs=types.SimpleNamespace(configPath=''))
with patch.dict(sys.modules, addonHandler=types.SimpleNamespace(initTranslation=lambda: None),
                gui=gui, ui=types.SimpleNamespace(message=messages.append), globalVars=globalVars):
    controllerModule = load('bookmarks')
sys.modules['bookmarkTestPackage.bookmarks'] = controllerModule

class Control:
    def __init__(self, *args, value='', choices=None, **kwargs):
        self.value, self.items, self.selection = value, choices or [], -1
        self.enabled = True
        self.visible = True
        self.focused = False
        self.style = kwargs.get('style', 0)
    def Bind(self, *args): pass
    def GetValue(self): return self.value
    def SetValue(self, value): self.value = value
    def SetLabel(self, value): self.label = value
    ChangeValue = SetValue
    def Set(self, items): self.items = list(items); self.selection = -1
    def GetSelection(self): return self.selection
    def SetSelection(self, index): self.selection = index
    def Enable(self, value): self.enabled = bool(value)
    def Show(self, value=True): self.visible = value
    def SetFocus(self): self.focused = True
    def Layout(self): pass
    def Fit(self): pass
    def SetSizerAndFit(self, *args): pass
    def GetSize(self): return (600, 400)
    def SetMinSize(self, *args): pass
    def CentreOnScreen(self): pass
    def CentreOnParent(self): pass
    def SetDefault(self): pass
    def Raise(self): pass
    def EndModal(self, value): self.result = value
    def Destroy(self): pass
    def IsBeingDeleted(self): return False
    def Close(self): self.onClose(None)

class Helper:
    def __init__(self, parent, **kwargs): self.parent = parent
    def addItem(self, item): return item
    def addLabeledControl(self, label, kind, **kwargs): return kind(self.parent, label=label, **kwargs)

callbacks = []
wx = types.SimpleNamespace(Dialog=Control, TextCtrl=Control, Choice=Control, ListBox=Control,
    StaticText=Control, Button=Control, BoxSizer=lambda *args: None,
    CallAfter=lambda func, *args: callbacks.append((func,args)), MessageBox=lambda *args: messages.append(args[0]))
for name in ('DEFAULT_DIALOG_STYLE','RESIZE_BORDER','VERTICAL','LB_SINGLE','LB_HSCROLL','TE_READONLY','ID_CANCEL','ID_OK',
             'EVT_BUTTON','EVT_TEXT','EVT_CHOICE','EVT_LISTBOX','EVT_LISTBOX_DCLICK','EVT_CHAR_HOOK','EVT_CLOSE','EVT_WINDOW_DESTROY',
             'OK','ICON_ERROR','DD_DEFAULT_STYLE','DD_DIR_MUST_EXIST','WXK_RETURN','WXK_NUMPAD_ENTER','WXK_DELETE'):
    setattr(wx, name, len(vars(wx)) + 1)
with patch.dict(sys.modules, addonHandler=types.SimpleNamespace(initTranslation=lambda: None), wx=wx,
                gui=types.SimpleNamespace(guiHelper=types.SimpleNamespace(BoxSizerHelper=Helper, associateElements=lambda *args:None)),
                ui=types.SimpleNamespace(message=messages.append)):
    dialogModule = load('bookmarksDialog')
sys.modules['bookmarkTestPackage.bookmarksDialog'] = dialogModule

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='bookmarks-', dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'extraSettingsBookmarks.json'
        self.store = Store(self.path)
        self.store.load()

    def test_new_store_does_not_write_until_save_and_unicode_survives_restart(self):
        self.assertFalse(self.path.exists())
        identity = self.store.save('Şiirlerim', 'web', 'https://example.org/şiir?q=ö#başlık')
        other = Store(self.path)
        self.assertEqual(other.load()[0]['name'], 'Şiirlerim')
        self.assertEqual(other.entries[0]['id'], identity)

    def test_edit_delete_persist_and_preserve_previous_valid_backup(self):
        identity = self.store.save('Belgeler', 'folder', r'C:\Belgeler')
        before = self.path.read_bytes()
        self.store.save('Belgelerim', 'folder', r'C:\Belgeler\Yeni', identity)
        self.assertEqual(self.path.with_suffix('.json.bak').read_bytes(), before)
        self.assertEqual(len(self.store.entries), 1)
        self.store.remove(identity)
        self.assertEqual(Store(self.path).load(), [])

    def test_duplicate_folder_case_and_web_host_detected_but_url_path_case_preserved(self):
        for kind, target, duplicate in [('folder', r'C:\Test\Bir', 'c:\\test\\bir\\'),
                                        ('web', 'https://EXAMPLE.org', 'https://example.org/')]:
            self.store.save('A', kind, target)
            with self.assertRaisesRegex(Error, 'duplicate'): self.store.save('B', kind, duplicate)
        self.store.save('C', 'web', 'https://example.org/Some')
        self.store.save('D', 'web', 'https://example.org/some')

    def test_rejects_invalid_or_unsupported_targets_without_writing(self):
        for kind, target in [('web','javascript:alert(1)'), ('web','file:///C:/a'), ('web','https://'),
                             ('web','https://name:pass@example.org'), ('web','https://a b.org'),
                             ('web','https://example.org:bad'), ('web','https://x.org\nfoo'),
                             ('folder','C:relative'), ('folder','relative'), ('folder','\\\\.\\C:\\'),
                             ('other','https://example.org')]:
            with self.subTest(kind=kind, target=target), self.assertRaises(Error): self.store.save('A', kind, target)
        with self.assertRaisesRegex(Error, 'name'): self.store.save('  ', 'folder', 'C:\\')
        self.assertFalse(self.path.exists())

    def test_network_share_and_environment_folder_paths(self):
        self.store.save('A', 'folder', r'\\server\share')
        self.store.save('B', 'folder', r'\\server\share\subfolder')
        with patch.dict(storeModule.os.environ, BOOKMARK_TEST='C:\\Test'):
            self.store.save('C', 'folder', r'%BOOKMARK_TEST%\child')
        self.assertEqual(self.store.entries[-1]['target'], r'C:\Test\child')

    def test_damaged_file_is_preserved_and_cannot_be_silently_replaced(self):
        for raw in (b'{bad', b'[]', b'{"version":2,"entries":[]}', b'{"version":1,"entries":[{}]}'):
            self.path.write_bytes(raw)
            with self.assertRaisesRegex(Error,'damaged'): self.store.load()
            with self.assertRaisesRegex(Error,'changed'): self.store.save('A','web','https://example.org')
            self.assertEqual(self.path.read_bytes(), raw)

    def test_external_change_and_failed_atomic_replace_preserve_current_records(self):
        self.store.save('A','web','https://example.org')
        other = Store(self.path); other.load(); other.save('B','folder', r'C:\Other')
        raw = self.path.read_bytes()
        with self.assertRaisesRegex(Error,'changed'): self.store.save('C','folder',r'C:\Third')
        self.store.load()
        with patch.object(storeModule.os, 'replace', side_effect=OSError('failure')):
            with self.assertRaisesRegex(Error,'write'): self.store.save('C','folder',r'C:\Third')
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(len(self.store.entries), 2)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

class ContextTests(unittest.TestCase):
    def test_explorer_context_uses_foreground_window_and_rejects_ambiguous_tabs(self):
        import ctypes
        focus = types.SimpleNamespace(treeInterceptor=None, appModule=types.SimpleNamespace(appName='explorer'))
        api = types.SimpleNamespace(getFocusObject=lambda:focus, isTypingProtected=lambda:False)
        def window(hwnd,path):
            return types.SimpleNamespace(HWND=hwnd,Document=types.SimpleNamespace(Folder=types.SimpleNamespace(Self=types.SimpleNamespace(Path=path))))
        windows = [window(999,r'C:\Other'),window(123,r'C:\Expected')]
        foreground = lambda:123
        initialized = []
        client = types.SimpleNamespace(CreateObject=lambda *args,**kwargs:types.SimpleNamespace(Windows=lambda:windows))
        comtypes = types.SimpleNamespace(client=client, CoInitialize=lambda:initialized.append('init'),
                                        CoUninitialize=lambda:initialized.append('exit'))
        with patch.dict(sys.modules, api=api, comtypes=comtypes, **{'comtypes.client':client}), \
                patch.object(ctypes,'WinDLL',return_value=types.SimpleNamespace(GetForegroundWindow=foreground)):
            self.assertEqual(context.currentLocation()['target'],r'C:\Expected')
            windows.append(window(123,r'C:\DifferentTab'))
            self.assertIsNone(context.currentLocation())
        self.assertEqual(initialized,['init','exit','init','exit'])

    def test_web_root_is_used_instead_of_current_link_or_edit_value(self):
        root = types.SimpleNamespace(name='Sayfam', value='https://example.org/current')
        focus = types.SimpleNamespace(value='https://wrong.org', treeInterceptor=types.SimpleNamespace(rootNVDAObject=root,
                                     documentConstantIdentifier='https://example.org/new'), appModule=None)
        api = types.SimpleNamespace(getFocusObject=lambda: focus, isTypingProtected=lambda: False)
        with patch.dict(sys.modules, api=api):
            result = context.currentLocation()
        self.assertEqual(result['target'], 'https://example.org/new')
        self.assertEqual(result['name'], 'Sayfam')

    def test_missing_or_protected_focus_does_not_capture(self):
        for focus, protected in [(None,False), (object(),True), (types.SimpleNamespace(treeInterceptor=None),False)]:
            api = types.SimpleNamespace(getFocusObject=lambda: focus, isTypingProtected=lambda: protected)
            with patch.dict(sys.modules, api=api): self.assertIsNone(context.currentLocation())

class DialogTests(unittest.TestCase):
    def setUp(self):
        StoreTests.setUp(self)
        messages.clear(); callbacks.clear()
        self.closed = 0
        self.controller = types.SimpleNamespace(viewClosed=lambda: setattr(self,'closed',self.closed+1))

    def dialog(self):
        return dialogModule.BookmarksDialog(None, self.controller, self.store, None)

    def test_search_selection_edit_and_remove_only_record(self):
        self.store.save('İş klasörü','folder',r'C:\İşler')
        self.store.save('NV Access','web','https://www.nvaccess.org/')
        dialog = self.dialog()
        dialog.search.SetValue('iş'); dialog.onSearch(None)
        self.assertEqual(len(dialog.visible),1)
        self.assertEqual(dialog.details.GetValue(),r'C:\İşler')
        with patch.object(dialogModule.os,'unlink',side_effect=AssertionError('Deleted real file')):
            dialog.onRemove(None)
        self.assertEqual(len(Store(self.path).load()),1)
        self.assertIn('Bookmark removed.',messages)
        dialog.search.SetValue(''); dialog.onSearch(None)
        self.assertEqual(dialog.visible[0]['name'],'NV Access')

    def test_editor_invalid_input_stays_open_then_save_persists(self):
        editor = dialogModule.BookmarkEditor(None,self.store)
        editor.onSave(None)
        self.assertFalse(hasattr(editor,'result'))
        editor.name.SetValue('Sayfa'); editor.kind.SetSelection(1); editor.target.SetValue('https://example.org')
        editor.onSave(None)
        self.assertEqual(editor.result,wx.ID_OK)
        self.assertEqual(Store(self.path).load()[0]['name'],'Sayfa')

    def test_enter_opens_selected_url_and_escape_close_notifies_once(self):
        self.store.save('Site','web','https://example.org')
        dialog = self.dialog()
        event = types.SimpleNamespace(GetKeyCode=lambda:wx.WXK_RETURN,ControlDown=lambda:False,AltDown=lambda:False,ShiftDown=lambda:False)
        with patch.object(dialogModule.os,'startfile') as start:
            dialog.onListKey(event)
            start.assert_called_once_with('https://example.org')
        dialog._notifyClosed()
        self.assertEqual(self.closed,1)

    def test_unavailable_folder_and_late_callback_never_launch(self):
        self.store.save('Folder','folder',r'C:\Missing')
        dialog = self.dialog()
        with patch.object(dialogModule.os,'startfile') as start:
            dialog._launch(r'C:\Missing',False)
            self.assertFalse(dialog._closed)
            dialog.onClose(None)
            dialog._launch(r'C:\Missing',True)
            start.assert_not_called()
        self.assertEqual(len(self.store.entries),1)

    def test_controller_disabled_does_not_capture_or_read_and_disable_closes(self):
        enabled = [False]
        controller = controllerModule.Bookmarks(lambda:enabled[0])
        with patch.object(context,'currentLocation',side_effect=AssertionError('Captured while disabled')):
            controller.show()
        self.assertEqual(messages[-1],'Enable bookmarks in Extra Settings first.')
        enabled[0] = True
        globalVars.appArgs.configPath = self.directory.name
        with patch.object(context,'currentLocation',return_value=None):
            controller.show()
        self.assertIsNotNone(controller._dialog)
        enabled[0] = False
        controller.applySettings()
        self.assertIsNone(controller._dialog)
        controller.terminate()
        controller.show()
        self.assertIsNone(controller._dialog)

if __name__ == '__main__': unittest.main(verbosity=2)
