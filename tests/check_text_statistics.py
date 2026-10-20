"""Count real selection strings and exercise background/UI lifecycle boundaries."""
import ast
import queue
import threading
import time
import unittest
from unittest.mock import patch

import check_existing_features as existing
import check_translation as base

stats = base.load('translationTestPackage.textStatistics', 'textStatistics.py')


class CountTests(unittest.TestCase):
    def test_plain_turkish_and_four_results(self):
        text='Merhaba dünya!\r\n\r\nBugün hava güzel.\r\n \t\u00a0\r\nSon satır'
        counts=stats.countText(text)
        self.assertEqual((counts.sentences,counts.words,counts.blankLines),(3,7,2))
        self.assertEqual(counts.characters,len(text.replace('\r','').replace('\n','')))
        self.assertEqual(stats.formatCounts(counts).splitlines(),[
            'Sentence count: 3','Word count: 7',f'Character count: {counts.characters}','Blank line count: 2'])

    def test_newline_variants_and_wrapping_do_not_add_blank_lines(self):
        for separator in ('\n','\r\n','\r','\u2028'):
            counts=stats.countText(f'Bir satır{separator}{separator}İki satır{separator}')
            self.assertEqual((counts.words,counts.characters,counts.blankLines),(4,18,1))
        self.assertEqual(stats.countText('Uzun bir dolu satır '*500).blankLines,0)
        self.assertEqual(stats.countText('metin\n').blankLines,0)
        self.assertEqual(stats.countText('\n').blankLines,1)

    def test_empty_whitespace_punctuation_and_repeated_endings(self):
        for text in ('','   ','\t\u00a0','!!! ... ???'):
            self.assertEqual(stats.countText(text).sentences,0)
            self.assertEqual(stats.countText(text).words,0)
        self.assertEqual(stats.countText('Bir!!! İki?! Üç… Dört... Beş').sentences,5)
        self.assertEqual(stats.countText('Aynı cümle\nalt satırda sürüyor.').sentences,1)

    def test_abbreviations_decimal_numbers_initials_and_links(self):
        cases={
            'Dr. Ahmet geldi. Nasılsın?':2,
            'Prof. Dr. Ayşe 3.14 değerini yazdı.':1,
            'A. Yılmaz geldi. Sonra ayrıldı.':2,
            'T.C. kimlik bilgisi. Son cümle.':2,
            'Sayfa https://example.com/test?a=1&b=2. Son cümle!':2,
            'Adres ad.soyad@example.com. Bir ileti gönder.':2,
            'Son kısaltma vb.':1,
        }
        for text,expected in cases.items():
            with self.subTest(text=text): self.assertEqual(stats.countText(text).sentences,expected)

    def test_apostrophes_hyphens_numbers_and_turkish_letters(self):
        self.assertEqual(stats.countText("Ankara’da e-posta 3,14 12.345,67 Türkçe İıŞşĞğ").words,6)
        self.assertEqual(stats.countText("Ankara'nın").words,1)
        self.assertEqual(stats.countText('bir,iki;üç').words,3)

    def test_large_unbroken_word_is_not_quadratic(self):
        started=time.monotonic()
        result=stats.countText('a'*400000)
        self.assertEqual((result.sentences,result.words,result.characters),(1,1,400000))
        self.assertLess(time.monotonic()-started,4)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.enabled=True;self.windows=[];self.messages=[];self.callbacks=queue.Queue()
        self.main=threading.get_ident()
        self.controller=stats.TextStatistics(lambda:self.enabled)
        self.patches=[
            patch.object(stats,'selectedText',return_value='Bir cümle.\n\nİki cümle!'),
            patch.object(stats.api,'isTypingProtected',return_value=False),
            patch.object(stats.wx,'CallAfter',side_effect=lambda f,*args:self.callbacks.put((f,args))),
            patch.object(stats.ui,'message',side_effect=self.messages.append),
            patch.object(stats.ui,'browseableMessage',create=True,side_effect=self.window),
        ]
        for p in self.patches:p.start()

    def tearDown(self):
        self.controller.terminate()
        for p in reversed(self.patches):p.stop()

    def window(self,*args,**kwargs):
        self.assertEqual(threading.get_ident(),self.main)
        self.windows.append((args,kwargs))

    def pump(self):
        f,args=self.callbacks.get(timeout=3);f(*args)

    def test_background_counting_and_main_thread_result_window(self):
        real=stats.countText; workers=[]
        def count(text):
            workers.append(threading.get_ident());return real(text)
        with patch.object(stats,'countText',side_effect=count):
            self.controller.show();self.pump()
        self.assertNotEqual(workers,[self.main])
        self.assertEqual(len(self.windows),1)
        self.assertTrue(self.windows[0][1]['closeButton'])
        self.assertIn('Blank line count: 1',self.windows[0][0][0])

    def test_disabled_and_missing_selection_never_launch_count(self):
        with patch.object(stats,'countText',side_effect=AssertionError('Unexpected counting')):
            self.enabled=False;self.controller.show()
            stats.selectedText.assert_not_called()
            self.assertIn('Enable word and character counts',self.messages[-1])
            self.enabled=True
            stats.selectedText.return_value='';self.controller.show()
            self.assertEqual(self.messages[-1],'Select some text first.')
            stats.selectedText.return_value=None;self.controller.show()
            self.assertEqual(self.messages[-1],'The selected text could not be read.')
        self.assertFalse(self.windows)

    def test_repeated_gesture_does_not_queue_or_read_another_selection(self):
        self.controller.show();self.controller.show()
        self.assertEqual(stats.selectedText.call_count,1)
        self.assertEqual(self.messages[-1],'Text counting is already in progress.')
        self.pump();self.assertEqual(len(self.windows),1)

    def test_disable_reenable_termination_and_protected_focus_discard_results(self):
        for mode in ('disabled','reenabled','terminated','protected'):
            self.enabled=True;self.controller=stats.TextStatistics(lambda:self.enabled)
            stats.api.isTypingProtected.return_value=False
            self.controller.show()
            if mode in ('disabled','reenabled'):
                self.enabled=False;self.controller.applySettings()
                if mode=='reenabled':self.enabled=True
            elif mode=='terminated':self.controller.terminate()
            else:stats.api.isTypingProtected.return_value=True
            self.pump();self.assertFalse(self.windows)

    def test_counting_failure_is_reported_without_selection_content(self):
        with patch.object(stats,'countText',side_effect=ValueError('sensitive selection')):
            self.controller.show();self.pump()
        self.assertEqual(self.messages,['The selected text could not be counted.'])
        self.assertFalse(self.windows)


class SettingsTests(base.PanelTests):
    def test_statistics_default_apply_and_shortcut_refresh(self):
        panel=self.panel
        self.assertFalse(panel.textStatistics.GetValue())
        self.assertIn('NVDA+shift+K',panel.textStatistics.label)
        panel.textStatistics.SetValue(True);panel.onSave()
        self.assertTrue(self.conf['textStatistics'])
        self.statisticsGestures[:]=['kb:NVDA+control+j']
        panel.onPanelActivated()
        self.assertIn('NVDA+control+J',panel.textStatistics.label)
        self.statisticsGestures.clear();panel.onPanelActivated()
        self.assertIn('No shortcut assigned',panel.textStatistics.label)


if __name__=='__main__':unittest.main(verbosity=2)
