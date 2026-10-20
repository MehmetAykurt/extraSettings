"""On-demand statistics of an accessible selection; never copy or edit text.

NVDA selection access and the results window stay on the main thread. Only a
plain string is passed to a single, short-lived daemon worker. No monitoring,
network requests, persistent selection cache or third-party tokenizer is used.
"""
from dataclasses import dataclass
import re
import threading

import addonHandler
import api
import ui
import wx

from .appendClipboard import selectedText

addonHandler.initTranslation()

_WORDS = re.compile(r"\d+(?:[.,]\d+)+|[^\W_]+(?:[’'\-‐‑][^\W_]+)*", re.UNICODE)
_ENDS = re.compile(r'[.!?…]+')
_LAST_WORD = re.compile(r'([^\W\d_]+(?:\.[^\W\d_]+)*)$', re.UNICODE)
_LINKS = re.compile(
    r'(?:https?://|www\.)[^\s<>]+|(?<![\w.+\-])[\w.+\-]{1,128}@[\w.\-]{1,255}\.[^\W\d_]{2,24}',
    re.IGNORECASE,
)
_ABBREVIATIONS = frozenset((
    'dr', 'prof', 'doç', 'yrd', 'sn', 'say', 'av', 'uzm', 'bkz', 'örn',
    'ör', 'vb', 'vs', 'vd', 'yy', 'no', 'mr', 'mrs', 'ms', 'jr', 'sr',
    'e.g', 'i.e', 'etc',
))
_LINE_ENDS = frozenset('\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029')


@dataclass(frozen=True)
class Counts:
    sentences: int
    words: int
    characters: int
    blankLines: int


def sentenceCount(text):
    """Punctuation-based estimate, with decimal/link/abbreviation exceptions.

    An unfinished final fragment containing letters or numbers is one sentence.
    Newlines alone are not sentence boundaries. This is not a grammar parser.
    """
    links = iter(_LINKS.finditer(text))
    link = next(links, None)
    total = 0
    start = 0
    for end in _ENDS.finditer(text):
        pos = end.start()
        while link is not None and link.end() <= pos:
            link = next(links, None)
        if link is not None and link.start() <= pos:
            # A final full stop after an address is punctuation, not its URL.
            contentEnd = link.start() + len(link.group().rstrip('.!?…,;:)]}»”\"\''))
            if pos < contentEnd:
                continue
        if end.group() == '.':
            if pos > 0 and end.end() < len(text) and text[pos-1].isdigit() and text[end.end()].isdigit():
                continue
            previous = _LAST_WORD.search(text[max(0, pos-40):pos])
            if previous is not None:
                word = previous.group(1)
                # The look-ahead stops at the next non-space character, so
                # abbreviations at the end of a selection still count once.
                nextPos = end.end()
                while nextPos < len(text) and text[nextPos].isspace():
                    nextPos += 1
                if nextPos < len(text) and text[nextPos].isalnum():
                    if word.casefold() in _ABBREVIATIONS:
                        continue
                    if len(word) == 1 and word.isalpha():
                        continue
                    if '.' in word and all(len(part) == 1 for part in word.split('.')):
                        continue
        if any(char.isalnum() for char in text[start:pos]):
            total += 1
        start = end.end()
    if any(char.isalnum() for char in text[start:]):
        total += 1
    return total


def countText(text):
    return Counts(
        sentences=sentenceCount(text),
        words=sum(1 for _match in _WORDS.finditer(text)),
        # Include spaces and tabs; ignore line separators. CRLF and LF
        # selections therefore have identical character counts.
        characters=sum(char not in _LINE_ENDS for char in text),
        # splitlines does not invent an empty line after a terminal newline.
        blankLines=sum(not line.strip() for line in text.splitlines()),
    )


def formatCounts(counts):
    return '\n'.join((
        _("Sentence count: {count}").format(count=counts.sentences),
        _("Word count: {count}").format(count=counts.words),
        _("Character count: {count}").format(count=counts.characters),
        _("Blank line count: {count}").format(count=counts.blankLines),
    ))


class TextStatistics:
    def __init__(self, enabled):
        self._enabled = enabled
        self._active = True
        self._generation = 0
        self._pending = None

    def show(self):
        if not self._active:
            return
        if not self._enabled():
            ui.message(_("Enable word and character counts in Extra Settings first."))
            return
        if self._pending is not None:
            ui.message(_("Text counting is already in progress."))
            return
        text = selectedText()
        if text is None:
            ui.message(_("The selected text could not be read."))
            return
        if not text:
            ui.message(_("Select some text first."))
            return
        token = object()
        self._pending = token
        generation = self._generation
        worker = threading.Thread(target=self._count, args=(text, token, generation),
                                  name='extraSettings-textStatistics', daemon=True)
        try:
            worker.start()
        except Exception:
            self._pending = None
            ui.message(_("The selected text could not be counted."))

    def _count(self, text, token, generation):
        try:
            result = countText(text)
        except Exception:
            result = None
        wx.CallAfter(self._done, token, generation, result)

    def _done(self, token, generation, result):
        if self._pending is not token:
            return
        self._pending = None
        if not self._active or generation != self._generation or not self._enabled():
            return
        if api.isTypingProtected():
            return
        if result is None:
            ui.message(_("The selected text could not be counted."))
            return
        ui.browseableMessage(formatCounts(result), title=_("Announce word and character counts"), closeButton=True)

    def applySettings(self):
        if not self._enabled():
            self._generation += 1

    def terminate(self):
        self._active = False
        self._generation += 1
        self._pending = None
