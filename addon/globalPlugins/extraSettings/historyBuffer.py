"""Bounded speech history, held only in process memory."""
from collections import deque
from threading import RLock


def parseLimit(value):
    text = str(value).strip()
    if not text.isascii() or not text.isdecimal():
        raise ValueError("History line count must be an integer from 1 to 1000")
    count = int(text)
    if not 1 <= count <= 1000:
        raise ValueError("History line count must be from 1 to 1000")
    return count


class HistoryBuffer:
    def __init__(self, limit=100):
        self._lock = RLock()
        self._entries = deque(maxlen=parseLimit(limit))

    def resize(self, limit):
        limit = parseLimit(limit)
        with self._lock:
            if self._entries.maxlen != limit:
                self._entries = deque(self._entries, maxlen=limit)

    def append(self, speechSequence):
        # One speech call is one row, even if it contains embedded newlines.
        # Keep only text, never synthesizer commands or callback objects.
        text = " ".join(" ".join(part for part in speechSequence if isinstance(part, str)).split())
        if text:
            with self._lock:
                self._entries.append(text)

    def snapshot(self, newestFirst=True):
        with self._lock:
            result = list(self._entries)
        if newestFirst:
            result.reverse()
        return result

    def clear(self):
        with self._lock:
            self._entries.clear()
