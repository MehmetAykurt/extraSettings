"""Real native signatures, inert HTML, file metadata and background/UI lifecycle."""
import ctypes
from pathlib import Path
import queue
import threading
import unittest
import uuid
from unittest.mock import patch

import check_translation as base

data=base.load('translationTestPackage.clipboardData','clipboardData.py')
inspector=base.load('translationTestPackage.clipboardInspector','clipboardInspector.py')

def htmlData(fragment):
    before='<html><body>UNCOPIED BEFORE<!--StartFragment-->'.encode()
    after=b'<!--EndFragment-->UNCOPIED AFTER</body></html>'
    body=fragment.encode('utf-8')
    pattern='Version:1.0\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\nStartFragment:{:010d}\r\nEndFragment:{:010d}\r\n'
    offset=len(pattern.format(0,0,0,0).encode())
    header=pattern.format(offset,offset+len(before+body+after),offset+len(before),offset+len(before+body)).encode()
    return header+before+body+after+b'\0'


class PreviewTests(unittest.TestCase):
    def test_text_is_preserved_including_tabs_and_literal_html(self):
        text='<table>plain text</table>\r\n\tİkinci satır & < >'
        result=data.makePreview(data.Snapshot(text=text,hasData=True))
        self.assertEqual(result.content,text);self.assertFalse(result.isHtml)

    def test_utf8_byte_offsets_only_show_copied_fragment(self):
        fragment='<table><tr><th>Adı</th><th>Sınıfı</th></tr><tr><td>Örnek</td><td>6/A</td></tr></table>'
        raw=htmlData(fragment)
        self.assertEqual(data.htmlFragment(raw),fragment)
        result=data.makePreview(data.Snapshot(text='fallback',html=raw,hasData=True))
        self.assertTrue(result.isHtml);self.assertNotIn('UNCOPIED',result.content)
        self.assertIn('<th>Adı</th>',result.content)

    def test_partial_table_rows_and_cells_are_wrapped(self):
        for fragment in ('<tr><td>A</td><td>B</td></tr>','<td>A</td><td>B</td>'):
            result=data.makePreview(data.Snapshot(html=htmlData(fragment),hasData=True))
            self.assertTrue(result.isHtml);self.assertIn('<table>',result.content)
            self.assertIn('<tr>',result.content);self.assertIn('</tr></table>',result.content)

    def test_scripts_external_resources_attributes_and_hidden_context_are_removed(self):
        fragment='''<style>SECRET_STYLE</style><script>SECRET_SCRIPT</script><iframe>SECRET_FRAME</iframe><table style="background:url(https://example.com)"><tr><td onclick="alert(1)" rowspan="2"><a href="javascript:alert(1)">Visible &amp; safe</a><img src="https://example.com/a"></td></tr></table>'''
        result=data.makePreview(data.Snapshot(html=htmlData(fragment),hasData=True))
        self.assertTrue(result.isHtml)
        self.assertIn('Visible &amp; safe',result.content);self.assertIn('rowspan="2"',result.content)
        for forbidden in ('SECRET','script','onclick','style=','iframe','href','src=','http','UNCOPIED'):
            self.assertNotIn(forbidden,result.content)

    def test_malformed_html_falls_back_to_plain_text(self):
        for raw in (b'StartFragment:999\nEndFragment:1000\n<table>bad</table>',b'bad data',b'<!--StartFragment-->\xff<!--EndFragment-->'):
            result=data.makePreview(data.Snapshot(text='Fallback text',html=raw,hasData=True))
            self.assertEqual(result.content,'Fallback text');self.assertFalse(result.isHtml)

    def test_html_only_plain_text_and_fragment_marker_fallback(self):
        raw=b'<html><!--StartFragment--><p>Only <b>text</b></p><!--EndFragment--></html>'
        result=data.makePreview(data.Snapshot(html=raw,hasData=True))
        self.assertFalse(result.isHtml);self.assertEqual(result.content,'Only text')

    def test_empty_and_unsupported_formats_are_distinguished(self):
        self.assertEqual(data.makePreview(data.Snapshot()).content,'The clipboard is empty.')
        self.assertIn('does not contain supported',data.makePreview(data.Snapshot(hasData=True)).content)
        self.assertEqual(data.makePreview(data.Snapshot(text='',hasData=True)).content,'The clipboard is empty.')

    def test_file_metadata_initializes_and_balances_com(self):
        for result,shouldUninitialize in ((0,True),(1,True),(ctypes.c_long(0x80010106).value,False)):
            with patch.object(data,'_coInitialize',return_value=result),patch.object(data,'_coUninitialize') as uninitialize, \
                 patch.object(data,'_filesPreview',return_value=data.Preview('files')):
                self.assertEqual(data.filesPreview(('C:\\test.txt',)).content,'files')
                self.assertEqual(uninitialize.called,shouldUninitialize)

    def test_real_files_folder_missing_file_and_priority_over_text(self):
        root=base.ROOT/('clipboard-fixtures-'+uuid.uuid4().hex)
        root.mkdir()
        file=root/'Örnek.txt';file.write_bytes(b'12345')
        folder=root/'Klasor';folder.mkdir()
        missing=root/'Moved.txt'
        paths=tuple(str(p.absolute()) for p in (file,missing,folder))
        result=data.makePreview(data.Snapshot(text='must not replace files',files=paths,hasData=True))
        self.assertFalse(result.isHtml);self.assertIn('Files on clipboard: 3',result.content)
        self.assertIn('Size: 5 bytes',result.content)
        self.assertIn('Type: Folder',result.content)
        self.assertIn('Not calculated for folders.',result.content)
        self.assertIn('It may have been moved or deleted.',result.content)
        for path in paths:self.assertIn(path,result.content)
        self.assertEqual(file.read_bytes(),b'12345')


class NativeReaderTests(unittest.TestCase):
    def test_unicode_clipboard_is_closed_and_global_memory_unlocked(self):
        buffer=ctypes.create_string_buffer(('Türkçe\r\nMetin\0').encode('utf-16-le'))
        with patch.object(data,'_open',return_value=True),patch.object(data,'_close') as close, \
             patch.object(data,'_available',side_effect=lambda fmt:fmt==13),patch.object(data,'_data',return_value=123), \
             patch.object(data,'_size',return_value=len(buffer.raw)-1),patch.object(data,'_lock',return_value=ctypes.addressof(buffer)), \
             patch.object(data,'_unlock') as unlock,patch.object(data,'_register',return_value=500),patch.object(data,'_count',return_value=1):
            result=data.readSnapshot()
        self.assertEqual(result.text,'Türkçe\r\nMetin')
        close.assert_called_once();unlock.assert_called_once_with(123)

    def test_file_paths_are_queried_as_unicode_and_take_priority(self):
        paths=('C:\\Örnek.txt','C:\\Belgeler\\İkinci.pdf')
        def query(handle,index,buffer,size):
            if index==0xffffffff:return len(paths)
            if buffer is not None:buffer.value=paths[index]
            return len(paths[index])
        with patch.object(data,'_open',return_value=True),patch.object(data,'_close') as close, \
             patch.object(data,'_available',return_value=True),patch.object(data,'_data',return_value=0x123456789), \
             patch.object(data,'_queryFile',side_effect=query),patch.object(data,'_readBytes') as read:
            result=data.readSnapshot()
        self.assertEqual(result.files,paths);read.assert_not_called();close.assert_called_once()
        self.assertEqual(data._data.restype,ctypes.wintypes.HANDLE)

    def test_locked_clipboard_does_not_call_close_or_read(self):
        with patch.object(data,'_open',return_value=False),patch.object(data,'_close') as close:
            with self.assertRaises(OSError):data.readSnapshot()
        close.assert_not_called()

    def test_corrupt_or_oversized_data_releases_clipboard(self):
        with patch.object(data,'_open',return_value=True),patch.object(data,'_close') as close, \
             patch.object(data,'_available',side_effect=lambda fmt:fmt==13),patch.object(data,'_data',return_value=1), \
             patch.object(data,'_size',return_value=data.MAX_BYTES+1),patch.object(data,'_lock') as lock, \
             patch.object(data,'_register',return_value=500):
            with self.assertRaises(data.ClipboardTooLarge):data.readSnapshot()
        close.assert_called_once();lock.assert_not_called()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.enabled=True;self.windows=[];self.messages=[];self.callbacks=queue.Queue()
        self.main=threading.get_ident();self.readerThreads=[]
        self.controller=inspector.ClipboardInspector(lambda:self.enabled)
        def read():
            self.readerThreads.append(threading.get_ident())
            return data.Snapshot(text='Test',hasData=True)
        self.patches=[patch.object(inspector,'readSnapshot',side_effect=read),
            patch.object(inspector.api,'isTypingProtected',return_value=False),
            patch.object(inspector.wx,'CallAfter',side_effect=lambda f,*args:self.callbacks.put((f,args))),
            patch.object(inspector.ui,'message',side_effect=self.messages.append),
            patch.object(inspector.ui,'browseableMessage',create=True,side_effect=self.window)]
        for p in self.patches:p.start()
    def tearDown(self):
        self.controller.terminate()
        for p in reversed(self.patches):p.stop()
    def window(self,*args,**kwargs):
        self.assertEqual(threading.get_ident(),self.main)
        self.windows.append((args,kwargs))
    def pump(self):
        f,args=self.callbacks.get(timeout=3);f(*args)
    def test_reads_off_main_thread_presents_on_main_thread(self):
        self.controller.show();self.pump()
        self.assertNotEqual(self.readerThreads,[self.main])
        self.assertEqual(self.windows[0][0],('Test',))
        self.assertTrue(self.windows[0][1]['closeButton']);self.assertFalse(self.windows[0][1]['isHtml'])
    def test_disabled_never_reads_and_repeated_presses_do_not_queue(self):
        self.enabled=False;self.controller.show()
        inspector.readSnapshot.assert_not_called()
        self.assertIn('Enable clipboard inspection',self.messages[-1])
        self.enabled=True;self.controller.show();self.controller.show();self.pump()
        self.assertEqual(inspector.readSnapshot.call_count,1)
        self.assertIn('already in progress',self.messages[-1])
    def test_disable_reenable_and_termination_discard_pending_result(self):
        for terminate in (False,True):
            self.controller=inspector.ClipboardInspector(lambda:self.enabled)
            self.controller.show()
            if terminate:self.controller.terminate()
            else:
                self.enabled=False;self.controller.applySettings();self.enabled=True
            self.pump();self.assertFalse(self.windows)
    def test_reader_failure_and_size_limit_are_announced(self):
        for error,message in ((OSError('locked'),'The clipboard could not be read. Please try again.'),
                              (data.ClipboardTooLarge(),'The clipboard content is too large to inspect.')):
            inspector.readSnapshot.side_effect=error
            self.controller.show();self.pump();self.assertEqual(self.messages[-1],message)
        self.assertFalse(self.windows)
    def test_table_preview_uses_html_window(self):
        inspector.readSnapshot.side_effect=lambda:data.Snapshot(html=htmlData('<table><tr><td>Cell</td></tr></table>'),hasData=True)
        self.controller.show();self.pump()
        self.assertTrue(self.windows[0][1]['isHtml']);self.assertIn('<td>Cell</td>',self.windows[0][0][0])


class SettingsTests(base.PanelTests):
    def test_clipboard_default_apply_and_remapping(self):
        panel=self.panel
        self.assertFalse(panel.inspectClipboard.GetValue())
        self.assertIn('NVDA+shift+C',panel.inspectClipboard.label)
        panel.inspectClipboard.SetValue(True);panel.onSave()
        self.assertTrue(self.conf['inspectClipboard'])
        self.clipboardGestures[:]=['kb:NVDA+alt+x'];panel.onPanelActivated()
        self.assertIn('NVDA+alt+X',panel.inspectClipboard.label)
        self.clipboardGestures.clear();panel.onPanelActivated()
        self.assertIn('No shortcut assigned',panel.inspectClipboard.label)


if __name__=='__main__':unittest.main(verbosity=2)
