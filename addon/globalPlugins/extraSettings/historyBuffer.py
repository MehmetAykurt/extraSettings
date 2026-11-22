"""Bounded speech history, held only in process memory."""
from collections import deque
from collections.abc import Iterable, Iterator
from threading import RLock
from typing import Any

# One row never exceeds this many characters, which bounds worst-case memory
# (1000 rows) even if a very long text is spoken at once.
MAX_ENTRY_CHARS = 10_000


def parseLimit(value: Any) -> int:
    """Validate a history line count: a whole number from 1 to 1000."""
    text = str(value).strip()
    if not text.isascii() or not text.isdecimal():
        raise ValueError("History line count must be an integer from 1 to 1000")
    count = int(text)
    if not 1 <= count <= 1000:
        raise ValueError("History line count must be from 1 to 1000")
    return count


def _texts(sequence: Iterable[Any]) -> Iterator[str]:
    """Yield every string of a possibly nested speech sequence, in order."""
    for part in sequence:
        if isinstance(part, str):
            yield part
        elif isinstance(part, (list, tuple)):
            yield from _texts(part)


class HistoryBuffer:
    """Ring buffer of spoken text; every entry gets a unique, increasing id."""

    def __init__(self, limit: Any = 100) -> None:
        self._lock = RLock()
        self._entries: deque[tuple[int, str]] = deque(maxlen=parseLimit(limit))
        self._nextId = 0

    def resize(self, limit: Any) -> None:
        """Change the capacity, keeping the newest entries."""
        limit = parseLimit(limit)
        with self._lock:
            if self._entries.maxlen != limit:
                self._entries = deque(self._entries, maxlen=limit)

    def append(self, speechSequence: Iterable[Any]) -> None:
        """Store the text of one spoken sequence as a single row."""
        # One speech call is one row, even if it contains embedded newlines.
        # Keep only text, never synthesizer commands or callback objects.
        text = " ".join(" ".join(_texts(speechSequence)).split())
        if len(text) > MAX_ENTRY_CHARS:
            text = text[:MAX_ENTRY_CHARS - 1] + "…"
        if text:
            with self._lock:
                self._entries.append((self._nextId, text))
                # Ids are never reused, even after eviction or clear().
                self._nextId += 1

    def snapshotItems(self, newestFirst: bool = True) -> list[tuple[int, str]]:
        """Return (id, text) pairs in the requested order."""
        with self._lock:
            result = list(self._entries)
        if newestFirst:
            result.reverse()
        return result

    def snapshot(self, newestFirst: bool = True) -> list[str]:
        """Return the texts in the requested order."""
        return [text for _, text in self.snapshotItems(newestFirst)]

    def clear(self) -> None:
        """Remove every entry."""
        with self._lock:
            self._entries.clear()
